"""Version-pinned, local-only Matrix room guard for the Foldfence demo.

Keycloak membership is read from a short-lived policy-sync snapshot. The signed
immutable create event records the two people in a monitored direct chat. The
Matrix is_direct flag is deliberately not an authorization input.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from synapse.module_api import NOT_SPAM, errors
from synapse.http.server import DirectServeJsonResource

MARKER = "org.foldfence.direct_chat"
ALGORITHM = "m.megolm.v1.aes-sha2"
CALL_MEMBER_EVENT = "org.matrix.msc3401.call.member"
CALL_MEMBER_POWER_LEVEL = 10
MAX_CALL_REQUEST_BYTES = 16 * 1024
MAX_UPSTREAM_RESPONSE_BYTES = 64 * 1024
CALL_UPSTREAM_TIMEOUT_SECONDS = 5
OPENID_USERINFO_URL = "http://synapse:8008/_matrix/federation/v1/openid/userinfo"
LIVEKIT_SFU_GET_URL = "http://lk-jwt-service:8080/sfu/get"
SYNAPSE_MATRIX_GET_TOKEN_URL = "http://synapse:8008/_matrix/client/unstable/io.element.msc4195/rtc/livekit/get_token"
logger = logging.getLogger(__name__)


class AllowedContactsResource(DirectServeJsonResource):
    """Return only the authenticated person's currently permitted DM contacts."""

    isLeaf = True

    def __init__(self, guard):
        super().__init__()
        self.guard = guard

    async def _async_render_GET(self, request):
        request.setHeader(b"Cache-Control", b"no-store")
        requester = await self.guard.api.get_user_by_req(request)
        user_id = requester.user.to_string()
        policy = self.guard.snapshot.load()
        if not self.guard.local_human(user_id):
            raise errors.SynapseError(403, "A local human identity is required", errors.Codes.FORBIDDEN)
        if not policy or user_id not in {user["user_id"] for user in policy["users"]}:
            raise errors.SynapseError(503, "A fresh contact policy is unavailable", "M_UNAVAILABLE")
        return 200, {
            "schema_version": 1,
            "user_id": user_id,
            "expires_at": policy["expires_at"],
            "allowed_user_ids": sorted(
                user["user_id"] for user in policy["users"]
                if self.guard.local_human(user["user_id"])
                and self.guard.snapshot.shared_group(policy, user_id, user["user_id"])
            ),
        }


class CallTokenResource(DirectServeJsonResource):
    """Authorize the LiveKit token request against current Matrix policy/state."""

    isLeaf = True

    def __init__(self, guard, client_server_api=False):
        super().__init__()
        self.guard = guard
        self.client_server_api = client_server_api

    @staticmethod
    def _deny(status=403):
        if status == 401:
            raise errors.SynapseError(401, "A valid Matrix OpenID proof is required", errors.Codes.UNAUTHORIZED)
        raise errors.SynapseError(403, "Room call access is not authorized", errors.Codes.FORBIDDEN)

    def _request_json(self, method, uri, body=None, authorization=None):
        """Issue a fixed-destination request without body logging or redirects."""
        import treq
        from synapse.util.async_helpers import timeout_deferred
        from twisted.web.http_headers import Headers

        client = self.guard.api.http_client
        headers = Headers({
            b"Accept": [b"application/json"],
            b"User-Agent": [client.user_agent],
        })
        if body is not None:
            headers.addRawHeader(b"Content-Type", b"application/json")
        if authorization is not None:
            headers.addRawHeader(
                b"Authorization",
                authorization.encode("ascii") if isinstance(authorization, str) else authorization,
            )
        deferred = treq.request(
            method,
            uri,
            agent=client.agent,
            headers=headers,
            data=body,
            allow_redirects=False,
            unbuffered=True,
        )
        return timeout_deferred(
            deferred=deferred,
            timeout=CALL_UPSTREAM_TIMEOUT_SECONDS,
            clock=client.clock,
        )

    async def _json_response(self, deferred):
        from synapse.logging.context import make_deferred_yieldable
        from synapse.http.client import read_body_with_max_size
        from synapse.util.async_helpers import timeout_deferred

        response = await make_deferred_yieldable(deferred)
        body = io.BytesIO()
        read_deferred = read_body_with_max_size(response, body, MAX_UPSTREAM_RESPONSE_BYTES + 1)
        await make_deferred_yieldable(timeout_deferred(
            deferred=read_deferred,
            timeout=CALL_UPSTREAM_TIMEOUT_SECONDS,
            clock=self.guard.api.http_client.clock,
        ))
        if not 200 <= response.code < 300:
            raise ValueError("Private call authorization dependency returned a non-success status")
        try:
            value = json.loads(body.getvalue().decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Private call authorization dependency returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Private call authorization dependency returned a non-object")
        return value

    async def _openid_user_id(self, proof):
        if not isinstance(proof, Mapping):
            self._deny(401)
        access_token = proof.get("access_token")
        if (proof.get("token_type") != "Bearer" or proof.get("matrix_server_name") != self.guard.server or
                not isinstance(access_token, str) or not access_token or len(access_token) > 8192 or
                any(character.isspace() for character in access_token)):
            self._deny(401)
        uri = OPENID_USERINFO_URL + "?" + urlencode({"access_token": access_token})
        try:
            userinfo = await self._json_response(self._request_json("GET", uri))
        except Exception:
            # Do not log or echo the bearer proof or HTTP response body.
            self._deny(401)
        user_id = userinfo.get("sub")
        if not self.guard.local_human(user_id):
            self._deny(401)
        return user_id

    @staticmethod
    def _member_joined(state, user_id):
        event = state.get(("m.room.member", user_id))
        return bool(event and event.content.get("membership") == "join")

    def _direct_registry_ready(self, room_id, marker):
        try:
            registry = self.guard.read_registry()
        except (OSError, ValueError, TypeError, KeyError):
            return False
        expected_people = list(marker["participants"])
        return any(
            room.get("room_id") == room_id and room.get("status") == "ready" and
            room.get("participants") == expected_people and room.get("bot_user_id") == self.guard.bot
            for room in registry["rooms"] if isinstance(room, Mapping)
        )

    def _room_authorized(self, policy, state, room_id, user_id):
        if not policy or user_id not in {user.get("user_id") for user in policy["users"]}:
            return False
        if not self._member_joined(state, user_id):
            return False

        managed = self.guard.managed_admitted(policy, room_id, user_id) and user_id != self.guard.bot
        direct_marker = None if managed else self.guard.marker_in_state(state)
        if managed:
            pass
        elif (direct_marker is None or user_id not in direct_marker["participants"] or
              not self.guard.allowed_pair(policy, direct_marker) or
              not self._direct_registry_ready(room_id, direct_marker) or
              not self.guard.bot_joined(state)):
            return False

        encryption = state.get(("m.room.encryption", ""))
        if not encryption or encryption.content.get("algorithm") != ALGORITHM:
            return False
        levels = state.get(("m.room.power_levels", ""))
        if not levels or not isinstance(levels.content, Mapping):
            return False
        content = levels.content
        event_levels = content.get("events", {})
        if not isinstance(event_levels, Mapping):
            return False
        event_threshold = event_levels.get(CALL_MEMBER_EVENT, content.get("state_default", 0))
        users = content.get("users", {})
        if not isinstance(users, Mapping):
            return False
        user_level = users.get(user_id, content.get("users_default", 0))
        if (not isinstance(event_threshold, int) or isinstance(event_threshold, bool) or
                event_threshold != CALL_MEMBER_POWER_LEVEL or
                not isinstance(user_level, int) or isinstance(user_level, bool) or
                user_level < event_threshold):
            return False
        return True

    async def _authorize_current_state(self, room_id, user_id):
        try:
            state = await self.guard.api.get_room_state(room_id)
        except Exception:
            return False
        # Load after the asynchronous state read so an expiring or revoked
        # grant cannot survive just because this request started before TTL.
        policy = self.guard.snapshot.load()
        return self._room_authorized(policy, state, room_id, user_id)

    async def _async_render_OPTIONS(self, request):
        request.setHeader(b"Cache-Control", b"no-store")
        return 204, {}

    async def _async_render_POST(self, request):
        request.setHeader(b"Cache-Control", b"no-store")
        length = request.getHeader(b"content-length")
        if length is not None:
            try:
                if int(length) > MAX_CALL_REQUEST_BYTES:
                    raise errors.SynapseError(413, "Request body is too large", errors.Codes.BAD_JSON)
            except (TypeError, ValueError):
                raise errors.SynapseError(400, "Invalid request body", errors.Codes.BAD_JSON)
        raw = request.content.read(MAX_CALL_REQUEST_BYTES + 1)
        if len(raw) > MAX_CALL_REQUEST_BYTES or request.content.read(1):
            raise errors.SynapseError(413, "Request body is too large", errors.Codes.BAD_JSON)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise errors.SynapseError(400, "Invalid request body", errors.Codes.BAD_JSON)
        if not isinstance(body, dict):
            raise errors.SynapseError(400, "Invalid request body", errors.Codes.BAD_JSON)
        if self.client_server_api:
            requester = await self.guard.api.get_user_by_req(request)
            user_id = requester.user.to_string()
            authorization = request.getHeader(b"authorization")
            if isinstance(authorization, bytes):
                try:
                    authorization = authorization.decode("ascii")
                except UnicodeDecodeError:
                    self._deny(401)
            if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
                self._deny(401)
            token = authorization[7:]
            if not token or len(token) > 8192 or any(character.isspace() for character in token):
                self._deny(401)
            room_id = body.get("room_id")
            server_name = body.get("server_name", "")
            if (server_name not in ("", self.guard.server) or
                    not isinstance(body.get("url"), str) or not body["url"] or len(body["url"]) > 2048 or
                    not isinstance(body.get("slot_id"), str) or not body["slot_id"] or len(body["slot_id"]) > 255 or
                    not isinstance(body.get("member_id"), str) or not body["member_id"] or len(body["member_id"]) > 255):
                self._deny(403)
        else:
            proof = body.get("openid_token")
            room_id = body.get("room")
            user_id = await self._openid_user_id(proof)
        if (not isinstance(room_id, str) or len(room_id) < 2 or len(room_id) > 255 or
                not room_id.startswith("!") or any(character.isspace() for character in room_id)):
            self._deny(403)
        if not await self._authorize_current_state(room_id, user_id):
            self._deny(403)

        if self.client_server_api:
            try:
                response = await self._json_response(self._request_json(
                    "POST", SYNAPSE_MATRIX_GET_TOKEN_URL, self._canonical_json(body),
                    authorization=authorization,
                ))
            except Exception:
                self._deny(403)
            # The private application service mints the JWT asynchronously;
            # re-check both policy and room state before returning it so a
            # concurrent role removal or membership change cannot race minting.
            if not await self._authorize_current_state(room_id, user_id):
                self._deny(403)
            jwt = response.get("jwt")
            if not isinstance(jwt, str) or not jwt or len(jwt) > MAX_UPSTREAM_RESPONSE_BYTES:
                raise errors.SynapseError(502, "Call authorization is temporarily unavailable", "M_UNKNOWN")
            # Matrix C-S specifies only the JWT field. Do not forward any
            # unexpected upstream response fields to the client.
            return 200, {"jwt": jwt}

        # The request identity is the OpenID `sub` verified above. Keep the
        # client body for protocol compatibility; neither device_id nor any
        # optional client identity field participates in authorization.
        try:
            response = await self._json_response(self._request_json(
                "POST", LIVEKIT_SFU_GET_URL, self._canonical_json(body)
            ))
        except Exception:
            raise errors.SynapseError(502, "Call authorization is temporarily unavailable", "M_UNKNOWN")

        # Re-read both Keycloak policy and Synapse state after token minting;
        # never return a token after a concurrent revocation or room change.
        if not await self._authorize_current_state(room_id, user_id):
            self._deny(403)
        if not isinstance(response.get("url"), str) or not isinstance(response.get("jwt"), str):
            raise errors.SynapseError(502, "Call authorization is temporarily unavailable", "M_UNKNOWN")
        return 200, response

    @staticmethod
    def _canonical_json(value):
        from canonicaljson import encode_canonical_json

        return encode_canonical_json(value)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing policy timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Policy timestamp needs timezone")
    return parsed.timestamp()


class PolicySnapshot:
    """No cached grants survive missing, malformed, future or expired files."""

    def __init__(self, filename, server_name, now=time.time):
        self.filename = Path(filename)
        self.server_name = server_name
        self.now = now

    def load(self):
        try:
            if self.filename.stat().st_size > 8 * 1024 * 1024:
                return None
            policy = json.loads(self.filename.read_text(encoding="utf-8"))
            generated = timestamp(policy.get("generated_at"))
            expires = timestamp(policy.get("expires_at"))
            now = self.now()
            if (policy.get("schema_version") != 1 or
                    policy.get("matrix_server_name") != self.server_name or
                    not generated <= now < expires or not 0 < expires - generated <= 60):
                return None
            if not isinstance(policy.get("users"), list) or not isinstance(policy.get("rooms"), list):
                return None
            prefix = policy.get("direct_chat", {}).get("group_prefix")
            if not isinstance(prefix, str) or not prefix or not prefix.endswith("."):
                return None
            user_ids = [user["user_id"] for user in policy["users"]]
            if (len(set(user_ids)) != len(user_ids) or
                    any(not isinstance(user_id, str) or not user_id.startswith("@") or
                        not user_id.endswith(":" + self.server_name) for user_id in user_ids)):
                return None
            for user in policy["users"]:
                if not isinstance(user.get("direct_groups"), list):
                    return None
                for group in user["direct_groups"]:
                    if (not isinstance(group, Mapping) or not isinstance(group.get("id"), str) or not group["id"] or
                            not isinstance(group.get("name"), str) or not group["name"].startswith(prefix) or
                            len(group["name"]) <= len(prefix)):
                        return None
            room_ids = []
            for room in policy["rooms"]:
                if not isinstance(room.get("room_id"), str) or not room["room_id"].startswith("!"):
                    return None
                if (not isinstance(room.get("authorized_user_ids"), list) or
                        any(user_id not in user_ids for user_id in room["authorized_user_ids"])):
                    return None
                room_ids.append(room["room_id"])
            if len(set(room_ids)) != len(room_ids):
                return None
            return policy
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def shared_group(self, policy, first, second):
        if not policy or first == second:
            return False
        users = {user["user_id"]: user for user in policy["users"]}
        prefix = policy["direct_chat"]["group_prefix"]

        def groups(user_id):
            result = set()
            for group in users.get(user_id, {}).get("direct_groups", []):
                if not isinstance(group, Mapping):
                    continue
                name, group_id = group.get("name"), group.get("id")
                if (isinstance(name, str) and name.startswith(prefix) and len(name) > len(prefix)
                        and isinstance(group_id, str) and group_id):
                    result.add(group_id)
            return result

        return bool(groups(first) & groups(second))


class RoomGuard:
    @staticmethod
    def parse_config(config):
        for key in ("server_name", "admin_user_id", "bot_user_id", "signing_secret"):
            if not isinstance(config.get(key), str) or not config[key]:
                raise ValueError(f"Chat guard requires {key}")
        if len(config["signing_secret"]) < 32 or config["signing_secret"] == "CHANGE_ME":
            raise ValueError("Chat guard signing secret must contain at least 32 characters")
        for key in ("admin_user_id", "bot_user_id"):
            if not config[key].endswith(":" + config["server_name"]):
                raise ValueError("Chat guard service identities must be local")
        if config["admin_user_id"] == config["bot_user_id"]:
            raise ValueError("Chat guard needs distinct admin and bot accounts")
        return config

    def __init__(self, config, api):
        self.parse_config(config)
        self.api = api
        self.server = config["server_name"]
        self.admin = config["admin_user_id"]
        self.bot = config["bot_user_id"]
        self.secret = config["signing_secret"].encode()
        self.registry = Path(config.get("registry_path", "/chat-guard/direct-rooms.json"))
        self.snapshot = PolicySnapshot(config.get("snapshot_path", "/policy/chat-policy.json"), self.server)
        self.join_snapshot = PolicySnapshot(config.get("join_snapshot_path", "/policy/join-policy.json"), self.server)
        api.register_web_resource("/_synapse/client/foldfence/contacts", AllowedContactsResource(self))
        api.register_web_resource("/_synapse/client/foldfence/call/sfu/get", CallTokenResource(self))
        api.register_web_resource(
            "/_synapse/client/foldfence/call/get-token", CallTokenResource(self, client_server_api=True)
        )
        api.register_spam_checker_callbacks(
            check_username_for_spam=self.check_username_for_spam,
            user_may_create_room=self.user_may_create_room,
            user_may_invite=self.user_may_invite,
            user_may_join_room=self.user_may_join_room,
            user_may_create_room_alias=self.deny_directory_change,
            user_may_publish_room=self.deny_directory_change,
        )
        # This callback covers membership changes (including kicks), initial
        # state and admin-API joins, which the spam callbacks alone do not cover.
        # Upstream labels it experimental: pin Synapse and test before upgrading.
        api.register_third_party_rules_callbacks(
            on_create_room=self.on_create_room,
            check_event_allowed=self.check_event_allowed,
            check_threepid_can_be_invited=self.no_threepid_invites,
            on_new_event=self.on_new_event,
        )
        api.looping_background_call(self.revoke_direct_memberships, 5000)

    def local_human(self, user_id):
        return (isinstance(user_id, str) and user_id.startswith("@") and
                user_id.endswith(":" + self.server) and user_id not in (self.admin, self.bot))

    async def check_username_for_spam(self, user_profile, requester_id):
        """Filter directory results using the same fresh grant as DM creation.

        Synapse's native callback passes the authenticated requester, so this
        works for every client without exposing policy snapshots to clients.
        True excludes a result. Missing/expired policy fails closed.
        """
        candidate = user_profile.get("user_id") if isinstance(user_profile, Mapping) else None
        if not self.local_human(requester_id) or not self.local_human(candidate):
            return True
        return not self.snapshot.shared_group(self.snapshot.load(), requester_id, candidate)

    def marker_payload(self, marker):
        if not isinstance(marker, Mapping):
            return None
        people = marker.get("participants")
        if (not isinstance(people, (list, tuple)) or len(people) != 2 or
                not all(self.local_human(person) for person in people) or people[0] >= people[1] or
                marker.get("creator") not in people or not isinstance(marker.get("nonce"), str)):
            return None
        return {"participants": list(people), "creator": marker["creator"], "nonce": marker["nonce"]}

    def mac(self, payload):
        return hmac.new(self.secret, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    def valid_marker(self, marker):
        payload = self.marker_payload(marker)
        return bool(payload and isinstance(marker.get("mac"), str) and hmac.compare_digest(marker["mac"], self.mac(payload)))

    def marker_in_state(self, state):
        create = state.get(("m.room.create", ""))
        marker = create.content.get(MARKER) if create else None
        if not self.valid_marker(marker):
            return None
        if (create.sender != marker["creator"] or create.content.get("m.federate") is not False
                or create.content.get("room_version") != "11"):
            return None
        return marker

    def allowed_pair(self, policy, marker):
        return self.snapshot.shared_group(policy, *marker["participants"])

    def power_levels(self, marker):
        return {
            "users": {marker["creator"]: 100, self.admin: 100, self.bot: 0,
                      **{person: 10 for person in marker["participants"] if person != marker["creator"]}},
            "users_default": 0, "events_default": 100, "state_default": 100,
            "invite": 10, "kick": 100, "ban": 100, "redact": 100,
            "events": {"m.room.encrypted": 10, "m.reaction": 10, "org.matrix.msc3401.call.member": 10},
        }

    async def on_create_room(self, requester, request_content, is_requester_admin):
        user_id = requester.user.to_string()
        if user_id == self.admin:
            return
        invite = request_content.get("invite")
        if (not self.local_human(user_id) or not isinstance(invite, list) or len(invite) != 1 or
                not self.local_human(invite[0]) or request_content.get("visibility", "private") != "private" or
                request_content.get("invite_3pid") or request_content.get("room_alias_name")):
            raise errors.SynapseError(403, "Only policy-authorized direct chats may be created", errors.Codes.FORBIDDEN)
        policy = self.snapshot.load()
        if not self.snapshot.shared_group(policy, user_id, invite[0]):
            raise errors.SynapseError(403, "No shared Keycloak direct-chat group", errors.Codes.FORBIDDEN)
        payload = {"participants": sorted([user_id, invite[0]]), "creator": user_id, "nonce": secrets.token_hex(16)}
        marker = {**payload, "mac": self.mac(payload)}
        target = invite[0]
        # Replace every client-controlled security input, including initial state,
        # room type, federation, power overrides and upgrade/predecessor fields.
        request_content.clear()
        request_content.update({
            "visibility": "private", "preset": "private_chat", "is_direct": True,
            "room_version": "11", "invite": [self.bot, target],
            "creation_content": {"m.federate": False, MARKER: marker},
            "initial_state": [
                {"type": "m.room.encryption", "state_key": "", "content": {"algorithm": ALGORITHM}},
                {"type": "m.room.history_visibility", "state_key": "", "content": {"history_visibility": "joined"}},
                {"type": "m.room.guest_access", "state_key": "", "content": {"guest_access": "forbidden"}},
                {"type": "m.room.power_levels", "state_key": "", "content": self.power_levels(marker)},
            ],
        })

    async def user_may_create_room(self, user_id, room_config):
        if user_id == self.admin:
            return NOT_SPAM
        marker = room_config.get("creation_content", {}).get(MARKER)
        return NOT_SPAM if (self.valid_marker(marker) and marker["creator"] == user_id and
                            self.allowed_pair(self.snapshot.load(), marker)) else errors.Codes.FORBIDDEN

    async def deny_directory_change(self, user_id, *args):
        return NOT_SPAM if user_id == self.admin else errors.Codes.FORBIDDEN

    async def no_threepid_invites(self, *args):
        return False

    def managed_admitted(self, policy, room_id, user_id):
        if not policy:
            return False
        for room in policy["rooms"]:
            if room.get("room_id") == room_id:
                return user_id in room.get("authorized_user_ids", []) or user_id == self.bot
        return False

    def bootstrap_join_admitted(self, room_id, user_id, state):
        create = state.get(("m.room.create", ""))
        policy = self.join_snapshot.load()
        return bool(create and create.sender == self.admin and policy and
                    policy.get("purpose") == "managed_room_join" and
                    self.managed_admitted(policy, room_id, user_id))

    async def user_may_join_room(self, user_id, room_id, is_invited):
        policy = self.snapshot.load()
        if user_id == self.admin or self.managed_admitted(policy, room_id, user_id):
            return NOT_SPAM
        state = await self.api.get_room_state(room_id)
        if self.bootstrap_join_admitted(room_id, user_id, state):
            return NOT_SPAM
        marker = self.marker_in_state(state)
        return NOT_SPAM if (marker and user_id in [*marker["participants"], self.bot] and
                            self.allowed_pair(policy, marker)) else errors.Codes.FORBIDDEN

    async def user_may_invite(self, inviter, invitee, room_id):
        if inviter == self.admin:
            return NOT_SPAM
        state = await self.api.get_room_state(room_id)
        marker = self.marker_in_state(state)
        policy = self.snapshot.load()
        sender_allowed = bool(marker and inviter in marker["participants"])
        target_allowed = bool(marker and invitee in [*marker["participants"], self.bot])
        pair_allowed = bool(marker and self.allowed_pair(policy, marker))
        allowed = sender_allowed and target_allowed and pair_allowed
        # No identity, room ID, group, token or signature is written to this log.
        logger.info("Room invitation policy: marker=%s sender=%s target=%s fresh=%s shared=%s allowed=%s",
                    bool(marker), sender_allowed, target_allowed, policy is not None, pair_allowed, allowed)
        if marker is None:
            create = state.get(("m.room.create", ""))
            raw = create.content.get(MARKER) if create else None
            logger.info("Room invitation marker: create=%s payload=%s signature=%s creator=%s local=%s v11=%s",
                        create is not None, bool(self.marker_payload(raw)), self.valid_marker(raw),
                        bool(create and isinstance(raw, Mapping) and create.sender == raw.get("creator")),
                        bool(create and create.content.get("m.federate") is False),
                        bool(create and create.content.get("room_version") == "11"))
        return NOT_SPAM if allowed else errors.Codes.FORBIDDEN

    async def check_event_allowed(self, event, state_events):
        # Self-leave and invitation rejection must keep working during outages.
        if (event.type == "m.room.member" and event.sender == event.state_key and
                event.content.get("membership") == "leave"):
            return True, None
        if event.sender == self.admin:
            return True, None
        policy = self.snapshot.load()
        create = state_events.get(("m.room.create", ""))
        # The admin join API uses a requester for the target user, so its spam
        # callback runs too. A separate, expiring exact-room join plan breaks
        # the startup cycle without granting messages or export access.
        if (create and create.sender == self.admin and event.type == "m.room.member" and
                event.sender == event.state_key and event.content.get("membership") == "join"):
            return (self.managed_admitted(policy, event.room_id, event.sender) or
                    self.bootstrap_join_admitted(event.room_id, event.sender, state_events)), None
        if self.managed_admitted(policy, event.room_id, event.sender):
            if event.sender == self.bot:
                return False, None  # OrgBot remains read-only in managed rooms.
            if event.type in ("m.room.message", "m.sticker"):
                return False, None  # Managed rooms carry content only as Megolm ciphertext.
            if event.type == "m.room.encrypted":
                encryption = state_events.get(("m.room.encryption", ""))
                return bool(encryption and encryption.content.get("algorithm") == ALGORITHM), None
            return True, None
        if event.type == "m.room.create":
            marker = event.content.get(MARKER)
            return bool(self.valid_marker(marker) and marker["creator"] == event.sender and
                        event.content.get("m.federate") is False and event.content.get("room_version") == "11" and
                        self.allowed_pair(policy, marker)), None
        marker = self.marker_in_state(state_events)
        if not marker or not self.allowed_pair(policy, marker):
            return False, None
        people = marker["participants"]
        if event.type == "m.room.member":
            target, membership = event.state_key, event.content.get("membership")
            if target not in [*people, self.bot]:
                return False, None
            if membership == "invite":
                return event.sender in people, None
            if membership == "join":
                return event.sender == target, None
            return False, None  # no kicks, bans or bot removal by either person
        if event.sender not in people:
            return False, None
        if event.is_state():
            key = (event.type, event.state_key)
            if event.type == "org.matrix.msc3401.call.member":
                own_key = (event.state_key == event.sender or event.state_key.startswith(event.sender + "_")
                           or event.state_key.startswith("_" + event.sender + "_"))
                return own_key and self.bot_joined(state_events), None
            initial = {
                "m.room.power_levels": self.power_levels(marker),
                "m.room.encryption": {"algorithm": ALGORITHM},
                "m.room.history_visibility": {"history_visibility": "joined"},
                "m.room.join_rules": {"join_rule": "invite"},
                "m.room.guest_access": {"guest_access": "forbidden"},
            }
            return bool(event.sender == marker["creator"] and event.state_key == "" and key not in state_events
                        and event.type in initial and event.content == initial[event.type]), None
        encrypted = state_events.get(("m.room.encryption", ""))
        return bool(event.type in ("m.room.encrypted", "m.reaction", "m.room.redaction") and
                    encrypted and encrypted.content.get("algorithm") == ALGORITHM and self.bot_joined(state_events)), None

    def bot_joined(self, state):
        member = state.get(("m.room.member", self.bot))
        return bool(member and member.content.get("membership") == "join")

    def read_registry(self):
        if not self.registry.exists():
            return {"schema_version": 1, "matrix_server_name": self.server, "rooms": []}
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or data.get("matrix_server_name") != self.server or not isinstance(data.get("rooms"), list):
            raise ValueError("Invalid direct-room registry")
        for room in data["rooms"]:
            if (not isinstance(room, Mapping) or not isinstance(room.get("room_id"), str) or
                    not room["room_id"].startswith("!") or
                    not isinstance(room.get("participants"), list) or len(room["participants"]) != 2 or
                    not all(self.local_human(user_id) for user_id in room["participants"]) or
                    len(set(room["participants"])) != 2 or
                    room.get("bot_user_id") != self.bot or room.get("encrypted") is not True or
                    room.get("status") not in {"pending_bot", "ready", "policy_unavailable"}):
                raise ValueError("Invalid direct-room registry entry")
        return data

    def write_registry(self, data):
        self.registry.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".direct-rooms-", dir=self.registry.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.fchmod(output.fileno(), 0o640)
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.registry)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def on_new_event(self, event, state_events):
        if event.type == "m.room.create":
            marker = self.marker_in_state({("m.room.create", ""): event})
            if not marker:
                return
            data = self.read_registry()
            if any(room["room_id"] == event.room_id for room in data["rooms"]):
                return
            policy = self.snapshot.load()
            policy_available = self.snapshot.shared_group(policy, *marker["participants"])
            data["rooms"].append({"room_id": event.room_id, "participants": list(marker["participants"]),
                                  "bot_user_id": self.bot, "encrypted": True,
                                  "status": "pending_bot" if policy_available else "policy_unavailable",
                                  "name": "Policy direct chat", "minimum_clearance": "unclassified_direct_chat",
                                  "minimum_clearance_rank": 0})
            self.write_registry(data)
        elif (event.type == "m.room.encryption" or
              (event.type == "m.room.member" and event.state_key == self.bot)):
            if not self.marker_in_state(state_events):
                return
            data = self.read_registry()
            room = next((room for room in data["rooms"] if room["room_id"] == event.room_id), None)
            if room is None:
                return
            policy = self.snapshot.load()
            if policy is None:
                status = "policy_unavailable"
            elif not self.snapshot.shared_group(policy, *room["participants"]):
                status = "pending_bot"
            else:
                encryption = state_events.get(("m.room.encryption", ""))
                ready = bool(encryption and encryption.content.get("algorithm") == ALGORITHM and self.bot_joined(state_events))
                status = "ready" if ready else "pending_bot"
            if room.get("status") != status:
                room["status"] = status
                self.write_registry(data)

    async def revoke_direct_memberships(self):
        try:
            data = self.read_registry()
            policy = self.snapshot.load()
            if policy is None:
                changed = False
                for room in data["rooms"]:
                    if room.get("status") != "policy_unavailable":
                        room["status"] = "policy_unavailable"
                        changed = True
                if changed:
                    self.write_registry(data)
                # An unavailable snapshot is not evidence that Keycloak
                # revoked a grant. Keep current membership and fail closed at
                # every policy-controlled action until a fresh snapshot returns.
                return
            changed = False
            for room in data["rooms"]:
                people = room["participants"]
                state = await self.api.get_room_state(room["room_id"])
                marker = self.marker_in_state(state)
                permitted = bool(marker and list(marker["participants"]) == sorted(people) and
                                 self.snapshot.shared_group(policy, *people))
                encryption = state.get(("m.room.encryption", ""))
                ready = bool(permitted and encryption and encryption.content.get("algorithm") == ALGORITHM
                             and self.bot_joined(state))
                status = "ready" if ready else "pending_bot"
                if room.get("status") != status:
                    room["status"] = status
                    changed = True
                if permitted:
                    continue
                for user_id in [*people, self.bot]:
                    member = state.get(("m.room.member", user_id))
                    if member and member.content.get("membership") in ("join", "invite"):
                        await self.api.update_room_membership(user_id, user_id, room["room_id"], "leave")
            if changed:
                self.write_registry(data)
        except Exception:
            # Events and joins still fail closed if cleanup cannot finish.
            # Do not log event bodies, identities or service credentials.
            logger.error("Direct-chat membership cleanup failed; policy gates remain closed")
