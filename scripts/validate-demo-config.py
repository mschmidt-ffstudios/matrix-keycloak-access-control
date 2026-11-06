#!/usr/bin/env python3
"""Validate the tracked demo seed, policy, and white-label configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROLE_RE = re.compile(r"^room\.([a-z0-9_-]+)\.([a-z0-9_-]+)$")
USERNAME_RE = re.compile(r"^(?:demo-[0-9]{2}|export-test-[0-9]{2})$")
SECRET_KEYS = {"password", "secret", "client_secret", "access_token", "refresh_token", "credentials"}


class Validator:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.errors: list[str] = []

    def error(self, location: str, message: str) -> None:
        self.errors.append(f"{location}: {message}")

    def read_json(self, relative: str) -> dict[str, Any] | None:
        path = self.root / relative
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.error(relative, "file is missing")
            return None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.error(relative, f"invalid JSON ({exc})")
            return None
        if not isinstance(value, dict):
            self.error(relative, "root JSON value must be an object")
            return None
        return value

    def check_seed(self, seed: dict[str, Any], policy: dict[str, Any]) -> None:
        users = seed.get("users")
        if not isinstance(users, list):
            self.error("config/seed/demo-users.json", "users must be a list")
            return

        demo_users = [user for user in users if isinstance(user, dict) and str(user.get("username", "")).startswith("demo-")]
        export_test_users = [user for user in users if isinstance(user, dict) and str(user.get("username", "")).startswith("export-test-")]
        if len(demo_users) != 20 or len(export_test_users) != 2 or len(users) != 22:
            self.error("config/seed/demo-users.json", "expected 20 demo users and 2 isolated export-test users")

        usernames: set[str] = set()
        room_ids = {room.get("id") for room in policy.get("rooms", []) if isinstance(room, dict)}
        export_test_room_ids = {room_id for room_id in room_ids if isinstance(room_id, str) and room_id.startswith("export_test_")}
        role_names = set(policy.get("roles", []))
        role_pattern = policy.get("keycloak_role_pattern", "room.{room_id}.{role}")
        if role_pattern != "room.{room_id}.{role}":
            self.error("config/policies/room-access.json", "validator expects the supported room.{room_id}.{role} naming pattern")

        levels = seed.get("clearance_levels", [])
        clearance_ranks = {
            entry.get("id"): entry.get("rank")
            for entry in levels
            if isinstance(entry, dict)
        }

        for index, user in enumerate(users):
            where = f"config/seed/demo-users.json users[{index}]"
            if not isinstance(user, dict):
                self.error(where, "user entry must be an object")
                continue
            username = user.get("username")
            if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
                self.error(where, "username must be a synthetic demo-NN account")
            elif username in usernames:
                self.error(where, f"duplicate username {username}")
            else:
                usernames.add(username)
            account_type = user.get("account_type")
            if username and username.startswith("export-test-") and account_type != "export_test_only":
                self.error(where, "export-test users must be explicitly marked export_test_only")
            if username and username.startswith("demo-") and account_type is not None:
                self.error(where, "regular demo users must not be marked as export-test accounts")

            clearance = user.get("clearance")
            if clearance not in clearance_ranks:
                self.error(where, "clearance must reference a declared clearance level")

            room_roles = user.get("room_roles", [])
            if not isinstance(room_roles, list):
                self.error(where, "room_roles must be a list")
                continue
            seen_user_rooms: set[str] = set()
            for role_name in room_roles:
                match = ROLE_RE.fullmatch(role_name) if isinstance(role_name, str) else None
                if not match:
                    self.error(where, f"invalid room role {role_name!r}; expected room.<room>.<role>")
                    continue
                room_id, role = match.groups()
                if room_id not in room_ids:
                    self.error(where, f"role {role_name!r} references an unconfigured room")
                    continue
                if role not in role_names:
                    self.error(where, f"role {role_name!r} is not one of the configured room roles")
                if room_id in seen_user_rooms:
                    self.error(where, f"more than one role is assigned for room {room_id}")
                seen_user_rooms.add(room_id)

                room = next((candidate for candidate in policy.get("rooms", []) if candidate.get("id") == room_id), None)
                if room and clearance in clearance_ranks:
                    required_rank = clearance_ranks.get(room.get("minimum_clearance"))
                    actual_rank = clearance_ranks.get(clearance)
                    if isinstance(required_rank, int) and isinstance(actual_rank, int) and actual_rank < required_rank:
                        self.error(where, f"role {role_name!r} exceeds the user's clearance for room {room_id}")

            if isinstance(username, str) and username.startswith("export-test-"):
                assigned_test_rooms = {
                    match.group(1)
                    for role_name in room_roles
                    if isinstance(role_name, str)
                    and (match := ROLE_RE.fullmatch(role_name))
                    and match.group(1).startswith("export_test_")
                }
                if assigned_test_rooms != export_test_room_ids:
                    self.error(where, "export-test users must have a role in every isolated export-test room and no regular-room roles")

        self._check_sensitive_values(seed, "config/seed/demo-users.json")

        admin_users = [user for user in users if isinstance(user, dict) and "org-export-admin" in user.get("realm_roles", [])]
        if not admin_users:
            self.error("config/seed/demo-users.json", "assign the synthetic org-export-admin role to at least one demo user")

    def _check_sensitive_values(self, value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in SECRET_KEYS:
                    self.error(location, f"contains a forbidden credential field: {key}")
                self._check_sensitive_values(child, location)
        elif isinstance(value, list):
            for child in value:
                self._check_sensitive_values(child, location)

    def check_policy(self, policy: dict[str, Any], seed: dict[str, Any]) -> None:
        location = "config/policies/room-access.json"
        if policy.get("default_decision") != "deny":
            self.error(location, "default_decision must be deny")
        direct = policy.get("direct_chats", {})
        prefix = direct.get("group_prefix") if isinstance(direct, dict) else None
        if not isinstance(prefix, str) or not re.fullmatch(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.", prefix):
            self.error(location, "direct_chats.group_prefix must be a non-empty dotted namespace ending with a dot")
        ttl = direct.get("snapshot_ttl_seconds") if isinstance(direct, dict) else None
        if type(ttl) is not int or not 1 <= ttl <= 60:
            self.error(location, "direct_chats.snapshot_ttl_seconds must be an integer from 1 to 60")
        if (not isinstance(direct, dict) or direct.get("membership_rule") != "shared_exact_keycloak_group"
                or direct.get("require_orgbot") is not True or direct.get("allow_user_created_group_rooms") is not False):
            self.error(location, "direct chats require shared exact groups, OrgBot, and no user-created group rooms")
        group_names = []
        for group in seed.get("keycloak_groups", []):
            name = group.get("name") if isinstance(group, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*", name):
                self.error("config/seed/demo-users.json", "Keycloak group names must use non-empty dotted components")
            else:
                group_names.append(name)
        if len(group_names) != len(set(group_names)):
            self.error("config/seed/demo-users.json", "Keycloak group names must be unique")
        for user in seed.get("users", []):
            groups = user.get("groups", [])
            if not isinstance(groups, list) or any(group not in {"/" + name for name in group_names} for group in groups):
                self.error("config/seed/demo-users.json", "User groups must reference explicitly declared group paths")
            if str(user.get("username", "")).startswith("export-test-") and groups:
                self.error("config/seed/demo-users.json", "Export fixture accounts must not join direct-chat groups")
        roles = policy.get("roles")
        if not isinstance(roles, list) or not roles or len(set(roles)) != len(roles):
            self.error(location, "roles must be a non-empty list of unique role names")
            roles = []

        seed_levels = seed.get("clearance_levels", [])
        policy_levels = policy.get("clearance_levels", [])
        if self._levels_signature(seed_levels, "config/seed/demo-users.json") != self._levels_signature(policy_levels, location):
            self.error(location, "clearance_levels must match the seed in the same rank order")
        rank_by_id = {entry["id"]: entry["rank"] for entry in policy_levels if isinstance(entry, dict) and "id" in entry and "rank" in entry}

        rooms = policy.get("rooms")
        if not isinstance(rooms, list) or not rooms:
            self.error(location, "rooms must be a non-empty list")
            rooms = []
        room_ids = [room.get("id") for room in rooms if isinstance(room, dict)]
        if len(room_ids) != len(set(room_ids)):
            self.error(location, "room IDs must be unique")
        aliases = [room.get("alias_localpart") for room in rooms if isinstance(room, dict)]
        if len(aliases) != len(set(aliases)):
            self.error(location, "room alias localparts must be unique")
        for room in rooms:
            if not isinstance(room, dict):
                self.error(location, "every room entry must be an object")
                continue
            room_id = room.get("id", "<unknown>")
            if room.get("encrypted") is not True:
                self.error(location, f"room {room_id} must be encrypted")
            if room.get("join_rule") != "invite":
                self.error(location, f"room {room_id} must be invite-only")
            if room.get("history_visibility") != "joined":
                self.error(location, f"room {room_id} must limit history visibility to joined members")
            if room.get("minimum_clearance") not in rank_by_id:
                self.error(location, f"room {room_id} references an undeclared minimum clearance")
        test_rooms = [room for room in rooms if isinstance(room, dict) and room.get("id", "").startswith("export_test_")]
        expected_test_names = {"[EXPORT TEST] Allgemein", "[EXPORT TEST] Pummel", "[EXPORT TEST] Speicher", "[EXPORT TEST] Leitung"}
        if len(test_rooms) != 4 or {room.get("name") for room in test_rooms} != expected_test_names:
            self.error(location, "four separately named [EXPORT TEST] rooms are required for isolated export fixtures")
        if any(room.get("test_only") is not True or room.get("join_rule") != "invite" for room in test_rooms):
            self.error(location, "export-test rooms must be marked test_only and invite-only")

        levels = policy.get("clearance_levels")
        self._levels_signature(levels, location)
        if policy.get("clearance_levels") != seed.get("clearance_levels"):
            # _levels_signature emits actionable validation errors; this check
            # keeps mismatched but individually valid rank tables visible too.
            pass

        matrix_levels = policy.get("matrix_power_levels", {})
        role_power = policy.get("role_power_levels", {})
        call_member_power = matrix_levels.get("events", {}).get("org.matrix.msc3401.call.member")
        writer_power = role_power.get("writer")
        if not isinstance(call_member_power, int) or not isinstance(writer_power, int) or call_member_power != writer_power:
            self.error(location, "MatrixRTC call membership must use the writer power level so readers cannot start or join calls")
        management_thresholds = [
            matrix_levels.get("invite"), matrix_levels.get("kick"), matrix_levels.get("ban"),
            matrix_levels.get("state_default"), matrix_levels.get("events_default"),
            matrix_levels.get("events", {}).get("m.room.member"),
            matrix_levels.get("events", {}).get("m.room.power_levels"),
            matrix_levels.get("events", {}).get("m.room.join_rules"),
            matrix_levels.get("events", {}).get("m.room.history_visibility"),
            matrix_levels.get("events", {}).get("m.room.encryption"),
        ]
        if any(not isinstance(value, int) for value in management_thresholds):
            self.error(location, "all room-management power thresholds must be configured as integers")
        else:
            minimum_management = min(management_thresholds)
            for role in roles:
                power = role_power.get(role)
                if not isinstance(power, int) or power < 0:
                    self.error(location, f"role {role} needs a non-negative integer Matrix power level")
                elif power >= minimum_management:
                    self.error(location, f"role {role} power must remain below the lowest room-management threshold")

    def _levels_signature(self, levels: Any, location: str) -> list[tuple[str, int]]:
        if not isinstance(levels, list) or not levels:
            self.error(location, "clearance_levels must be a non-empty ordered list")
            return []
        signature: list[tuple[str, int]] = []
        seen_ids: set[str] = set()
        seen_ranks: set[int] = set()
        for index, entry in enumerate(levels):
            if not isinstance(entry, dict):
                self.error(location, f"clearance_levels[{index}] must be an object")
                continue
            level_id, rank = entry.get("id"), entry.get("rank")
            if not isinstance(level_id, str) or not level_id:
                self.error(location, f"clearance_levels[{index}] has no valid id")
                continue
            if not isinstance(rank, int) or rank < 0:
                self.error(location, f"clearance level {level_id} needs a non-negative integer rank")
                continue
            if level_id in seen_ids or rank in seen_ranks:
                self.error(location, "clearance level IDs and ranks must be unique")
            if signature and rank <= signature[-1][1]:
                self.error(location, "clearance levels must be ordered by increasing rank")
            seen_ids.add(level_id)
            seen_ranks.add(rank)
            signature.append((level_id, rank))
        return signature

    def check_branding(self, brand_doc: dict[str, Any], element: dict[str, Any]) -> None:
        location = "config/branding/brand.json"
        brand = brand_doc.get("brand")
        if not isinstance(brand, dict):
            self.error(location, "brand object is missing")
            return
        for field in ("organization_name", "product_name", "short_name"):
            if not isinstance(brand.get(field), str) or not brand[field].strip():
                self.error(location, f"{field} must be a non-empty string")

        for asset_key in ("logo_wide", "logo_mark", "login_background"):
            value = brand.get(asset_key)
            if not value:
                continue
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                self.error(location, f"{asset_key} must be a relative path inside config/branding")
                continue
            target = (self.root / "config/branding" / relative).resolve()
            try:
                target.relative_to((self.root / "config/branding").resolve())
            except ValueError:
                self.error(location, f"{asset_key} escapes config/branding")
                continue
            if not target.is_file():
                self.error(location, f"{asset_key} points to missing asset {value!r}")

        targets = brand_doc.get("targets", {})
        keycloak_targets = targets.get("keycloak", {}) if isinstance(targets, dict) else {}
        themes = {
            path.name
            for path in (self.root / "config/keycloak/themes").iterdir()
            if path.is_dir()
        } if (self.root / "config/keycloak/themes").is_dir() else set()
        for key in ("login_theme", "account_theme"):
            theme_name = keycloak_targets.get(key) if isinstance(keycloak_targets, dict) else None
            if not isinstance(theme_name, str) or not theme_name:
                self.error(location, f"targets.keycloak.{key} must name a configured theme")
                continue
            if theme_name not in themes:
                self.error(location, f"targets.keycloak.{key} names missing theme {theme_name!r}")
                continue
            part = "login" if key == "login_theme" else "account"
            properties = self.root / "config/keycloak/themes" / theme_name / part / "theme.properties"
            if not properties.is_file():
                self.error(location, f"theme {theme_name!r} is missing {part}/theme.properties")

        element_brand = element.get("brand")
        target_brand = targets.get("element_web", {}).get("brand") if isinstance(targets, dict) else None
        if element_brand != brand.get("product_name") or target_brand != element_brand:
            self.error("config/element/config.json", "Element brand and brand.json product_name/target brand must match")
        logo_url = element.get("branding", {}).get("auth_header_logo_url")
        expected_suffix = Path(brand.get("logo_wide", "")).suffix.lower()
        if not isinstance(logo_url, str) or Path(logo_url).suffix.lower() != expected_suffix:
            self.error("config/element/config.json", "auth_header_logo_url extension must match the configured wide-logo asset")

    def run(self) -> bool:
        seed = self.read_json("config/seed/demo-users.json")
        policy = self.read_json("config/policies/room-access.json")
        brand = self.read_json("config/branding/brand.json")
        element = self.read_json("config/element/config.json")
        if seed and policy:
            self.check_seed(seed, policy)
            self.check_policy(policy, seed)
        if brand and element:
            self.check_branding(brand, element)
        if self.errors:
            for error in self.errors:
                print(f"ERROR: {error}", file=sys.stderr)
            print(f"Configuration validation failed with {len(self.errors)} issue(s).", file=sys.stderr)
            return False
        print("Configuration validation passed: 20 demo users, 2 isolated export-test users, room policies, role levels, and branding assets are consistent.")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="project root")
    args = parser.parse_args()
    return 0 if Validator(args.root).run() else 1


if __name__ == "__main__":
    raise SystemExit(main())
