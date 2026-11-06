#!/usr/bin/env python3
"""Offline tests for the provisioned OrgBot Matrix session boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


try:
    import nio  # noqa: F401
except ImportError:
    stub = types.ModuleType("nio")
    stub.AsyncClient = type("AsyncClient", (), {})
    stub.AsyncClientConfig = type("AsyncClientConfig", (), {})
    stub.SyncError = type("SyncError", (), {})
    sys.modules["nio"] = stub

SPEC = importlib.util.spec_from_file_location("orgbot_session_tests", Path(__file__).with_name("orgbot.py"))
assert SPEC and SPEC.loader
orgbot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(orgbot)

BOT_ID = "@orgbot:example.test"
BOT_DEVICE = "FOLDFENCEORGBOT"
TOKEN = "mas_a_synthetic_opaque_access_token_value"


def session_document(**changes):
    document = {
        "schema_version": 1,
        "user_id": BOT_ID,
        "device_id": BOT_DEVICE,
        "access_token": TOKEN,
        "admin": False,
    }
    document.update(changes)
    return document


class SessionFileTests(unittest.TestCase):
    def write_document(self, directory: str, document: dict | str, mode: int = 0o600) -> Path:
        path = Path(directory) / "orgbot-session.json"
        raw = document if isinstance(document, str) else json.dumps(document)
        path.write_text(raw, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_valid_owner_only_file_returns_exact_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_document(directory, session_document())
            result = orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)
        self.assertEqual(result, session_document())

    def test_non_0600_permissions_are_rejected(self):
        for mode in (0o400, 0o640, 0o644, 0o660):
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory() as directory:
                path = self.write_document(directory, session_document(), mode=mode)
                with self.assertRaisesRegex(RuntimeError, "missing, malformed, or unsafe"):
                    orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)

    def test_wrong_owner_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_document(directory, session_document())
            with patch.object(orgbot.os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(RuntimeError, "missing, malformed, or unsafe"):
                    orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.write_document(directory, session_document())
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "missing, malformed, or unsafe"):
                orgbot.read_session_file(link, BOT_ID, BOT_DEVICE)

    def test_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "missing, malformed, or unsafe"):
                orgbot.read_session_file(Path(directory), BOT_ID, BOT_DEVICE)

    def test_relative_path_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "missing, malformed, or unsafe"):
            orgbot.read_session_file(Path("session.json"), BOT_ID, BOT_DEVICE)

    def test_duplicate_json_fields_are_rejected(self):
        raw = json.dumps(session_document())[:-1] + ',"admin":true}'
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_document(directory, raw)
            with self.assertRaisesRegex(RuntimeError, "missing, malformed, or unsafe"):
                orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)

    def test_unknown_or_missing_fields_are_rejected(self):
        cases = (
            session_document(extra="unexpected"),
            {key: value for key, value in session_document().items() if key != "admin"},
        )
        for document in cases:
            with self.subTest(document=document.keys()), tempfile.TemporaryDirectory() as directory:
                path = self.write_document(directory, document)
                with self.assertRaisesRegex(RuntimeError, "invalid schema"):
                    orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)

    def test_wrong_user_device_or_admin_flag_is_rejected(self):
        cases = (
            session_document(user_id="@another:example.test"),
            session_document(device_id="OTHERDEVICE"),
            session_document(admin=True),
            session_document(admin=1),
            session_document(schema_version=True),
        )
        for document in cases:
            with self.subTest(document=document), tempfile.TemporaryDirectory() as directory:
                path = self.write_document(directory, document)
                with self.assertRaisesRegex(RuntimeError, "identity or privilege"):
                    orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)

    def test_empty_control_character_and_oversized_tokens_are_rejected(self):
        cases = ("", "has space and cannot be a bearer token", "bad\nheader", "x" * 8193)
        for token in cases:
            with self.subTest(token_length=len(token)), tempfile.TemporaryDirectory() as directory:
                path = self.write_document(directory, session_document(access_token=token))
                expected = "malformed, or unsafe" if len(token) > orgbot.MAX_SESSION_FILE_BYTES else "identity or privilege"
                with self.assertRaisesRegex(RuntimeError, expected):
                    orgbot.read_session_file(path, BOT_ID, BOT_DEVICE)


class Response:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, content_type=None):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


class TokenVerificationTests(unittest.TestCase):
    def make_bot(self):
        bot = object.__new__(orgbot.OrgBot)
        bot.user_id = BOT_ID
        bot.device_id = BOT_DEVICE
        bot.homeserver = "https://matrix.example.test"
        bot.client = types.SimpleNamespace(restore_login=Mock())
        return bot

    def test_whoami_and_synapse_admin_403_are_required_before_restore(self):
        bot = self.make_bot()
        fake_session = Session([
            Response(200, {"user_id": BOT_ID, "device_id": BOT_DEVICE, "is_guest": False}),
            Response(403, {"errcode": "M_FORBIDDEN", "error": "Forbidden"}),
        ])
        with patch.object(orgbot, "ClientSession", return_value=fake_session) as client_session:
            asyncio.run(bot._verify_session_identity(session_document()))
        client_session.assert_called_once()
        self.assertEqual(len(fake_session.requests), 2)
        self.assertTrue(fake_session.requests[0][0].endswith("/_matrix/client/v3/account/whoami"))
        self.assertTrue(fake_session.requests[1][0].endswith("/_synapse/admin/v1/users/%40orgbot%3Aexample.test/joined_rooms"))
        for _url, request in fake_session.requests:
            self.assertEqual(request["headers"], {"Authorization": f"Bearer {TOKEN}"})
            self.assertFalse(request["allow_redirects"])
        bot._restore_authenticated_session(session_document())
        bot.client.restore_login.assert_called_once_with(BOT_ID, BOT_DEVICE, TOKEN)

    def test_whoami_user_mismatch_stops_before_admin_probe_and_restore(self):
        bot = self.make_bot()
        fake_session = Session([Response(200, {"user_id": "@human:example.test", "device_id": BOT_DEVICE})])
        with patch.object(orgbot, "ClientSession", return_value=fake_session):
            with self.assertRaisesRegex(RuntimeError, "user/device identity"):
                asyncio.run(bot._verify_session_identity(session_document()))
        self.assertEqual(len(fake_session.requests), 1)
        bot.client.restore_login.assert_not_called()

    def test_whoami_device_mismatch_stops_before_admin_probe(self):
        bot = self.make_bot()
        fake_session = Session([Response(200, {"user_id": BOT_ID, "device_id": "OTHERDEVICE"})])
        with patch.object(orgbot, "ClientSession", return_value=fake_session):
            with self.assertRaisesRegex(RuntimeError, "user/device identity"):
                asyncio.run(bot._verify_session_identity(session_document()))
        self.assertEqual(len(fake_session.requests), 1)

    def test_admin_token_is_rejected(self):
        bot = self.make_bot()
        fake_session = Session([
            Response(200, {"user_id": BOT_ID, "device_id": BOT_DEVICE}),
            Response(200, {"joined_rooms": []}),
        ])
        with patch.object(orgbot, "ClientSession", return_value=fake_session):
            with self.assertRaisesRegex(RuntimeError, "did not prove non-admin"):
                asyncio.run(bot._verify_session_identity(session_document()))
        bot.client.restore_login.assert_not_called()

    def test_non_admin_probe_requires_synapse_forbidden_error(self):
        bot = self.make_bot()
        fake_session = Session([
            Response(200, {"user_id": BOT_ID, "device_id": BOT_DEVICE}),
            Response(403, {"errcode": "M_UNKNOWN", "error": "not a Synapse admin response"}),
        ])
        with patch.object(orgbot, "ClientSession", return_value=fake_session):
            with self.assertRaisesRegex(RuntimeError, "did not prove non-admin"):
                asyncio.run(bot._verify_session_identity(session_document()))

    def test_invalid_whoami_status_fails_closed(self):
        bot = self.make_bot()
        fake_session = Session([Response(401, {"errcode": "M_UNKNOWN_TOKEN"})])
        with patch.object(orgbot, "ClientSession", return_value=fake_session):
            with self.assertRaisesRegex(RuntimeError, "failed Matrix whoami"):
                asyncio.run(bot._verify_session_identity(session_document()))
        bot.client.restore_login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
