"""E2EE-aware Matrix OrgBot and private portable-export endpoint.

Synapse relays encrypted room events. This client can expose plaintext only for
events its own matrix-nio device successfully decrypts. Its device keys remain
in the private, persistent SDK store; export content is held in bounded memory
and is never written to logs or a bot-owned message database.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import inspect
import io
import json
import logging
import mimetypes
import os
import re
import signal
import stat
import zipfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import nio
from aiohttp import ClientSession, ClientTimeout, web
from nio import AsyncClient, AsyncClientConfig, SyncError


LOG = logging.getLogger("orgbot")
ROOM_ID_RE = re.compile(r"^![A-Za-z0-9._=-]+(?::[A-Za-z0-9.:-]+)?$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
EXPORT_FORMAT = "matrix-portable-v1"
DEFAULT_ATTACHMENT_LIMIT = 25 * 1024 * 1024
DEFAULT_CACHE_BYTES = 64 * 1024 * 1024
MAX_SESSION_FILE_BYTES = 8192
class DirectRoomPolicyError(ValueError):
    """A direct-room authorization snapshot is malformed or stale."""


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise DirectRoomPolicyError("snapshot_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DirectRoomPolicyError("snapshot_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise DirectRoomPolicyError("snapshot_timestamp_must_be_utc")
    return parsed.astimezone(timezone.utc)


def _on_server(identifier: Any, sigil: str, server_name: str) -> bool:
    if not isinstance(identifier, str) or not identifier.startswith(sigil):
        return False
    localpart, separator, server = identifier[1:].partition(":")
    return bool(localpart and separator and server == server_name)


def _eligible_direct_groups(user: dict[str, Any], group_prefix: str) -> dict[str, str]:
    groups = user.get("direct_groups", [])
    if not isinstance(groups, list):
        raise DirectRoomPolicyError("direct_groups_invalid")
    result: dict[str, str] = {}
    seen_ids: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise DirectRoomPolicyError("direct_group_entry_invalid")
        group_id, name = group.get("id"), group.get("name")
        path = group.get("path")
        if not all(isinstance(value, str) and value for value in (group_id, name)):
            raise DirectRoomPolicyError("direct_group_identity_invalid")
        if path is not None and not isinstance(path, str):
            raise DirectRoomPolicyError("direct_group_path_invalid")
        if group_id in seen_ids:
            raise DirectRoomPolicyError("duplicate_direct_group_id")
        seen_ids.add(group_id)
        # Use only groups explicitly present in this user's direct_groups list.
        # The group's exact ID joins the pair; path is descriptive metadata and
        # does not infer membership through any ancestor.
        if name.startswith(group_prefix) and len(name) > len(group_prefix):
            result[group_id] = name
    return result


def authorize_direct_rooms(
    registry: Any,
    chat_policy: Any,
    bot_user_id: str,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Intersect the room registry with the fresh enabled-user group snapshot.

    The registry names exact room IDs and the invited pair. It is never enough
    on its own: both local users must still be present in the fresh snapshot and
    share the exact same explicit group ID whose current name matches the
    configured direct-chat prefix. Group paths and ancestors do not authorize.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise DirectRoomPolicyError("direct_room_registry_invalid")
    if not isinstance(chat_policy, dict) or chat_policy.get("schema_version") != 1:
        raise DirectRoomPolicyError("chat_policy_invalid")

    server_name = registry.get("matrix_server_name")
    if not isinstance(server_name, str) or not server_name or chat_policy.get("matrix_server_name") != server_name:
        raise DirectRoomPolicyError("matrix_server_name_mismatch")
    if not _on_server(bot_user_id, "@", server_name):
        raise DirectRoomPolicyError("orgbot_identity_server_mismatch")

    generated_at = _timestamp(chat_policy.get("generated_at"))
    expires_at = _timestamp(chat_policy.get("expires_at"))
    if generated_at > now:
        raise DirectRoomPolicyError("chat_policy_generated_in_future")
    ttl_seconds = (expires_at - generated_at).total_seconds()
    if ttl_seconds <= 0 or ttl_seconds > 60:
        raise DirectRoomPolicyError("chat_policy_ttl_invalid")
    if expires_at <= now:
        raise DirectRoomPolicyError("chat_policy_expired")

    direct_chat = chat_policy.get("direct_chat")
    group_prefix = direct_chat.get("group_prefix") if isinstance(direct_chat, dict) else None
    if (
        not isinstance(group_prefix, str)
        or not group_prefix
        or not group_prefix.endswith(".")
        or group_prefix.strip() != group_prefix
    ):
        raise DirectRoomPolicyError("direct_group_prefix_invalid")

    def user_id_list(field: str) -> set[str]:
        values = chat_policy.get(field)
        if not isinstance(values, list):
            raise DirectRoomPolicyError(f"{field}_invalid")
        result: set[str] = set()
        for value in values:
            if not _on_server(value, "@", server_name) or value in result:
                raise DirectRoomPolicyError(f"{field}_entry_invalid_or_duplicate")
            result.add(value)
        return result

    service_users = user_id_list("service_user_ids")
    admin_users = user_id_list("admin_user_ids")
    excluded_users = service_users | admin_users
    users_raw = chat_policy.get("users")
    if not isinstance(users_raw, list):
        raise DirectRoomPolicyError("chat_policy_users_invalid")
    users: dict[str, dict[str, Any]] = {}
    direct_groups_by_user: dict[str, dict[str, str]] = {}
    seen_users: set[str] = set()
    for user in users_raw:
        if not isinstance(user, dict):
            raise DirectRoomPolicyError("chat_policy_user_invalid")
        user_id = user.get("user_id")
        if not _on_server(user_id, "@", server_name) or user_id in seen_users:
            raise DirectRoomPolicyError("chat_policy_user_id_invalid_or_duplicate")
        seen_users.add(user_id)
        enabled = user.get("enabled", True)
        if not isinstance(enabled, bool):
            raise DirectRoomPolicyError("chat_policy_user_enabled_invalid")
        groups = _eligible_direct_groups(user, group_prefix)
        if enabled:
            users[user_id] = user
            direct_groups_by_user[user_id] = groups

    entries = registry.get("rooms")
    if not isinstance(entries, list):
        raise DirectRoomPolicyError("direct_room_registry_rooms_invalid")
    room_ids: set[str] = set()
    authorized: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise DirectRoomPolicyError("direct_room_entry_invalid")
        room_id = entry.get("room_id")
        if not _on_server(room_id, "!", server_name) or room_id in room_ids:
            raise DirectRoomPolicyError("direct_room_id_invalid_or_duplicate")
        room_ids.add(room_id)
        participants = entry.get("participants")
        if (
            not isinstance(participants, list)
            or len(participants) != 2
            or any(not _on_server(user_id, "@", server_name) for user_id in participants)
            or len(set(participants)) != 2
        ):
            raise DirectRoomPolicyError("direct_room_participants_invalid")
        if entry.get("bot_user_id") != bot_user_id or entry.get("encrypted") is not True:
            raise DirectRoomPolicyError("direct_room_bot_or_encryption_invalid")
        status = entry.get("status")
        if status not in {"pending_bot", "ready", "policy_unavailable"}:
            raise DirectRoomPolicyError("direct_room_status_invalid")
        pair = tuple(sorted(participants))
        if any(
            user_id == bot_user_id or user_id in excluded_users or user_id not in users
            for user_id in pair
        ):
            continue
        groups_a = direct_groups_by_user[pair[0]]
        groups_b = direct_groups_by_user[pair[1]]
        common_ids = set(groups_a) & set(groups_b)
        if not common_ids:
            continue

        room_name = entry.get("name")
        clearance = entry.get("minimum_clearance")
        clearance_rank = entry.get("minimum_clearance_rank")
        if not isinstance(room_name, str) or not room_name:
            raise DirectRoomPolicyError("direct_room_name_invalid")
        if clearance != "unclassified_direct_chat":
            raise DirectRoomPolicyError("direct_room_classification_invalid")
        if not isinstance(clearance_rank, int) or isinstance(clearance_rank, bool) or clearance_rank < 0:
            raise DirectRoomPolicyError("direct_room_clearance_rank_invalid")
        authorized[room_id] = {
            "room_id": room_id,
            "name": room_name,
            "classification": "unclassified_direct_chat",
            "minimum_clearance": "unclassified_direct_chat",
            "minimum_clearance_rank": clearance_rank,
            "authorization_basis": "shared_keycloak_group",
            "status": status,
            "participants": list(pair),
            "shared_direct_group_ids": sorted(common_ids),
            "shared_direct_group_names": sorted(
                {groups_a[group_id] for group_id in common_ids}
                | {groups_b[group_id] for group_id in common_ids}
            ),
            "encrypted": True,
        }
    return authorized, room_ids


def invite_sender(invite_info: Any, bot_user_id: str) -> str | None:
    """Read the inviter from the invite-state event targeted at this bot."""
    if isinstance(invite_info, dict):
        invite_state = invite_info.get("invite_state")
    else:
        invite_state = getattr(invite_info, "invite_state", None)
    if isinstance(invite_state, (list, tuple)):
        events = invite_state
    elif isinstance(invite_state, dict):
        events = invite_state.get("events")
    else:
        events = getattr(invite_state, "events", None)
    if not isinstance(events, (list, tuple)):
        return None

    inviters: set[str] = set()
    for event in events:
        source = event if isinstance(event, dict) else event_source(event)
        if not source:
            continue
        content = source.get("content")
        if (
            source.get("type") == "m.room.member"
            and source.get("state_key") == bot_user_id
            and isinstance(content, dict)
            and content.get("membership") == "invite"
        ):
            sender = source.get("sender")
            if isinstance(sender, str) and sender:
                inviters.add(sender)
    return next(iter(inviters)) if len(inviters) == 1 else None


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _safe_identity_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and value.isascii()
        and all("!" <= char <= "~" for char in value)
    )


def read_session_file(path: Path, expected_user_id: str, expected_device_id: str) -> dict[str, Any]:
    """Read the provisioner-issued token from a private, owner-only file."""
    try:
        if not path.is_absolute():
            raise ValueError("session path must be absolute")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("session file is not regular")
        if stat.S_IMODE(before.st_mode) != 0o600 or before.st_uid != os.geteuid():
            raise ValueError("session file permissions or owner are unsafe")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_uid != os.geteuid()
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
            ):
                raise ValueError("session file changed during read")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(MAX_SESSION_FILE_BYTES + 1)
        finally:
            os.close(descriptor)

        if len(raw) > MAX_SESSION_FILE_BYTES:
            raise ValueError("session file is too large")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("OrgBot session file is missing, malformed, or unsafe") from exc

    required_fields = {"schema_version", "user_id", "device_id", "access_token", "admin"}
    if not isinstance(document, dict) or set(document) != required_fields:
        raise RuntimeError("OrgBot session file has an invalid schema")
    token = document.get("access_token")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("user_id") != expected_user_id
        or document.get("device_id") != expected_device_id
        or not _safe_identity_string(document.get("user_id"))
        or not _safe_identity_string(document.get("device_id"))
        or document.get("admin") is not False
        or not isinstance(token, str)
        or not 16 <= len(token) <= MAX_SESSION_FILE_BYTES
        or not token.isascii()
        or any(not ("!" <= char <= "~") for char in token)
    ):
        raise RuntimeError("OrgBot session identity or privilege contract is invalid")
    return document


def iso_timestamp(timestamp_ms: Any) -> str | None:
    if not isinstance(timestamp_ms, int):
        return None
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def text_bytes(value: Any) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def event_source(event: Any) -> dict[str, Any]:
    source = getattr(event, "source", None)
    return source if isinstance(source, dict) else {}


def event_content(event: Any) -> dict[str, Any]:
    content = event_source(event).get("content", {})
    return content if isinstance(content, dict) else {}


def event_msgtype(event: Any) -> str:
    value = getattr(event, "msgtype", None) or event_content(event).get("msgtype")
    return value if isinstance(value, str) else "m.room.message"


def safe_filename(name: Any) -> str:
    """Keep a display filename in metadata, but only a generated path in ZIP."""
    if not isinstance(name, str) or not name.strip():
        return "attachment"
    normalized = name.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = "".join(ch for ch in normalized if ch.isprintable() and ch not in "\x00")
    return normalized[:255] or "attachment"


def room_encryption(room: Any) -> tuple[bool | None, str | None]:
    """Return the SDK-known room encryption state and algorithm, if available."""
    flag = getattr(room, "encrypted", None)
    encryption = getattr(room, "encryption_event", None)
    if not isinstance(encryption, dict):
        encryption = getattr(room, "encryption", None)
    if isinstance(encryption, dict):
        content = encryption.get("content", encryption)
        algorithm = content.get("algorithm") if isinstance(content, dict) else None
        return True, algorithm if isinstance(algorithm, str) else None
    if isinstance(flag, bool):
        return flag, "m.megolm.v1.aes-sha2" if flag else None
    return None, None


class OrgBot:
    def __init__(self) -> None:
        self.homeserver = required("ORG_BOT_HOMESERVER_URL").rstrip("/")
        self.user_id = required("ORG_BOT_USER_ID")
        self.device_id = required("ORG_BOT_DEVICE_ID")
        self.session_file = Path(required("ORG_BOT_SESSION_FILE"))
        self.export_token = required("ORG_BOT_EXPORT_TOKEN")
        if len(self.export_token) < 32:
            raise RuntimeError("ORG_BOT_EXPORT_TOKEN must contain at least 32 characters")

        self.store_path = Path(os.environ.get("ORG_BOT_STORE_PATH", "/var/lib/orgbot"))
        self.room_map_path = Path(os.environ.get("ORG_BOT_ROOM_MAP_PATH", "/data/room-map.json"))
        self.direct_room_registry_path = Path(
            os.environ.get(
                "ORG_BOT_DIRECT_ROOMS_PATH",
                os.environ.get("ORG_BOT_DIRECT_ROOM_REGISTRY_PATH", "/run/chat-guard/direct-rooms.json"),
            )
        )
        self.chat_policy_path = Path(os.environ.get("ORG_BOT_CHAT_POLICY_PATH", "/data/chat-policy.json"))
        self.matrix_server_name = self.user_id.partition(":")[2]
        self.http_port = int(os.environ.get("ORG_BOT_HTTP_PORT", "8090"))
        self.max_events = max(100, min(int(os.environ.get("ORG_BOT_CACHE_LIMIT", "5000")), 50000))
        self.max_cache_bytes = max(1024 * 1024, int(os.environ.get("ORG_BOT_CACHE_MAX_BYTES", str(DEFAULT_CACHE_BYTES))))
        self.max_export_events = max(1, min(int(os.environ.get("ORG_BOT_EXPORT_MAX_EVENTS", "5000")), 50000))
        self.max_attachment_bytes = max(1024, int(os.environ.get("ORG_BOT_MAX_ATTACHMENT_BYTES", str(DEFAULT_ATTACHMENT_LIMIT))))
        self.sync_stale_seconds = max(30, int(os.environ.get("ORG_BOT_SYNC_STALE_SECONDS", "90")))
        self.cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self.cache_bytes = 0
        self.cache_evicted_rooms: set[str] = set()
        self.direct_room_registry_ids: set[str] = set()
        self.authorized_direct_rooms: dict[str, dict[str, Any]] = {}
        self.pending_direct_invites: dict[str, str] = {}
        self.direct_join_attempt_at: dict[str, float] = {}
        self.started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.ready = False
        self.device_keys_verified = False
        self.device_keys_verified_at: float | None = None
        self.device_key_check_interval = 300.0
        self.last_sync_at: datetime | None = None
        self.stop_event = asyncio.Event()

        os.umask(0o077)
        self.store_path.mkdir(parents=True, exist_ok=True)
        if self.store_path.is_symlink():
            raise RuntimeError("OrgBot SDK store path must not be a symbolic link")
        self.store_path.chmod(stat.S_IRWXU)
        sdk_config = AsyncClientConfig(encryption_enabled=True, store_sync_tokens=True)
        self.client = AsyncClient(
            self.homeserver,
            self.user_id,
            device_id=self.device_id,
            store_path=str(self.store_path),
            config=sdk_config,
        )
        self._register_event_callbacks()

    def _register_event_callbacks(self) -> None:
        """Subscribe to decrypted message subclasses and undecrypted Megolm events.

        matrix-nio releases expose the base RoomMessage in their public API. The
        fallbacks keep the module loadable across the supported SDK range while
        retaining text/image/file/audio/video coverage where available.
        """
        room_message = getattr(nio, "RoomMessage", None)
        if isinstance(room_message, type):
            self.client.add_event_callback(self.on_decrypted_message, room_message)
        else:
            for name in (
                "RoomMessageText", "RoomMessageNotice", "RoomMessageEmote",
                "RoomMessageImage", "RoomMessageFile", "RoomMessageAudio", "RoomMessageVideo",
            ):
                event_type = getattr(nio, name, None)
                if isinstance(event_type, type):
                    self.client.add_event_callback(self.on_decrypted_message, event_type)

        for name in ("MegolmEvent", "RoomEncryptedEvent"):
            event_type = getattr(nio, name, None)
            if isinstance(event_type, type):
                self.client.add_event_callback(self.on_undecrypted_event, event_type)
                break

    def authorized(self, request: web.Request) -> bool:
        supplied = request.headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return False
        return hmac.compare_digest(supplied.removeprefix("Bearer ").strip(), self.export_token)

    async def health(self, request: web.Request) -> web.Response:
        if not self._sync_is_healthy():
            return web.json_response({"status": "starting"}, status=503)
        return web.json_response({"status": "ready"})

    def _sync_is_healthy(self) -> bool:
        if not self.ready or not getattr(self, "device_keys_verified", False) or self.last_sync_at is None:
            return False
        age = (datetime.now(timezone.utc) - self.last_sync_at).total_seconds()
        return age <= self.sync_stale_seconds

    async def room_readiness(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        room_id = unquote(request.match_info.get("room_id", ""))
        if not ROOM_ID_RE.fullmatch(room_id):
            return web.json_response({"error": "invalid_room_id"}, status=400)
        if not self._sync_is_healthy():
            return web.json_response(
                {"status": "not_ready", "reason": "matrix_sync_unhealthy"}, status=503
            )

        baseline_rooms, _map_error = self._load_room_map()
        authorized, error = self._refresh_direct_room_authorization()
        is_direct_or_unregistered = (
            room_id in self.direct_room_registry_ids or room_id not in baseline_rooms
        )
        if is_direct_or_unregistered:
            room_policy = authorized.get(room_id)
            if error is not None or room_policy is None or room_policy.get("status") != "ready":
                return web.json_response(
                    {"status": "not_ready", "reason": "direct_room_not_currently_authorized"}, status=503
                )

        room = self.client.rooms.get(room_id)
        if room is None:
            return web.json_response(
                {"status": "not_ready", "reason": "room_missing_from_joined_sync_state"}, status=503
            )
        encrypted, algorithm = room_encryption(room)
        if encrypted is not True or not isinstance(algorithm, str) or not algorithm:
            return web.json_response(
                {"status": "not_ready", "reason": "matrix_sync_does_not_confirm_room_encryption"}, status=503
            )
        room_users = getattr(room, "users", None)
        bot_membership = room_users.get(self.user_id) if isinstance(room_users, dict) else None
        if bot_membership is None or getattr(bot_membership, "invited", None) is not False:
            return web.json_response(
                {"status": "not_ready", "reason": "matrix_sync_does_not_confirm_bot_membership"}, status=503
            )

        assert self.last_sync_at is not None
        return web.json_response(
            {
                "status": "ready",
                "room_id": room_id,
                "encrypted": True,
                "algorithm": algorithm,
                "bot_user_id": self.user_id,
                "bot_device_id": self.device_id,
                "synced_at": self.last_sync_at.isoformat().replace("+00:00", "Z"),
            }
        )

    def _display_name(self, room: Any, sender: str) -> str | None:
        users = getattr(room, "users", {})
        user = users.get(sender) if isinstance(users, dict) else None
        name = getattr(user, "display_name", None)
        return name if isinstance(name, str) and name else None

    def _direct_room_capture_allowed(self, room_id: str, room: Any) -> bool:
        if room_id not in self.direct_room_registry_ids:
            return True
        policy = self.authorized_direct_rooms.get(room_id)
        if policy is None or policy.get("status") not in {"pending_bot", "ready"}:
            return False
        encrypted, algorithm = room_encryption(room)
        return encrypted is True and isinstance(algorithm, str) and bool(algorithm)

    async def on_decrypted_message(self, room: Any, event: Any) -> None:
        room_id = getattr(room, "room_id", None)
        event_id = getattr(event, "event_id", None)
        sender = getattr(event, "sender", None)
        timestamp = getattr(event, "server_timestamp", None)
        if not (isinstance(room_id, str) and isinstance(event_id, str) and isinstance(sender, str)):
            return
        if not self._direct_room_capture_allowed(room_id, room):
            return

        source = event_source(event)
        content = event_content(event)
        body = getattr(event, "body", None)
        if not isinstance(body, str):
            body = content.get("body") if isinstance(content.get("body"), str) else None
        encrypted, algorithm = room_encryption(room)
        status = "available"
        reason: str | None = None
        if body is None and event_msgtype(event) in {"m.text", "m.notice", "m.emote"}:
            status = "unavailable"
            reason = "decrypted_message_body_missing"

        attachment_entries: list[dict[str, Any]] = []
        media_reference = content.get("file") if isinstance(content.get("file"), dict) else content.get("url")
        if isinstance(media_reference, (dict, str)):
            entry = await self._fetch_attachment(event, content)
            attachment_entries.append(entry)
            if not entry["available"]:
                status = "partial" if status == "available" else status
                reason = reason or "one_or_more_attachments_unavailable"

        record = {
            "record_type": "message",
            "event_id": event_id,
            "room_id": room_id,
            "room_name": getattr(room, "display_name", None) or getattr(room, "name", None),
            "classification": None,
            "matrix_user_id": sender,
            "display_name": self._display_name(room, sender),
            "origin_server_ts": timestamp if isinstance(timestamp, int) else None,
            "timestamp": iso_timestamp(timestamp),
            "message_type": event_msgtype(event),
            "message_body": body,
            "attachment_refs": [entry["path"] for entry in attachment_entries if entry.get("available")],
            "attachments": attachment_entries,
            "encryption": {
                "encrypted": encrypted,
                "algorithm": algorithm,
                "decryption_status": "decrypted_by_matrix_nio_device" if encrypted else (
                    "not_applicable" if encrypted is False else "room_encryption_state_unknown"
                ),
                "device_id": self.device_id if encrypted else None,
            },
            "content_status": status,
            "content_status_reason": reason,
            "_media": {entry["path"]: entry.pop("_bytes", None) for entry in attachment_entries},
        }
        self._cache_record(record)

    async def on_undecrypted_event(self, room: Any, event: Any) -> None:
        room_id = getattr(room, "room_id", None)
        event_id = getattr(event, "event_id", None)
        sender = getattr(event, "sender", None)
        timestamp = getattr(event, "server_timestamp", None)
        if not (isinstance(room_id, str) and isinstance(event_id, str) and isinstance(sender, str)):
            return
        if not self._direct_room_capture_allowed(room_id, room):
            return
        encrypted, algorithm = room_encryption(room)
        record = {
            "record_type": "message",
            "event_id": event_id,
            "room_id": room_id,
            "room_name": getattr(room, "display_name", None) or getattr(room, "name", None),
            "classification": None,
            "matrix_user_id": sender,
            "display_name": self._display_name(room, sender),
            "origin_server_ts": timestamp if isinstance(timestamp, int) else None,
            "timestamp": iso_timestamp(timestamp),
            "message_type": "m.room.encrypted",
            "message_body": None,
            "attachment_refs": [],
            "attachments": [],
            "encryption": {
                "encrypted": True if encrypted is None else encrypted,
                "algorithm": algorithm or "m.megolm.v1.aes-sha2",
                "decryption_status": "undecrypted_missing_key_or_decryption_failed",
                "device_id": self.device_id,
            },
            "content_status": "undecrypted",
            "content_status_reason": "matrix_nio_could_not_decrypt_event; ciphertext omitted",
            "_media": {},
        }
        self._cache_record(record)

    def _cache_record(self, record: dict[str, Any]) -> None:
        key = (record["room_id"], record["event_id"])
        previous = self.cache.pop(key, None)
        if previous:
            self.cache_bytes -= previous.get("_size", 0)
        size = text_bytes(record.get("message_body"))
        size += sum(len(data) for data in record.get("_media", {}).values() if isinstance(data, bytes))
        if size > self.max_cache_bytes:
            record["message_body"] = None
            record["_media"] = {}
            record["content_status"] = "unavailable"
            record["content_status_reason"] = "event_exceeds_orgbot_memory_cache_limit"
            record["attachment_refs"] = []
            for attachment in record.get("attachments", []):
                attachment["available"] = False
                attachment["byte_length"] = None
                attachment["sha256"] = None
                attachment["reason"] = "event_exceeds_orgbot_memory_cache_limit"
                attachment.pop("_bytes", None)
            size = 0
        record["_size"] = size
        self.cache[key] = record
        self.cache_bytes += size
        while len(self.cache) > self.max_events or self.cache_bytes > self.max_cache_bytes:
            _, removed = self.cache.popitem(last=False)
            self.cache_evicted_rooms.add(removed["room_id"])
            self.cache_bytes -= removed.get("_size", 0)

    async def _fetch_attachment(self, event: Any, content: dict[str, Any]) -> dict[str, Any]:
        encrypted_file = content.get("file") if isinstance(content.get("file"), dict) else None
        mxc_uri = encrypted_file.get("url") if encrypted_file else content.get("url")
        original_name = safe_filename(content.get("body"))
        info = content.get("info") if isinstance(content.get("info"), dict) else {}
        declared_size = info.get("size")
        event_id = str(getattr(event, "event_id", "event"))
        digest_name = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:20]
        suffix = Path(original_name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
            suffix = ""
        archive_path = f"attachments/{digest_name}{suffix}"
        media_type = info.get("mimetype") if isinstance(info.get("mimetype"), str) else mimetypes.guess_type(original_name)[0]

        base = {
            "path": archive_path,
            "file_name": original_name,
            "media_type": media_type or "application/octet-stream",
            "byte_length": None,
            "sha256": None,
            "available": False,
            "reason": None,
            "encrypted_at_rest_in_matrix": bool(encrypted_file),
        }
        if not isinstance(mxc_uri, str) or not mxc_uri.startswith("mxc://"):
            base["reason"] = "missing_or_invalid_mxc_reference"
            return base
        if isinstance(declared_size, int) and declared_size > self.max_attachment_bytes:
            base["reason"] = "declared_attachment_size_exceeds_orgbot_limit"
            return base

        try:
            response = await self.client.download(mxc_uri)
            media = getattr(response, "body", None)
            if not isinstance(media, bytes):
                base["reason"] = "matrix_nio_media_download_unavailable"
                return base
            if encrypted_file:
                media = self._decrypt_attachment(media, encrypted_file)
            if len(media) > self.max_attachment_bytes:
                base["reason"] = "attachment_size_exceeds_orgbot_limit"
                return base
            base.update(
                {
                    "byte_length": len(media),
                    "sha256": hashlib.sha256(media).hexdigest(),
                    "available": True,
                    "reason": None,
                    "_bytes": media,
                }
            )
            return base
        except Exception as exc:  # noqa: BLE001 - payloads and SDK details stay out of logs
            base["reason"] = "matrix_nio_attachment_decryption_failed" if encrypted_file else "matrix_nio_media_download_failed"
            LOG.warning("Attachment processing failed (%s)", type(exc).__name__)
            return base

    @staticmethod
    def _decrypt_attachment(ciphertext: bytes, encrypted_file: dict[str, Any]) -> bytes:
        module = importlib.import_module("nio.crypto.attachments")
        decrypt = getattr(module, "decrypt_attachment")
        key_info = encrypted_file.get("key")
        if isinstance(key_info, dict):
            key_info = key_info.get("k")
        values = {
            "ciphertext": ciphertext,
            "data": ciphertext,
            "content": ciphertext,
            "key": key_info,
            "hash": (encrypted_file.get("hashes") or {}).get("sha256")
            if isinstance(encrypted_file.get("hashes"), dict)
            else None,
            "iv": encrypted_file.get("iv"),
            "hashes": encrypted_file.get("hashes"),
            "version": encrypted_file.get("v"),
            "file": encrypted_file,
            "encrypted_file": encrypted_file,
        }
        signature = inspect.signature(decrypt)
        params = [
            p for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        ]
        kwargs: dict[str, Any] = {}
        positional: list[Any] = []
        for index, param in enumerate(params):
            if param.name in values:
                value = values[param.name]
            elif index == 0:
                value = ciphertext
            elif len(params) == 2 and index == 1:
                value = encrypted_file
            else:
                raise RuntimeError("unsupported_matrix_nio_attachment_decrypt_signature")
            if param.kind == param.POSITIONAL_ONLY:
                positional.append(value)
            else:
                kwargs[param.name] = value
        result = decrypt(*positional, **kwargs)
        if not isinstance(result, bytes):
            raise RuntimeError("matrix_nio_attachment_decrypt_returned_non_bytes")
        return result

    def _load_room_map(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        try:
            data = json.loads(self.room_map_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, "provisioner_room_map_missing"
        except (OSError, json.JSONDecodeError):
            return {}, "provisioner_room_map_unreadable"
        rooms = data.get("rooms") if isinstance(data, dict) else None
        if not isinstance(rooms, list):
            return {}, "provisioner_room_map_invalid"
        result = {}
        for room in rooms:
            if not isinstance(room, dict) or not isinstance(room.get("room_id"), str):
                continue
            result[room["room_id"]] = room
        return result, None

    def _load_direct_room_authorization(
        self,
    ) -> tuple[dict[str, dict[str, Any]], set[str], str | None]:
        registry_ids: set[str] = set()
        try:
            registry = json.loads(self.direct_room_registry_path.read_text(encoding="utf-8"))
            raw_rooms = registry.get("rooms") if isinstance(registry, dict) else None
            if isinstance(raw_rooms, list):
                registry_ids = {
                    room["room_id"] for room in raw_rooms
                    if isinstance(room, dict) and isinstance(room.get("room_id"), str)
                }
            chat_policy = json.loads(self.chat_policy_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, registry_ids, "direct_room_snapshot_missing"
        except (OSError, json.JSONDecodeError):
            return {}, registry_ids, "direct_room_snapshot_unreadable"

        try:
            authorized, registered_ids = authorize_direct_rooms(
                registry,
                chat_policy,
                self.user_id,
            )
            return authorized, registered_ids, None
        except DirectRoomPolicyError as exc:
            return {}, registry_ids, str(exc)

    def _purge_room_cache(self, room_id: str) -> None:
        for key, record in list(self.cache.items()):
            if key[0] == room_id:
                self.cache.pop(key, None)
                self.cache_bytes -= record.get("_size", 0)

    def _refresh_direct_room_authorization(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        authorized, registry_ids, error = self._load_direct_room_authorization()
        self.direct_room_registry_ids.update(registry_ids)
        self.authorized_direct_rooms = authorized
        for room_id in self.direct_room_registry_ids:
            if error is None and room_id not in authorized:
                self._purge_room_cache(room_id)
                self.pending_direct_invites.pop(room_id, None)
        return authorized, error

    async def _process_direct_invites(
        self,
        response: Any,
        authorized: dict[str, dict[str, Any]],
        policy_available: bool = True,
    ) -> None:
        if not policy_available:
            # Snapshot loss is not evidence of revoked membership. Keep the
            # bot joined and retain its cache, but do no room processing,
            # invitation acceptance, or export-authorizing work until fresh
            # policy returns.
            return
        sync_rooms = getattr(response, "rooms", None)
        invite_rooms = getattr(sync_rooms, "invite", {})
        if isinstance(invite_rooms, dict):
            for room_id, invite in invite_rooms.items():
                inviter = invite_sender(invite, self.user_id)
                if room_id in self.direct_room_registry_ids and inviter:
                    self.pending_direct_invites[room_id] = inviter

        # Incremental sync need not repeat an invite received before the guard
        # published its registry entry. nio retains these MatrixInvitedRoom
        # objects; retry them only after exact fresh registry authorization.
        retained_invites = getattr(self.client, "invited_rooms", {})
        if isinstance(retained_invites, dict):
            for room_id, invited_room in retained_invites.items():
                inviter = getattr(invited_room, "inviter", None)
                if (room_id in self.direct_room_registry_ids
                        and getattr(invited_room, "room_id", None) == room_id
                        and getattr(invited_room, "own_user_id", None) == self.user_id
                        and isinstance(inviter, str) and inviter):
                    self.pending_direct_invites[room_id] = inviter

        # Re-check already joined direct rooms after every successful sync.
        # Leave if the fresh policy no longer grants the pair or SDK state no
        # longer confirms E2EE; the homeserver guard also revokes membership.
        for room_id in self.direct_room_registry_ids:
            joined_room = self.client.rooms.get(room_id)
            if joined_room is None:
                continue
            policy = authorized.get(room_id)
            if policy is not None and policy.get("status") == "policy_unavailable":
                continue
            encrypted, _algorithm = room_encryption(joined_room)
            if policy is None or encrypted is False:
                self._purge_room_cache(room_id)
                try:
                    await self.client.room_leave(room_id)
                except Exception as exc:  # noqa: BLE001 - no room/event data in logs
                    LOG.warning("Unauthorized direct-room leave failed (%s)", type(exc).__name__)
                self.pending_direct_invites.pop(room_id, None)
                self.direct_join_attempt_at.pop(room_id, None)

        now = asyncio.get_running_loop().time()
        for room_id, inviter in list(self.pending_direct_invites.items()):
            policy = authorized.get(room_id)
            if policy is None or inviter not in policy["participants"]:
                self.pending_direct_invites.pop(room_id, None)
                self.direct_join_attempt_at.pop(room_id, None)
                continue
            if policy.get("status") == "policy_unavailable":
                continue

            joined_room = self.client.rooms.get(room_id)
            if joined_room is not None:
                encrypted, algorithm = room_encryption(joined_room)
                if encrypted is False:
                    try:
                        await self.client.room_leave(room_id)
                    except Exception as exc:  # noqa: BLE001 - no room/event data in logs
                        LOG.warning("Unencrypted direct-room leave failed (%s)", type(exc).__name__)
                    self.pending_direct_invites.pop(room_id, None)
                    self.direct_join_attempt_at.pop(room_id, None)
                elif (
                    policy.get("status") == "ready"
                    and encrypted is True
                    and isinstance(algorithm, str)
                    and algorithm
                ):
                    self.pending_direct_invites.pop(room_id, None)
                    self.direct_join_attempt_at.pop(room_id, None)
                continue

            last_attempt = self.direct_join_attempt_at.get(room_id)
            if last_attempt is not None and now - last_attempt < 5.0:
                continue
            self.direct_join_attempt_at[room_id] = now
            try:
                result = await self.client.join(room_id)
            except Exception as exc:  # noqa: BLE001 - no request/event data in logs
                LOG.warning("Authorized direct-room join failed (%s)", type(exc).__name__)
                continue
            if getattr(result, "room_id", None) == room_id:
                LOG.info("Authorized direct-room join was accepted; awaiting encrypted joined state")
            else:
                LOG.warning("Authorized direct-room join returned %s", type(result).__name__)

    def _snapshot_room_events(self, room_ids: list[str]) -> tuple[list[dict[str, Any]], bool]:
        records = []
        evicted_or_capped = any(room_id in self.cache_evicted_rooms for room_id in room_ids)
        for room_id in room_ids:
            room_records = [r for (rid, _), r in self.cache.items() if rid == room_id]
            room_records.sort(key=lambda item: (item.get("origin_server_ts") or -1, item.get("event_id") or ""))
            if len(room_records) > self.max_export_events:
                evicted_or_capped = True
                room_records = room_records[-self.max_export_events :]
            records.extend(room_records)
        records.sort(key=lambda item: (item.get("origin_server_ts") or -1, item.get("room_id", ""), item.get("event_id", "")))
        return records, evicted_or_capped

    async def export_rooms(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not getattr(self, "device_keys_verified", False):
            return web.json_response({"error": "orgbot_device_keys_unverified"}, status=503)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - parser exception messages can include request data
            return web.json_response({"error": "invalid_json"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"error": "invalid_request"}, status=400)
        request_id = payload.get("request_id")
        requested_by = payload.get("requested_by")
        scope = payload.get("scope")
        room_ids = payload.get("room_ids")
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            return web.json_response({"error": "invalid_request_id"}, status=400)
        if not isinstance(requested_by, str) or not requested_by or len(requested_by) > 255:
            return web.json_response({"error": "invalid_requested_by"}, status=400)
        if scope not in {"all", "selected"}:
            return web.json_response({"error": "invalid_scope"}, status=400)
        if not isinstance(room_ids, list) or not room_ids or len(room_ids) > 100:
            return web.json_response({"error": "invalid_room_ids"}, status=400)
        if any(not isinstance(room_id, str) or not ROOM_ID_RE.fullmatch(room_id) for room_id in room_ids):
            return web.json_response({"error": "invalid_room_id"}, status=400)
        exact_room_ids = sorted(set(room_ids))
        if len(exact_room_ids) != len(room_ids):
            return web.json_response({"error": "duplicate_room_ids"}, status=400)

        room_map, map_error = self._load_room_map()
        direct_room_map, _direct_error = self._refresh_direct_room_authorization()
        for direct_room_id in self.direct_room_registry_ids:
            if direct_room_id not in direct_room_map or direct_room_map[direct_room_id]["status"] != "ready":
                room_map.pop(direct_room_id, None)
        room_map.update({
            room_id: metadata
            for room_id, metadata in direct_room_map.items()
            if metadata["status"] == "ready"
        })
        unregistered = [room_id for room_id in exact_room_ids if room_id not in room_map]
        if unregistered:
            return web.json_response({"error": "room_not_in_current_export_policy"}, status=403)
        not_joined = [room_id for room_id in exact_room_ids if room_id not in self.client.rooms]
        if not_joined:
            return web.json_response({"error": "bot_not_joined_to_requested_room"}, status=403)
        for room_id in exact_room_ids:
            encrypted, algorithm = room_encryption(self.client.rooms[room_id])
            if encrypted is not True or not isinstance(algorithm, str) or not algorithm:
                return web.json_response({"error": "room_not_confirmed_encrypted_by_sdk"}, status=403)

        events, capped = self._snapshot_room_events(exact_room_ids)
        reasons = ["history_before_process_start_not_available"]
        if map_error:
            reasons.append(map_error)
        if capped:
            reasons.append("event_cache_eviction_or_export_limit_may_have_omitted_events")
        if any(event.get("content_status") in {"undecrypted", "partial", "unavailable"} for event in events):
            reasons.append("one_or_more_events_or_attachments_unavailable")

        for event in events:
            room_info = room_map.get(event["room_id"], {})
            event["room_name"] = room_info.get("name") or event.get("room_name")
            event["classification"] = room_info.get("classification") or "unknown"
            event["authorization_basis"] = room_info.get("authorization_basis")
            if not room_info:
                event["content_status_reason"] = event.get("content_status_reason") or "classification_and_room_metadata_unavailable"

        completeness = "partial"
        manifest = {
            "format_version": EXPORT_FORMAT,
            "request_id": request_id,
            "requested_by": requested_by,
            "scope": scope,
            "requested_room_ids": exact_room_ids,
            "included_room_ids": exact_room_ids,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completeness": completeness,
            "incompleteness_reasons": reasons,
            "source": {
                "matrix_user_id": self.user_id,
                "device_id": self.device_id,
                "client_sdk": "matrix-nio",
                "homeserver_decryption_claim": False,
            },
            "history_coverage": {
                "status": "partial",
                "available_from": self.started_at,
                "boundary": "events yielded to this running OrgBot process and retained in its bounded in-memory cache",
                "complete": False,
                "reason": "No historical backfill is performed. Events before this process started, events omitted by Matrix sync pagination, cache evictions, and events not delivered to this device cannot be proven absent.",
            },
            "rooms": [],
            "encrypted_rooms": {},
            "attachments": [],
        }
        for room_id in exact_room_ids:
            room = self.client.rooms[room_id]
            room_info = room_map.get(room_id, {})
            encrypted, algorithm = room_encryption(room)
            room_events = [event for event in events if event["room_id"] == room_id]
            manifest["rooms"].append(
                {
                    "room_id": room_id,
                    "room_name": room_info.get("name") or getattr(room, "display_name", None) or getattr(room, "name", None),
                    "classification": room_info.get("classification") or "unknown",
                    "authorization_basis": room_info.get("authorization_basis"),
                    "encrypted": encrypted,
                    "algorithm": algorithm,
                    "decrypted_event_count": sum(e["encryption"]["decryption_status"] == "decrypted_by_matrix_nio_device" for e in room_events),
                    "undecrypted_event_count": sum(e["content_status"] == "undecrypted" for e in room_events),
                    "history_status": "partial",
                    "history_reason": "history_before_process_start_not_available",
                }
            )
            manifest["encrypted_rooms"][room_id] = {
                "algorithm": algorithm or "m.megolm.v1.aes-sha2",
                "decryption_status": "partial" if room_events else "unknown",
                "key_coverage": "unknown",
                "decrypted_by": {"user_id": self.user_id, "device_id": self.device_id},
            }

        ndjson_lines: list[str] = []
        media: dict[str, bytes] = {}
        for cached in events:
            encryption = cached.get("encryption", {})
            record = {
                "record_type": "message",
                "event_id": cached.get("event_id"),
                "room_id": cached.get("room_id"),
                "room_name": cached.get("room_name"),
                "classification": cached.get("classification"),
                "authorization_basis": cached.get("authorization_basis"),
                "user_id": cached.get("matrix_user_id"),
                "display_name": cached.get("display_name"),
                "timestamp": cached.get("origin_server_ts"),
                "timestamp_utc": cached.get("timestamp"),
                "type": "m.room.message",
                "message_type": cached.get("message_type"),
                "content_status": cached.get("content_status", "unknown"),
                "content_status_reason": cached.get("content_status_reason"),
                "body": cached.get("message_body"),
                "encryption_provenance": {
                    "encrypted": encryption.get("encrypted") is True,
                    "algorithm": encryption.get("algorithm") or "m.megolm.v1.aes-sha2",
                    "decryption_status": "decrypted" if encryption.get("decryption_status") == "decrypted_by_matrix_nio_device" else "undecrypted",
                    "key_coverage": "unknown",
                    "decrypted_by_user_id": self.user_id,
                    "decrypted_by_device_id": self.device_id,
                },
                "attachment_paths": [],
                "attachments": [],
            }
            for attachment in cached.get("attachments", []):
                metadata = {
                    key: value for key, value in attachment.items()
                    if key not in {"available", "_bytes"} and not key.startswith("_")
                }
                metadata["availability"] = "available" if attachment.get("available") else "unavailable"
                record["attachments"].append(metadata)
                manifest["attachments"].append(
                    {
                        "event_id": record["event_id"],
                        "room_id": record["room_id"],
                        **metadata,
                    }
                )
                file_bytes = cached.get("_media", {}).get(attachment.get("path"))
                if attachment.get("available") and isinstance(file_bytes, bytes):
                    media[attachment["path"]] = file_bytes
                    record["attachment_paths"].append(attachment["path"])
                elif attachment.get("path"):
                    record["attachment_paths"].append(attachment["path"])
            ndjson_lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("messages.ndjson", "\n".join(ndjson_lines) + ("\n" if ndjson_lines else ""))
            for path, data in media.items():
                archive.writestr(path, data)
        body = zip_buffer.getvalue()
        return web.Response(
            body=body,
            content_type="application/zip",
            headers={
                "X-Export-Format": EXPORT_FORMAT,
                "X-Export-Room-Ids": ",".join(exact_room_ids),
                "X-Export-Completeness": completeness,
                "Cache-Control": "no-store",
            },
        )

    def _expected_device_keys(self) -> dict[str, str]:
        olm = getattr(self.client, "olm", None)
        account = getattr(olm, "account", None)
        identity_keys = getattr(account, "identity_keys", None)
        if not isinstance(identity_keys, dict):
            raise RuntimeError("Matrix E2EE device identity is unavailable")
        ed25519 = identity_keys.get("ed25519")
        curve25519 = identity_keys.get("curve25519")
        if not all(isinstance(value, str) and value for value in (ed25519, curve25519)):
            raise RuntimeError("Matrix E2EE device identity is invalid")
        return {
            f"ed25519:{self.device_id}": ed25519,
            f"curve25519:{self.device_id}": curve25519,
        }

    async def _query_own_device_keys(self) -> dict[str, Any] | None:
        olm = getattr(self.client, "olm", None)
        query_users = getattr(olm, "users_for_key_query", None)
        if not isinstance(query_users, set):
            raise RuntimeError("Matrix E2EE key-query state is unavailable")
        query_users.add(self.user_id)
        try:
            response = await self.client.keys_query()
        finally:
            query_users.discard(self.user_id)
        if not isinstance(response, nio.KeysQueryResponse):
            raise RuntimeError("Matrix E2EE device-key query failed")
        failures = getattr(response, "failures", {})
        if isinstance(failures, dict) and self.user_id in failures:
            raise RuntimeError("Matrix E2EE device-key query failed for OrgBot")
        users = getattr(response, "device_keys", None)
        devices = users.get(self.user_id) if isinstance(users, dict) else None
        if devices is None:
            return None
        if not isinstance(devices, dict):
            raise RuntimeError("Matrix E2EE device-key query returned invalid data")
        payload = devices.get(self.device_id)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise RuntimeError("Matrix E2EE device-key query returned invalid data")
        return payload

    def _device_key_payload_matches(self, payload: dict[str, Any]) -> bool:
        if payload.get("user_id") != self.user_id or payload.get("device_id") != self.device_id:
            return False
        keys = payload.get("keys")
        expected = self._expected_device_keys()
        return isinstance(keys, dict) and all(keys.get(key_id) == value for key_id, value in expected.items())

    async def _verify_and_republish_device_keys(self) -> None:
        """Verify this persisted device's public keys, republishing only if absent.

        matrix-nio persists whether device keys were once uploaded. That local
        flag can outlive the server-side device-key record, so presence on the
        homeserver is verified against the public identity derived from the
        persistent Olm account before OrgBot can report ready.
        """
        self.device_keys_verified = False
        payload = await self._query_own_device_keys()
        if payload is not None:
            if not self._device_key_payload_matches(payload):
                raise RuntimeError("Matrix E2EE server device keys do not match the persistent OrgBot device")
        else:
            olm = getattr(self.client, "olm", None)
            account = getattr(olm, "account", None)
            if account is None or not hasattr(account, "shared"):
                raise RuntimeError("Matrix E2EE account state is unavailable for device-key recovery")
            # nio only includes device_keys in keys/upload when `shared` is
            # false. A restored true value is not proof that the server still
            # has them; force the same persisted identity through nio's public
            # upload path, then verify the server response independently.
            account.shared = False
            save_account = getattr(olm, "save_account", None)
            if callable(save_account):
                save_account()
            upload = await self.client.keys_upload()
            if not isinstance(upload, nio.KeysUploadResponse):
                raise RuntimeError("Matrix E2EE device-key republish failed")
            payload = await self._query_own_device_keys()
            if payload is None or not self._device_key_payload_matches(payload):
                raise RuntimeError("Matrix E2EE device keys are still absent or mismatched after republish")
        self.device_keys_verified = True
        self.device_keys_verified_at = asyncio.get_running_loop().time()

    async def _maintain_e2ee(self) -> None:
        """Perform the crypto traffic normally handled by nio.sync_forever.

        Manual sync alone does not publish this device's public/one-time keys.
        Fail readiness on protocol errors instead of claiming an E2EE receiver
        is ready without the required key upload and pending to-device traffic.
        """
        loop = asyncio.get_running_loop()
        verified_at = getattr(self, "device_keys_verified_at", None)
        verification_due = (
            not getattr(self, "device_keys_verified", False)
            or verified_at is None
            or loop.time() - verified_at >= getattr(self, "device_key_check_interval", 300.0)
        )
        if getattr(self.client, "olm", None) is not None and verification_due:
            await self._verify_and_republish_device_keys()
        if self.client.should_upload_keys:
            response = await self.client.keys_upload()
            if not isinstance(response, nio.KeysUploadResponse):
                raise RuntimeError("Matrix E2EE key upload failed")
        if self.client.should_query_keys:
            response = await self.client.keys_query()
            if not isinstance(response, nio.KeysQueryResponse):
                raise RuntimeError("Matrix E2EE key query failed")
        if self.client.should_claim_keys:
            response = await self.client.keys_claim(self.client.get_users_for_key_claiming())
            if not isinstance(response, nio.KeysClaimResponse):
                raise RuntimeError("Matrix E2EE key claim failed")
        responses = await self.client.send_to_device_messages()
        if any(not isinstance(response, nio.ToDeviceResponse) for response in responses):
            raise RuntimeError("Matrix E2EE to-device delivery failed")

    async def _verify_session_identity(self, session_data: dict[str, Any]) -> None:
        """Check the provisioned token belongs to this device and is not admin."""
        access_token = session_data["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}
        whoami_url = f"{self.homeserver}/_matrix/client/v3/account/whoami"
        admin_url = (
            f"{self.homeserver}/_synapse/admin/v1/users/"
            f"{quote(self.user_id, safe='')}/joined_rooms"
        )
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                async with session.get(whoami_url, headers=headers, allow_redirects=False) as response:
                    if response.status != 200:
                        raise RuntimeError("OrgBot session failed Matrix whoami verification")
                    whoami = await response.json(content_type=None)
                if (
                    not isinstance(whoami, dict)
                    or whoami.get("user_id") != self.user_id
                    or whoami.get("device_id") != self.device_id
                ):
                    raise RuntimeError("OrgBot session user/device identity does not match configuration")

                async with session.get(admin_url, headers=headers, allow_redirects=False) as response:
                    if response.status != 403:
                        raise RuntimeError("OrgBot session did not prove non-admin access")
                    denial = await response.json(content_type=None)
                    if not isinstance(denial, dict) or denial.get("errcode") != "M_FORBIDDEN":
                        raise RuntimeError("OrgBot session did not prove non-admin access")
        except asyncio.CancelledError:
            raise
        except RuntimeError:
            raise
        except Exception:
            # Do not include HTTP bodies, credentials, or transport diagnostics.
            raise RuntimeError("OrgBot session identity verification could not be completed") from None

    def _restore_authenticated_session(self, login: Any) -> None:
        if not isinstance(login, dict):
            raise RuntimeError("OrgBot Matrix session has an invalid identity response")
        access_token = login.get("access_token")
        returned_user_id = login.get("user_id")
        returned_device_id = login.get("device_id")
        if (
            not isinstance(access_token, str)
            or not access_token
            or returned_user_id != self.user_id
            or returned_device_id != self.device_id
        ):
            raise RuntimeError("OrgBot Matrix session identity does not match its configured user and device")
        self.client.restore_login(returned_user_id, returned_device_id, access_token)

    async def sync_loop(self) -> None:
        first_sync = True
        delay = 2
        while not self.stop_event.is_set():
            try:
                sync_options = {"timeout": 0 if first_sync else 30000, "full_state": first_sync}
                if first_sync:
                    # Room/invite objects are not persisted by nio. Rebuild
                    # current state on restart without replaying old timelines;
                    # an old since-token could omit an outstanding invitation.
                    self.client.next_batch = None
                    self.client.loaded_sync_token = None
                    sync_options["sync_filter"] = {"room": {"timeline": {"limit": 0}}}
                response = await self.client.sync(**sync_options)
                if isinstance(response, SyncError):
                    self.ready = False
                    LOG.warning("Matrix sync returned an error; retrying")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                await self._maintain_e2ee()
                if not getattr(self, "device_keys_verified", False):
                    raise RuntimeError("Matrix E2EE device keys are not verified")
                first_sync = False
                delay = 2
                self.last_sync_at = datetime.now(timezone.utc)
                self.ready = True
                authorized_direct_rooms, direct_policy_error = self._refresh_direct_room_authorization()
                await self._process_direct_invites(
                    response,
                    authorized_direct_rooms,
                    policy_available=direct_policy_error is None,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - don't log payloads or credentials
                self.ready = False
                LOG.warning("Matrix sync failed (%s); retrying", type(exc).__name__)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def run(self) -> None:
        session_data = read_session_file(self.session_file, self.user_id, self.device_id)
        await self._verify_session_identity(session_data)
        self._restore_authenticated_session(session_data)
        await self._maintain_e2ee()
        if not self.device_keys_verified:
            raise RuntimeError("Matrix E2EE device keys are not verified")
        LOG.info("Matrix client authenticated; E2EE device store is persistent")

        app = web.Application(client_max_size=64 * 1024)
        app.add_routes(
            [
                web.get("/healthz", self.health),
                web.get("/v1/rooms/{room_id}/readiness", self.room_readiness),
                web.post("/v1/rooms/export", self.export_rooms),
            ]
        )
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.http_port)
        await site.start()
        LOG.info("Internal health/export API is listening")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except NotImplementedError:
                pass

        sync_task = asyncio.create_task(self.sync_loop(), name="matrix-sync")
        try:
            await self.stop_event.wait()
        finally:
            self.ready = False
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)
            await runner.cleanup()
            await self.client.close()


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bot = OrgBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
