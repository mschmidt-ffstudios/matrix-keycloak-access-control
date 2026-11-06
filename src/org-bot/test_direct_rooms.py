#!/usr/bin/env python3
"""Offline checks for direct-room authorization and OrgBot ZIP output."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import AsyncMock, Mock, patch
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


try:
    import nio  # noqa: F401
    from nio.crypto.attachments import encrypt_attachment
except ImportError:
    encrypt_attachment = None
    # The policy and ZIP tests use a fake Matrix client and need no SDK or server.
    stub = types.ModuleType("nio")
    stub.AsyncClient = type("AsyncClient", (), {})
    stub.AsyncClientConfig = type("AsyncClientConfig", (), {})
    stub.SyncError = type("SyncError", (), {})
    sys.modules["nio"] = stub

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("orgbot_for_offline_tests", Path(__file__).with_name("orgbot.py"))
assert SPEC and SPEC.loader
orgbot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(orgbot)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BOT_ID = "@orgbot:example.test"
PAIR = ["@alice:example.test", "@bob:example.test"]
ROOM_IDS = [f"!room{index}:example.test" for index in range(4)]


def synthetic_bot_device_payload():
    return {
        "user_id": BOT_ID,
        "device_id": "SYNTHETICDEVICE",
        "keys": {
            "ed25519:SYNTHETICDEVICE": "synthetic-ed25519-public",
            "curve25519:SYNTHETICDEVICE": "synthetic-curve25519-public",
        },
    }


def chat_policy(groups_by_user=None, expires_at="2026-01-01T00:00:30Z"):
    groups_by_user = groups_by_user or {user: ["group-1"] for user in PAIR}
    group_name = {"group-1": "1to1chat.case-review", "ordinary": "project-team"}
    users = []
    for user_id in PAIR:
        groups = []
        for group_id in groups_by_user.get(user_id, []):
            name = group_name.get(group_id, "1to1chat.other")
            groups.append({"id": group_id, "name": name, "path": f"/{name}"})
        users.append({"user_id": user_id, "direct_groups": groups})
    return {
        "schema_version": 1,
        "matrix_server_name": "example.test",
        "generated_at": "2026-01-01T00:00:00Z",
        "expires_at": expires_at,
        "users": users,
        "direct_chat": {"group_prefix": "1to1chat."},
        "service_user_ids": [BOT_ID],
        "admin_user_ids": [],
        "rooms": [],
    }


def room_registry(room_ids=None):
    if room_ids is None:
        room_ids = [ROOM_IDS[0]]
    return {
        "schema_version": 1,
        "matrix_server_name": "example.test",
        "rooms": [
            {
                "room_id": room_id,
                "participants": list(PAIR),
                "bot_user_id": BOT_ID,
                "encrypted": True,
                "status": "ready",
                "name": "Policy direct chat",
                "minimum_clearance": "unclassified_direct_chat",
                "minimum_clearance_rank": 0,
            }
            for room_id in room_ids
        ],
    }


class RoomReadinessTests(unittest.TestCase):
    def make_bot(self, room, authorized, error=None):
        bot = object.__new__(orgbot.OrgBot)
        bot.user_id = BOT_ID
        bot.device_id = "ORGBOTDEVICE"
        bot.export_token = "synthetic-readiness-token-value"
        bot.ready = True
        bot.device_keys_verified = True
        bot.last_sync_at = datetime.now(timezone.utc)
        bot.sync_stale_seconds = 90
        bot.direct_room_registry_ids = {ROOM_IDS[0]}
        bot.client = SimpleNamespace(rooms={ROOM_IDS[0]: room})
        bot._load_room_map = lambda: ({}, None)
        bot._refresh_direct_room_authorization = lambda: (authorized, error)
        return bot

    def request(self):
        return SimpleNamespace(
            headers={"Authorization": "Bearer synthetic-readiness-token-value"},
            match_info={"room_id": ROOM_IDS[0]},
        )

    def room(self, users):
        return SimpleNamespace(room_id=ROOM_IDS[0], encrypted=True, users=users)

    def test_cached_revoked_direct_room_is_not_ready(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=False)})
        bot = self.make_bot(room, {}, None)
        response = asyncio.run(bot.room_readiness(self.request()))
        self.assertEqual(response.status, 503)
        self.assertIn("direct_room_not_currently_authorized", response.text)

    def test_unknown_cached_room_is_not_ready_when_registry_is_missing(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=False)})
        bot = self.make_bot(room, {}, "direct_room_snapshot_missing")
        bot.direct_room_registry_ids = set()
        response = asyncio.run(bot.room_readiness(self.request()))
        self.assertEqual(response.status, 503)
        self.assertIn("direct_room_not_currently_authorized", response.text)

    def test_direct_room_policy_unavailable_is_not_ready(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=False)})
        bot = self.make_bot(room, {}, "chat_policy_expired")
        response = asyncio.run(bot.room_readiness(self.request()))
        self.assertEqual(response.status, 503)
        self.assertIn("direct_room_not_currently_authorized", response.text)

    def test_cached_room_without_current_bot_membership_is_not_ready(self):
        room = self.room({})
        bot = self.make_bot(room, {ROOM_IDS[0]: {"status": "ready"}})
        response = asyncio.run(bot.room_readiness(self.request()))
        self.assertEqual(response.status, 503)
        self.assertIn("matrix_sync_does_not_confirm_bot_membership", response.text)

    def test_invitation_is_not_reported_as_joined_readiness(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=True)})
        bot = self.make_bot(room, {ROOM_IDS[0]: {"status": "ready"}})
        response = asyncio.run(bot.room_readiness(self.request()))
        self.assertEqual(response.status, 503)

    def test_currently_authorized_joined_encrypted_room_returns_safe_readiness(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=False)})
        bot = self.make_bot(room, {ROOM_IDS[0]: {"status": "ready"}})
        response = asyncio.run(bot.room_readiness(self.request()))
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["room_id"], ROOM_IDS[0])
        self.assertIs(payload["encrypted"], True)
        self.assertEqual(payload["bot_user_id"], BOT_ID)
        self.assertEqual(payload["bot_device_id"], "ORGBOTDEVICE")
        self.assertNotIn("body", payload)
        self.assertNotIn("keys", payload)

    def test_sync_health_requires_verified_server_device_keys(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=False)})
        bot = self.make_bot(room, {ROOM_IDS[0]: {"status": "ready"}})
        bot.device_keys_verified = False
        response = asyncio.run(bot.health(self.request()))
        self.assertEqual(response.status, 503)
        self.assertIn('"status": "starting"', response.text)

    def test_known_managed_group_room_uses_sdk_readiness_without_dm_snapshot(self):
        room = self.room({BOT_ID: SimpleNamespace(invited=False)})
        bot = self.make_bot(room, {}, "chat_policy_expired")
        bot.direct_room_registry_ids = set()
        bot._load_room_map = lambda: ({ROOM_IDS[0]: {"classification": "basis"}}, None)
        response = asyncio.run(bot.room_readiness(self.request()))
        self.assertEqual(response.status, 200)


class DirectRoomPolicyTests(unittest.TestCase):
    def test_active_room_requires_exact_shared_prefixed_group_id(self):
        allowed, ids = orgbot.authorize_direct_rooms(room_registry(), chat_policy(), BOT_ID, NOW)
        self.assertEqual(set(allowed), {ROOM_IDS[0]})
        self.assertEqual(ids, {ROOM_IDS[0]})
        self.assertEqual(allowed[ROOM_IDS[0]]["classification"], "unclassified_direct_chat")

    def test_pending_bot_registry_entry_allows_only_sdk_confirmed_e2ee_capture(self):
        registry = room_registry()
        registry["rooms"][0]["status"] = "pending_bot"
        allowed, _ = orgbot.authorize_direct_rooms(registry, chat_policy(), BOT_ID, NOW)
        self.assertEqual(allowed[ROOM_IDS[0]]["status"], "pending_bot")
        bot = object.__new__(orgbot.OrgBot)
        bot.direct_room_registry_ids = {ROOM_IDS[0]}
        bot.authorized_direct_rooms = allowed
        self.assertTrue(bot._direct_room_capture_allowed(ROOM_IDS[0], SimpleNamespace(encrypted=True)))
        self.assertFalse(bot._direct_room_capture_allowed(ROOM_IDS[0], SimpleNamespace(encrypted=False)))

    def test_policy_unavailable_registry_is_retained_but_not_authorized_or_captured(self):
        registry = room_registry()
        registry["rooms"][0]["status"] = "policy_unavailable"
        allowed, registered = orgbot.authorize_direct_rooms(registry, chat_policy(), BOT_ID, NOW)
        self.assertEqual(allowed[ROOM_IDS[0]]["status"], "policy_unavailable")
        self.assertEqual(registered, {ROOM_IDS[0]})
        bot = object.__new__(orgbot.OrgBot)
        bot.direct_room_registry_ids = registered
        bot.authorized_direct_rooms = allowed
        self.assertFalse(bot._direct_room_capture_allowed(ROOM_IDS[0], SimpleNamespace(encrypted=True)))

    def test_ordinary_group_does_not_authorize_pair(self):
        users = chat_policy({PAIR[0]: ["ordinary"], PAIR[1]: ["ordinary"]})
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(allowed, {})

    def test_group_name_containing_prefix_is_not_enough(self):
        users = chat_policy()
        for user in users["users"]:
            user["direct_groups"][0]["name"] = "department.1to1chat.case-review"
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(allowed, {})

    def test_different_group_ids_with_same_display_name_do_not_match(self):
        users = chat_policy({PAIR[0]: ["group-1"], PAIR[1]: ["different-id"]})
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(allowed, {})

    def test_explicit_direct_membership_in_nested_group_is_allowed(self):
        users = chat_policy()
        for user in users["users"]:
            user["direct_groups"][0]["path"] = "/department/1to1chat.case-review"
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(set(allowed), {ROOM_IDS[0]})

    def test_group_path_is_optional_metadata(self):
        users = chat_policy()
        for user in users["users"]:
            user["direct_groups"][0].pop("path")
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(set(allowed), {ROOM_IDS[0]})

    def test_ancestor_membership_is_not_inferred_when_not_listed(self):
        users = chat_policy({PAIR[0]: [], PAIR[1]: []})
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(allowed, {})

    def test_same_group_id_allows_name_rename_between_user_records(self):
        users = chat_policy()
        users["users"][1]["direct_groups"][0]["name"] = "1to1chat.case-renamed"
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(set(allowed), {ROOM_IDS[0]})
        self.assertEqual(allowed[ROOM_IDS[0]]["shared_direct_group_ids"], ["group-1"])

    def test_configured_prefix_must_end_with_dot_and_matches_group_name(self):
        users = chat_policy()
        users["direct_chat"]["group_prefix"] = "dm-scope."
        for user in users["users"]:
            user["direct_groups"][0]["name"] = "dm-scope.case-review"
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(set(allowed), {ROOM_IDS[0]})

    def test_expired_snapshot_disables_all_direct_rooms(self):
        users = chat_policy(expires_at="2025-12-31T23:59:00Z")
        users["generated_at"] = "2025-12-31T23:58:00Z"
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "chat_policy_expired"):
            orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)

    def test_policy_ttl_must_not_exceed_sixty_seconds(self):
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "chat_policy_ttl_invalid"):
            orgbot.authorize_direct_rooms(
                room_registry(), chat_policy(expires_at="2026-01-01T00:01:01Z"), BOT_ID, NOW
            )

    def test_future_snapshot_is_rejected_without_clock_skew(self):
        users = chat_policy()
        users["generated_at"] = "2026-01-01T00:00:01Z"
        users["expires_at"] = "2026-01-01T00:00:30Z"
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "chat_policy_generated_in_future"):
            orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)

    def test_disabled_user_does_not_authorize_pair(self):
        users = chat_policy()
        users["users"][1]["enabled"] = False
        allowed, _ = orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)
        self.assertEqual(allowed, {})

    def test_duplicate_room_ids_fail_closed(self):
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "duplicate"):
            orgbot.authorize_direct_rooms(room_registry([ROOM_IDS[0], ROOM_IDS[0]]), chat_policy(), BOT_ID, NOW)

    def test_duplicate_user_ids_fail_closed(self):
        users = chat_policy()
        users["users"].append(dict(users["users"][0]))
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "user_id_invalid_or_duplicate"):
            orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)

    def test_duplicate_direct_group_ids_fail_closed(self):
        users = chat_policy()
        users["users"][0]["direct_groups"].append(dict(users["users"][0]["direct_groups"][0]))
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "duplicate_direct_group_id"):
            orgbot.authorize_direct_rooms(room_registry(), users, BOT_ID, NOW)

    def test_invalid_registry_status_fails_closed(self):
        registry = room_registry()
        registry["rooms"][0]["status"] = "revoked"
        with self.assertRaisesRegex(orgbot.DirectRoomPolicyError, "direct_room_status_invalid"):
            orgbot.authorize_direct_rooms(registry, chat_policy(), BOT_ID, NOW)

    def test_service_or_admin_cannot_be_a_human_participant(self):
        registry = room_registry()
        registry["rooms"][0]["participants"] = [PAIR[0], BOT_ID]
        allowed, _ = orgbot.authorize_direct_rooms(registry, chat_policy(), BOT_ID, NOW)
        self.assertEqual(allowed, {})

    def test_invite_sender_requires_bot_targeted_member_invite(self):
        invite = {
            "invite_state": {
                "events": [{
                    "type": "m.room.member",
                    "state_key": BOT_ID,
                    "sender": PAIR[0],
                    "content": {"membership": "invite"},
                }]
            }
        }
        self.assertEqual(orgbot.invite_sender(invite, BOT_ID), PAIR[0])
        invite["invite_state"]["events"][0]["state_key"] = PAIR[1]
        self.assertIsNone(orgbot.invite_sender(invite, BOT_ID))

    def test_only_currently_authorized_invites_are_joined(self):
        async def exercise():
            bot = object.__new__(orgbot.OrgBot)
            bot.user_id = BOT_ID
            bot.direct_room_registry_ids = {ROOM_IDS[0], ROOM_IDS[1]}
            bot.pending_direct_invites = {}
            bot.direct_join_attempt_at = {}
            bot.client = SimpleNamespace(rooms={}, join_calls=[])

            async def join(room_id):
                bot.client.join_calls.append(room_id)
                return SimpleNamespace(room_id=room_id)

            bot.client.join = join
            valid_invite = {"invite_state": {"events": [{
                "type": "m.room.member", "state_key": BOT_ID, "sender": PAIR[0],
                "content": {"membership": "invite"},
            }]}}
            response = SimpleNamespace(rooms=SimpleNamespace(invite={
                ROOM_IDS[0]: valid_invite,
                ROOM_IDS[1]: valid_invite,
                "!arbitrary:example.test": valid_invite,
            }))
            authorized = {
                ROOM_IDS[0]: {"participants": PAIR, "status": "pending_bot"},
                ROOM_IDS[1]: {"participants": ["@mallory:example.test", PAIR[1]], "status": "ready"},
            }
            await bot._process_direct_invites(response, authorized)
            self.assertEqual(bot.client.join_calls, [ROOM_IDS[0]])

        asyncio.run(exercise())

    def test_policy_outage_preserves_bot_membership_and_cache_but_fresh_revocation_leaves(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                registry = room_registry()
                registry_path = root / "direct-rooms.json"
                registry_path.write_text(json.dumps(registry), encoding="utf-8")
                policy = chat_policy()
                current = datetime.now(timezone.utc)
                policy["generated_at"] = (current - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
                policy["expires_at"] = (current - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
                policy_path = root / "chat-policy.json"
                policy_path.write_text(json.dumps(policy), encoding="utf-8")

                bot = object.__new__(orgbot.OrgBot)
                bot.user_id = BOT_ID
                bot.direct_room_registry_path = registry_path
                bot.chat_policy_path = policy_path
                bot.direct_room_registry_ids = set()
                bot.authorized_direct_rooms = {}
                bot.pending_direct_invites = {}
                bot.direct_join_attempt_at = {}
                marker = {"event": "synthetic-cached-message", "_size": 17}
                bot.cache = OrderedDict({(ROOM_IDS[0], "$event"): marker})
                bot.cache_bytes = 17
                bot.client = SimpleNamespace(
                    rooms={ROOM_IDS[0]: SimpleNamespace(room_id=ROOM_IDS[0], encrypted=True)},
                    leave_calls=[],
                )

                async def room_leave(room_id):
                    bot.client.leave_calls.append(room_id)

                bot.client.room_leave = room_leave
                denied, error = bot._refresh_direct_room_authorization()
                self.assertEqual(denied, {})
                self.assertEqual(error, "chat_policy_expired")
                await bot._process_direct_invites(SimpleNamespace(rooms=SimpleNamespace(invite={})), denied,
                                                  policy_available=False)
                self.assertEqual(bot.client.leave_calls, [])
                self.assertIn((ROOM_IDS[0], "$event"), bot.cache)
                self.assertIn(ROOM_IDS[0], bot.client.rooms)

                policy = chat_policy({PAIR[1]: []})
                current = datetime.now(timezone.utc)
                policy["generated_at"] = current.isoformat().replace("+00:00", "Z")
                policy["expires_at"] = (current + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
                policy_path.write_text(json.dumps(policy), encoding="utf-8")
                denied, error = bot._refresh_direct_room_authorization()
                self.assertEqual(denied, {})
                self.assertIsNone(error)
                await bot._process_direct_invites(SimpleNamespace(rooms=SimpleNamespace(invite={})), denied,
                                                  policy_available=True)
                self.assertEqual(bot.client.leave_calls, [ROOM_IDS[0]])
                self.assertNotIn((ROOM_IDS[0], "$event"), bot.cache)

        asyncio.run(exercise())






@unittest.skipUnless(hasattr(nio, "InviteInfo"), "Locked SDK required for invite compatibility checks")
class DirectInviteRetryTests(unittest.TestCase):
    def test_invite_sender_accepts_the_actual_sdk_invite_state_list(self):
        invite = nio.InviteInfo([{"type": "m.room.member", "state_key": BOT_ID,
                                  "sender": PAIR[0], "content": {"membership": "invite"}}])
        self.assertEqual(orgbot.invite_sender(invite, BOT_ID), PAIR[0])

    def test_retained_invite_is_retried_only_after_registry_and_pair_authorize(self):
        from nio.rooms import MatrixInvitedRoom
        room = MatrixInvitedRoom(ROOM_IDS[0], BOT_ID)
        room.inviter = PAIR[0]
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.user_id = BOT_ID
        bot.direct_room_registry_ids = set()
        bot.pending_direct_invites = {}
        bot.direct_join_attempt_at = {}
        bot.client = SimpleNamespace(rooms={}, invited_rooms={ROOM_IDS[0]: room},
                                     join=AsyncMock(return_value=SimpleNamespace(room_id=ROOM_IDS[0])))
        empty_incremental = SimpleNamespace(rooms=SimpleNamespace(invite={}))
        asyncio.run(bot._process_direct_invites(empty_incremental, {}))
        bot.client.join.assert_not_awaited()
        bot.direct_room_registry_ids.add(ROOM_IDS[0])
        asyncio.run(bot._process_direct_invites(empty_incremental,
                    {ROOM_IDS[0]: {"participants": PAIR, "status": "pending_bot"}}))
        bot.client.join.assert_awaited_once_with(ROOM_IDS[0])

    def test_retained_invite_does_not_join_without_current_pair_grant(self):
        from nio.rooms import MatrixInvitedRoom
        room = MatrixInvitedRoom(ROOM_IDS[0], BOT_ID)
        room.inviter = "@outside:example.test"
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.user_id = BOT_ID
        bot.direct_room_registry_ids = {ROOM_IDS[0]}
        bot.pending_direct_invites = {}
        bot.direct_join_attempt_at = {}
        bot.client = SimpleNamespace(rooms={}, invited_rooms={ROOM_IDS[0]: room}, join=AsyncMock())
        asyncio.run(bot._process_direct_invites(SimpleNamespace(rooms=SimpleNamespace(invite={})),
                    {ROOM_IDS[0]: {"participants": PAIR, "status": "pending_bot"}}))
        bot.client.join.assert_not_awaited()


@unittest.skipUnless(hasattr(nio, "KeysUploadResponse"), "Locked SDK required for E2EE maintenance checks")
class E2EEMaintenanceTests(unittest.TestCase):
    def client(self):
        return SimpleNamespace(
            should_upload_keys=True, should_query_keys=True, should_claim_keys=True,
            keys_upload=AsyncMock(return_value=nio.KeysUploadResponse(0, 1)),
            keys_query=AsyncMock(return_value=nio.KeysQueryResponse({}, {})),
            keys_claim=AsyncMock(return_value=nio.KeysClaimResponse({}, {})),
            get_users_for_key_claiming=lambda: {"@fixture:example.org": ["DEVICE"]},
            send_to_device_messages=AsyncMock(return_value=[]),
        )

    def test_manual_sync_performs_required_sdk_crypto_traffic(self):
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.client = self.client()
        asyncio.run(bot._maintain_e2ee())
        bot.client.keys_upload.assert_awaited_once()
        bot.client.keys_query.assert_awaited_once()
        bot.client.keys_claim.assert_awaited_once_with({"@fixture:example.org": ["DEVICE"]})
        bot.client.send_to_device_messages.assert_awaited_once()

    def make_device_key_bot(self, initial_devices, devices_after_upload=None):
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.user_id = BOT_ID
        bot.device_id = "SYNTHETICDEVICE"
        bot.device_keys_verified = False
        bot.device_keys_verified_at = None
        bot.device_key_check_interval = 300.0
        account = SimpleNamespace(
            shared=True,
            identity_keys={"ed25519": "synthetic-ed25519-public", "curve25519": "synthetic-curve25519-public"},
        )
        olm = SimpleNamespace(account=account, users_for_key_query=set(), save_account=Mock())
        payload = synthetic_bot_device_payload()
        query_responses = [nio.KeysQueryResponse({BOT_ID: initial_devices}, {})]
        if devices_after_upload is not None:
            query_responses.append(nio.KeysQueryResponse({BOT_ID: devices_after_upload}, {}))

        async def keys_query():
            return query_responses.pop(0)

        async def keys_upload():
            self.assertFalse(account.shared, "recovery must force nio to include device_keys")
            account.shared = True
            return nio.KeysUploadResponse(0, 50)

        bot.client = SimpleNamespace(
            olm=olm,
            should_upload_keys=False,
            should_query_keys=False,
            should_claim_keys=False,
            keys_query=AsyncMock(side_effect=keys_query),
            keys_upload=AsyncMock(side_effect=keys_upload),
            keys_claim=AsyncMock(),
            get_users_for_key_claiming=lambda: {},
            send_to_device_messages=AsyncMock(return_value=[]),
        )
        return bot, payload

    def test_persisted_shared_flag_with_missing_server_device_republishes_then_verifies_same_keys(self):
        payload = synthetic_bot_device_payload()
        bot, _ = self.make_device_key_bot({})
        bot.client.keys_query.side_effect = [
            nio.KeysQueryResponse({BOT_ID: {}}, {}),
            nio.KeysQueryResponse({BOT_ID: {"SYNTHETICDEVICE": payload}}, {}),
        ]
        asyncio.run(bot._maintain_e2ee())
        bot.client.keys_upload.assert_awaited_once()
        self.assertTrue(bot.client.olm.account.shared)
        self.assertTrue(bot.device_keys_verified)
        self.assertEqual(bot.client.keys_query.await_count, 2)

    def test_existing_server_device_must_match_persisted_fingerprint(self):
        bot, _payload = self.make_device_key_bot({"SYNTHETICDEVICE": {
            **synthetic_bot_device_payload(),
            "keys": {"ed25519:SYNTHETICDEVICE": "different-key", "curve25519:SYNTHETICDEVICE": "synthetic-curve25519-public"},
        }})
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            asyncio.run(bot._maintain_e2ee())
        bot.client.keys_upload.assert_not_awaited()
        self.assertFalse(bot.device_keys_verified)

    def test_matching_server_device_is_verified_without_reupload(self):
        bot, payload = self.make_device_key_bot({"SYNTHETICDEVICE": synthetic_bot_device_payload()})
        bot.client.keys_query.side_effect = [nio.KeysQueryResponse(
            {BOT_ID: {"SYNTHETICDEVICE": payload}}, {},
        )]
        asyncio.run(bot._maintain_e2ee())
        bot.client.keys_upload.assert_not_awaited()
        self.assertTrue(bot.device_keys_verified)

    def test_missing_device_after_republish_keeps_bot_unready(self):
        bot, _payload = self.make_device_key_bot({}, {})
        with self.assertRaisesRegex(RuntimeError, "still absent or mismatched"):
            asyncio.run(bot._maintain_e2ee())
        self.assertFalse(bot.device_keys_verified)

    def test_locked_nio_can_republish_device_keys_after_persisted_shared_flag(self):
        if not hasattr(nio, "AsyncClient") or not hasattr(nio, "AsyncClientConfig"):
            self.skipTest("matrix-nio is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            client = nio.AsyncClient(
                "https://example.test",
                BOT_ID,
                device_id="SYNTHETICDEVICE",
                store_path=directory,
                config=nio.AsyncClientConfig(encryption_enabled=True),
            )
            client.restore_login(BOT_ID, "SYNTHETICDEVICE", "synthetic-token")
            client.olm.account.shared = True
            client.olm.uploaded_key_count = None
            self.assertFalse(client.should_upload_keys)

            client.olm.account.shared = False
            self.assertTrue(client.should_upload_keys)
            content = client.olm.share_keys()
            self.assertIn("device_keys", content)
            self.assertEqual(content["device_keys"]["user_id"], BOT_ID)
            self.assertEqual(content["device_keys"]["device_id"], "SYNTHETICDEVICE")
            self.assertEqual(
                set(content["device_keys"]["keys"]),
                {"ed25519:SYNTHETICDEVICE", "curve25519:SYNTHETICDEVICE"},
            )
            asyncio.run(client.close())

    def test_matrix_login_must_return_the_configured_device_id(self):
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.user_id = BOT_ID
        bot.device_id = "SYNTHETICDEVICE"
        bot.client = SimpleNamespace(restore_login=Mock())
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            bot._restore_authenticated_session({
                "user_id": BOT_ID, "device_id": "UNEXPECTEDDEVICE", "access_token": "synthetic-token",
            })
        bot.client.restore_login.assert_not_called()
        bot._restore_authenticated_session({
            "user_id": BOT_ID, "device_id": "SYNTHETICDEVICE", "access_token": "synthetic-token",
        })
        bot.client.restore_login.assert_called_once_with(BOT_ID, "SYNTHETICDEVICE", "synthetic-token")

    def test_export_is_blocked_before_server_device_key_verification(self):
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.export_token = "synthetic-export-token-value"
        bot.device_keys_verified = False

        class Request:
            headers = {"Authorization": "Bearer synthetic-export-token-value"}

        response = asyncio.run(bot.export_rooms(Request()))
        self.assertEqual(response.status, 503)
        self.assertIn("orgbot_device_keys_unverified", response.text)

    def test_restart_rebuilds_invites_without_replaying_room_history(self):
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.stop_event = asyncio.Event()
        async def one_sync(**kwargs):
            bot.stop_event.set()
            return SimpleNamespace()
        bot.client = SimpleNamespace(next_batch="old-next", loaded_sync_token="old-saved",
                                     sync=AsyncMock(side_effect=one_sync))
        bot.ready = False
        bot._maintain_e2ee = AsyncMock()
        async def maintain_and_verify():
            bot.device_keys_verified = True
        bot._refresh_direct_room_authorization = lambda: ({}, None)
        bot._process_direct_invites = AsyncMock()
        bot._maintain_e2ee.side_effect = maintain_and_verify
        asyncio.run(bot.sync_loop())
        self.assertIsNone(bot.client.next_batch)
        self.assertIsNone(bot.client.loaded_sync_token)
        bot.client.sync.assert_awaited_once_with(timeout=0, full_state=True,
                     sync_filter={"room": {"timeline": {"limit": 0}}})
        self.assertTrue(bot.ready)

    def test_failed_key_upload_does_not_publish_ready_sync(self):
        bot = orgbot.OrgBot.__new__(orgbot.OrgBot)
        bot.client = self.client()
        bot.client.keys_upload.return_value = object()
        bot.client.sync = AsyncMock(return_value=SimpleNamespace())
        bot.ready = True
        bot.stop_event = asyncio.Event()
        async def stop_after_failure(_):
            bot.stop_event.set()
        with patch.object(orgbot.asyncio, "sleep", side_effect=stop_after_failure):
            asyncio.run(bot.sync_loop())
        self.assertFalse(bot.ready)
        bot.client.keys_query.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
