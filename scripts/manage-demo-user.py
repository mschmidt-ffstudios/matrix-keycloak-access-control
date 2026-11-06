#!/usr/bin/env python3
"""Create and manage synthetic Matrix demo users through Keycloak Admin REST."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REALM_DEFAULT = "foldfence-demo"
CLIENT_DEFAULT = "matrix-synapse"
MATRIX_ACCESS_ROLE = "matrix-access"
MATRIX_ACCESS_RESERVED_USERS = {"demo-admin-01", "demo-admin-02", "demo-policy-admin"}
DEMO_USERNAME_RE = re.compile(r"^demo-[a-z0-9][a-z0-9-]*$")


class AdminApiError(RuntimeError):
    pass


def project_env() -> dict[str, str]:
    """Read simple KEY=VALUE lines without executing the project's private .env."""
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update(os.environ)
    if "KEYCLOAK_ADMIN_USERNAME" not in values and "KEYCLOAK_BOOTSTRAP_ADMIN" in values:
        values["KEYCLOAK_ADMIN_USERNAME"] = values["KEYCLOAK_BOOTSTRAP_ADMIN"]
    if "KEYCLOAK_ADMIN_PASSWORD" not in values and "KEYCLOAK_BOOTSTRAP_PASSWORD" in values:
        values["KEYCLOAK_ADMIN_PASSWORD"] = values["KEYCLOAK_BOOTSTRAP_PASSWORD"]
    if "KEYCLOAK_BASE_URL" not in values and "KEYCLOAK_PUBLIC_URL" in values:
        values["KEYCLOAK_BASE_URL"] = values["KEYCLOAK_PUBLIC_URL"]
    return values


def known_room_ids() -> set[str]:
    doc = json.loads((ROOT / "config/policies/room-access.json").read_text(encoding="utf-8"))
    return {room["id"] for room in doc["rooms"] if room.get("test_only") is not True}


def known_clearances() -> set[str]:
    doc = json.loads((ROOT / "config/policies/room-access.json").read_text(encoding="utf-8"))
    return {level["id"] for level in doc["clearance_levels"]}


def seeded_password_for_user(env: dict[str, str], username: str) -> str | None:
    """Read only a seeded user's password from the private, target-scoped manifest."""
    password_mode = env.get("DEMO_USER_PASSWORD_MODE", "individual").strip().lower()
    if password_mode == "shared-legacy":
        password = env.get("DEMO_USER_PASSWORD", "").strip()
        return password if password and password != "CHANGE_ME" else None
    if password_mode != "individual":
        raise AdminApiError("DEMO_USER_PASSWORD_MODE must be individual or shared-legacy.")

    generated_value = env.get("DEMO_GENERATED_DIR", "../../internal/deploy/local/generated").strip()
    generated = Path(generated_value)
    if generated.is_absolute():
        raise AdminApiError("DEMO_GENERATED_DIR must be relative to deployment/compose/.")
    generated = (ROOT / "deployment" / "compose" / generated).resolve()
    try:
        generated.relative_to((ROOT / "internal").resolve())
    except ValueError:
        raise AdminApiError("DEMO_GENERATED_DIR must remain inside internal/.") from None

    path = generated / "handoff" / "credential-manifest.json"
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise AdminApiError("The private credential manifest must be a regular owner-only file; rerun demo preparation to repair it.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AdminApiError("The private credential manifest could not be read safely.") from None
    expected_target = env.get("DEMO_TARGET", generated.name).strip()
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("target") != expected_target
        or manifest.get("password_mode") != password_mode
    ):
        raise AdminApiError("The private credential manifest does not match this deployment target and password mode.")
    accounts = manifest.get("accounts")
    if not isinstance(accounts, list):
        raise AdminApiError("The private credential manifest has an invalid accounts list.")
    matches = [
        account for account in accounts
        if isinstance(account, dict) and account.get("kind") == "human" and account.get("username") == username
    ]
    if len(matches) > 1:
        raise AdminApiError("The private credential manifest repeats this username.")
    if not matches:
        return None
    password = matches[0].get("password")
    if not isinstance(password, str) or not password:
        raise AdminApiError("The private credential manifest has no password for this seeded user.")
    return password


def policy_group_prefix() -> str:
    doc = json.loads((ROOT / "config/policies/room-access.json").read_text(encoding="utf-8"))
    prefix = doc.get("direct_chats", {}).get("group_prefix")
    if not isinstance(prefix, str) or not prefix or "/" in prefix:
        raise AdminApiError("The direct-chat group prefix is missing or invalid in room-access.json.")
    return prefix


def require_demo_username(username: str) -> None:
    if not isinstance(username, str) or not DEMO_USERNAME_RE.fullmatch(username):
        raise AdminApiError("Only synthetic usernames beginning with 'demo-' are managed by this tool.")


def split_display_name(display_name: str) -> tuple[str, str]:
    parts = display_name.split(maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (display_name, "Demo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage demo users and room client roles in the foldfence-demo Keycloak realm."
    )
    parser.add_argument("--base-url", help="Keycloak base URL including /auth, or set KEYCLOAK_BASE_URL.")
    parser.add_argument("--realm", default=REALM_DEFAULT, help=f"Realm name (default: {REALM_DEFAULT}).")
    parser.add_argument("--auth-realm", help="Authentication realm; defaults to --realm. Use master explicitly for the private owner account.")
    parser.add_argument("--client-id", default=CLIENT_DEFAULT, help=f"Matrix client ID (default: {CLIENT_DEFAULT}).")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a synthetic user, or reconcile an existing one.")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--clearance", required=True, choices=sorted(known_clearances()))
    create.add_argument("--role", action="append", default=[], help="Room role; repeat to add multiple roles.")
    create.add_argument("--matrix-access", action="store_true", help="Explicitly grant Matrix application access; new users are unentitled by default.")

    update = commands.add_parser("update", help="Update display name and/or security clearance.")
    update.add_argument("--username", required=True)
    update.add_argument("--display-name")
    update.add_argument("--clearance", choices=sorted(known_clearances()))

    password = commands.add_parser("password", help="Set a user's password; prompt input is hidden.")
    password.add_argument("--username", required=True)

    roles = commands.add_parser("roles", help="Add or remove only managed room roles.")
    roles.add_argument("--username", required=True)
    roles.add_argument("--add", action="append", default=[], metavar="ROOM_ROLE")
    roles.add_argument("--remove", action="append", default=[], metavar="ROOM_ROLE")

    access = commands.add_parser("matrix-access", help="Grant or revoke only the Matrix application entitlement.")
    access.add_argument("--username", required=True)
    access_action = access.add_mutually_exclusive_group(required=True)
    access_action.add_argument("--grant", action="store_true")
    access_action.add_argument("--revoke", action="store_true", help="Remove the direct grant; policy-sync locks MAS and revokes Matrix sessions.")

    group = commands.add_parser("group", help="Manage only named top-level direct-chat policy groups.")
    group_actions = group.add_subparsers(dest="group_action", required=True)

    group_actions.add_parser("list", help="List all top-level groups in the configured direct-chat namespace.")

    group_create = group_actions.add_parser("create", help="Create a top-level direct-chat policy group.")
    group_create.add_argument("--name", required=True, help="Name starting with the configured direct-chat prefix.")

    for action, help_text in (
        ("add-user", "Add a synthetic demo user to a policy group by exact Keycloak group ID."),
        ("remove-user", "Remove a synthetic demo user from a policy group by exact Keycloak group ID."),
    ):
        membership = group_actions.add_parser(action, help=help_text)
        membership.add_argument("--group-id", required=True, help="Exact Keycloak group UUID from `group list`.")
        membership.add_argument("--username", required=True, help="Synthetic demo username.")

    group_actions.add_parser(
        "bootstrap-seeded",
        help="Explicitly create seeded policy groups and add seed memberships; never runs automatically.",
    )

    return parser.parse_args()


class KeycloakAdmin:
    def __init__(self, base_url: str, realm: str, client_id: str, username: str, password: str, auth_realm: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.realm = urllib.parse.quote(realm, safe="")
        self.auth_realm = urllib.parse.quote(auth_realm or realm, safe="")
        self.client_id = client_id
        self.username = username
        self.password = password
        self.token: str | None = None
        self._matrix_client_uuid: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        form: dict[str, str] | None = None,
        authenticate: bool = True,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticate and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read()
                if not payload:
                    return response.status, None
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type:
                    return response.status, json.loads(payload.decode("utf-8"))
                return response.status, payload.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # Avoid printing the response body: some identity providers include sensitive details.
            raise AdminApiError(f"Keycloak Admin REST request failed with HTTP {exc.code} ({method}).") from None
        except urllib.error.URLError:
            raise AdminApiError("Could not reach Keycloak. Check the base URL and that the demo is running.") from None
        except TimeoutError:
            raise AdminApiError("Keycloak request timed out.") from None

    def authenticate(self) -> None:
        token_path = f"/realms/{self.auth_realm}/protocol/openid-connect/token"
        _, token_doc = self._request(
            "POST",
            token_path,
            form={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.username,
                "password": self.password,
            },
            authenticate=False,
        )
        token = token_doc.get("access_token") if isinstance(token_doc, dict) else None
        if not isinstance(token, str) or not token:
            raise AdminApiError("Keycloak did not return an admin access token; check the demo admin account.")
        self.token = token

    def _realm_path(self, suffix: str = "") -> str:
        return f"/admin/realms/{self.realm}{suffix}"

    def matrix_client_uuid(self) -> str:
        if self._matrix_client_uuid:
            return self._matrix_client_uuid
        query = urllib.parse.urlencode({"clientId": self.client_id})
        _, clients = self._request("GET", self._realm_path(f"/clients?{query}"))
        matches = [item for item in clients if item.get("clientId") == self.client_id] if isinstance(clients, list) else []
        if len(matches) != 1 or not matches[0].get("id"):
            raise AdminApiError(f"Could not find exactly one Keycloak client named {self.client_id}.")
        self._matrix_client_uuid = matches[0]["id"]
        return self._matrix_client_uuid

    @property
    def matrix_client_uuid_value(self) -> str | None:
        return self._matrix_client_uuid

    @matrix_client_uuid_value.setter
    def matrix_client_uuid_value(self, value: str | None) -> None:
        self._matrix_client_uuid = value

    def find_user(self, username: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"username": username, "exact": "true"})
        _, users = self._request("GET", self._realm_path(f"/users?{query}"))
        if not isinstance(users, list):
            raise AdminApiError("Keycloak returned an unexpected user list.")
        exact = [item for item in users if item.get("username") == username]
        if len(exact) > 1:
            raise AdminApiError("Keycloak returned multiple users with the same username.")
        return exact[0] if exact else None

    def require_user(self, username: str) -> dict[str, Any]:
        user = self.find_user(username)
        if not user or not user.get("id"):
            raise AdminApiError(f"Demo user {username} was not found.")
        return user

    def create_user(self, username: str, display_name: str, clearance: str, password: str) -> dict[str, Any]:
        first_name, last_name = split_display_name(display_name)
        _, _ = self._request(
            "POST",
            self._realm_path("/users"),
            body={
                "username": username,
                "enabled": True,
                "email": f"{username}@foldfence-demo.invalid",
                "firstName": first_name,
                "lastName": last_name,
                "attributes": {"security_clearance": [clearance]},
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            },
        )
        return self.require_user(username)

    def update_user(self, user: dict[str, Any], *, display_name: str | None = None, clearance: str | None = None) -> None:
        first_name, last_name = split_display_name(display_name) if display_name is not None else (
            user.get("firstName", ""),
            user.get("lastName", ""),
        )
        payload = {
            "id": user["id"],
            "username": user["username"],
            "enabled": user.get("enabled", True),
            "email": user.get("email") or f"{user['username']}@foldfence-demo.invalid",
            "firstName": first_name,
            "lastName": last_name,
            "attributes": dict(user.get("attributes") or {}),
        }
        if clearance is not None:
            payload["attributes"]["security_clearance"] = [clearance]
        self._request("PUT", self._realm_path(f"/users/{urllib.parse.quote(user['id'], safe='')}"), body=payload)

    def set_password(self, user: dict[str, Any], password: str) -> None:
        credential = {"type": "password", "value": password, "temporary": False}
        user_id = urllib.parse.quote(user["id"], safe="")
        self._request("PUT", self._realm_path(f"/users/{user_id}/reset-password"), body=credential)

    def validate_room_role(self, name: str) -> None:
        parts = name.split(".")
        if len(parts) != 3 or parts[0] != "room" or parts[1] not in known_room_ids() or parts[2] not in {"reader", "writer", "moderator"}:
            raise AdminApiError(f"Not a managed demo room role: {name}")

    def room_role(self, name: str) -> dict[str, Any]:
        self.validate_room_role(name)
        client_uuid = urllib.parse.quote(self.matrix_client_uuid(), safe="")
        role_name = urllib.parse.quote(name, safe="")
        _, role = self._request("GET", self._realm_path(f"/clients/{client_uuid}/roles/{role_name}"))
        if not isinstance(role, dict) or role.get("name") != name or not role.get("id"):
            raise AdminApiError(f"Could not find managed room role {name}.")
        return role

    def update_roles(self, user: dict[str, Any], add: list[str], remove: list[str]) -> None:
        for name in add + remove:
            self.validate_room_role(name)
        overlap = set(add) & set(remove)
        if overlap:
            raise AdminApiError("A role cannot be both added and removed in one operation.")
        client_uuid = urllib.parse.quote(self.matrix_client_uuid(), safe="")
        user_id = urllib.parse.quote(user["id"], safe="")
        endpoint = self._realm_path(f"/users/{user_id}/role-mappings/clients/{client_uuid}")
        _, assigned = self._request("GET", endpoint)
        assigned_by_name = {item.get("name"): item for item in assigned if isinstance(item, dict)} if isinstance(assigned, list) else {}
        additions = [self.room_role(name) for name in add if name not in assigned_by_name]
        removals = [assigned_by_name[name] for name in remove if name in assigned_by_name]
        if additions:
            self._request("POST", endpoint, body=additions)
        if removals:
            self._request("DELETE", endpoint, body=removals)

    def update_matrix_access(self, user: dict[str, Any], *, grant: bool) -> dict[str, bool]:
        """Change one named realm entitlement; never accept arbitrary realm roles."""
        username = user.get("username")
        require_demo_username(username)
        if username in MATRIX_ACCESS_RESERVED_USERS or username.startswith("demo-admin-") or user.get("serviceAccountClientId"):
            raise AdminApiError("Matrix application access is managed only for ordinary synthetic humans.")
        if type(grant) is not bool:
            raise AdminApiError("Choose explicitly whether to grant or revoke Matrix access.")
        if not isinstance(user.get("id"), str) or not user["id"]:
            raise AdminApiError("The synthetic user has no exact Keycloak UUID.")
        _, role = self._request("GET", self._realm_path(f"/roles/{MATRIX_ACCESS_ROLE}"))
        if (not isinstance(role, dict) or role.get("name") != MATRIX_ACCESS_ROLE
                or not role.get("id") or role.get("clientRole") is not False):
            raise AdminApiError("Keycloak did not return the exact Matrix-access realm role.")
        user_id = urllib.parse.quote(user["id"], safe="")
        endpoint = self._realm_path(f"/users/{user_id}/role-mappings/realm")
        _, assigned = self._request("GET", endpoint)
        if not isinstance(assigned, list):
            raise AdminApiError("Keycloak returned an invalid direct realm-role mapping list.")
        direct = [item for item in assigned if isinstance(item, dict) and item.get("name") == MATRIX_ACCESS_ROLE]
        if len(direct) > 1 or direct and direct[0].get("id") != role["id"]:
            raise AdminApiError("The existing Matrix-access role mapping is ambiguous.")
        changed = (grant and not direct) or (not grant and bool(direct))
        if changed:
            self._request("POST" if grant else "DELETE", endpoint, body=[role])
        _, effective = self._request("GET", endpoint + "/composite")
        if not isinstance(effective, list):
            raise AdminApiError("Keycloak returned an invalid effective realm-role list.")
        has_access = any(isinstance(item, dict) and item.get("name") == MATRIX_ACCESS_ROLE for item in effective)
        if has_access is not grant:
            if not grant:
                raise AdminApiError("Matrix access is still inherited; remove its group/composite grant before claiming revocation.")
            raise AdminApiError("Keycloak did not confirm the effective Matrix-access grant.")
        return {"role_mapping_changed": bool(changed), "await_policy_sync": True}

    def validate_policy_group_name(self, name: str) -> None:
        prefix = policy_group_prefix()
        if not isinstance(name, str) or not name.startswith(prefix):
            raise AdminApiError(f"Policy group name must start with {prefix!r} and have a non-empty top-level scope.")
        suffix = name[len(prefix):]
        if not suffix.strip() or "/" in suffix:
            raise AdminApiError(f"Policy group name must start with {prefix!r} and have a non-empty top-level scope.")

    @staticmethod
    def _is_top_level_group(group: dict[str, Any]) -> bool:
        name = group.get("name")
        path = group.get("path")
        if isinstance(path, str):
            return path == f"/{name}"
        return not group.get("parentId")

    def _paginated_groups(self, endpoint: str, description: str) -> list[dict[str, Any]]:
        page_size = 100
        first = 0
        groups: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        while first < 50000:
            query = urllib.parse.urlencode({"first": first, "max": page_size, "briefRepresentation": "false"})
            _, page = self._request("GET", f"{endpoint}?{query}")
            if not isinstance(page, list):
                raise AdminApiError(f"Keycloak returned an unexpected {description} page.")
            new_count = 0
            for group in page:
                if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"]:
                    raise AdminApiError(f"Keycloak returned a {description} without an exact group ID.")
                if group["id"] in seen_ids:
                    continue
                seen_ids.add(group["id"])
                groups.append(group)
                new_count += 1
            if len(page) < page_size:
                return groups
            if new_count == 0:
                raise AdminApiError(f"Keycloak repeated a full {description} page; refusing incomplete results.")
            first += page_size
        raise AdminApiError(f"Keycloak {description} exceeded the safety limit of 50000 entries.")

    def list_policy_groups(self) -> list[dict[str, Any]]:
        prefix = policy_group_prefix()
        groups = self._paginated_groups(self._realm_path("/groups"), "top-level group list")
        return sorted(
            [
                group for group in groups
                if isinstance(group.get("name"), str)
                and group["name"].startswith(prefix)
                and len(group["name"]) > len(prefix)
                and self._is_top_level_group(group)
            ],
            key=lambda group: (group["name"], group["id"]),
        )

    def require_policy_group(self, group_id: str) -> dict[str, Any]:
        if not isinstance(group_id, str) or not group_id.strip():
            raise AdminApiError("Provide the exact Keycloak group ID from `group list`.")
        encoded_id = urllib.parse.quote(group_id, safe="")
        _, group = self._request("GET", self._realm_path(f"/groups/{encoded_id}"))
        if not isinstance(group, dict) or group.get("id") != group_id:
            raise AdminApiError("Keycloak did not return the requested exact group ID.")
        self.validate_policy_group_name(group.get("name", ""))
        if not self._is_top_level_group(group):
            raise AdminApiError("Only top-level direct-chat policy groups can be managed by this command.")
        return group

    def create_policy_group(self, name: str) -> tuple[dict[str, Any], bool]:
        self.validate_policy_group_name(name)
        matches = [group for group in self.list_policy_groups() if group.get("name") == name]
        if len(matches) > 1:
            raise AdminApiError("Keycloak contains multiple top-level policy groups with the same name.")
        if matches:
            return matches[0], False
        self._request("POST", self._realm_path("/groups"), body={"name": name})
        created = [group for group in self.list_policy_groups() if group.get("name") == name]
        if len(created) != 1:
            raise AdminApiError("Keycloak did not return exactly one group after creation.")
        return created[0], True

    def list_user_groups(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        user_id = urllib.parse.quote(user["id"], safe="")
        return self._paginated_groups(self._realm_path(f"/users/{user_id}/groups"), "user group membership list")

    def add_user_to_policy_group(self, username: str, group_id: str) -> bool:
        require_demo_username(username)
        group = self.require_policy_group(group_id)
        user = self.require_user(username)
        memberships = self.list_user_groups(user)
        if any(item.get("id") == group["id"] for item in memberships):
            return False
        user_id = urllib.parse.quote(user["id"], safe="")
        exact_group_id = urllib.parse.quote(group["id"], safe="")
        self._request("PUT", self._realm_path(f"/users/{user_id}/groups/{exact_group_id}"))
        return True

    def remove_user_from_policy_group(self, username: str, group_id: str) -> bool:
        require_demo_username(username)
        group = self.require_policy_group(group_id)
        user = self.require_user(username)
        memberships = self.list_user_groups(user)
        if not any(item.get("id") == group["id"] for item in memberships):
            return False
        user_id = urllib.parse.quote(user["id"], safe="")
        exact_group_id = urllib.parse.quote(group["id"], safe="")
        self._request("DELETE", self._realm_path(f"/users/{user_id}/groups/{exact_group_id}"))
        return True

    def bootstrap_seeded_policy_groups(self) -> tuple[int, int]:
        seed = json.loads((ROOT / "config/seed/demo-users.json").read_text(encoding="utf-8"))
        specs = seed.get("keycloak_groups")
        users = seed.get("users")
        if not isinstance(specs, list) or not isinstance(users, list):
            raise AdminApiError("The seed must define keycloak_groups and users lists.")
        names: list[str] = []
        for spec in specs:
            name = spec.get("name") if isinstance(spec, dict) else None
            if not isinstance(name, str):
                raise AdminApiError("Every keycloak_groups seed entry must contain a group name.")
            self.validate_policy_group_name(name)
            if name in names:
                raise AdminApiError(f"The seed repeats policy group {name}.")
            names.append(name)

        groups_by_name: dict[str, dict[str, Any]] = {}
        created_count = 0
        for name in names:
            group, created = self.create_policy_group(name)
            groups_by_name[name] = group
            created_count += int(created)

        added_count = 0
        for seed_user in users:
            if not isinstance(seed_user, dict):
                raise AdminApiError("The demo user seed contains a non-object entry.")
            username = seed_user.get("username")
            group_paths = seed_user.get("groups", [])
            if not isinstance(group_paths, list):
                raise AdminApiError(f"Seeded groups for {username or 'a user'} must be a list.")
            if not group_paths:
                continue
            require_demo_username(username)
            for group_path in group_paths:
                if not isinstance(group_path, str) or not group_path.startswith("/") or group_path.count("/") != 1:
                    raise AdminApiError(f"Seeded policy group for {username} must be a top-level path like /1to1chat.scope.")
                name = group_path[1:]
                self.validate_policy_group_name(name)
                if name not in groups_by_name:
                    raise AdminApiError(f"User {username} references an undeclared seeded policy group.")
                added_count += int(self.add_user_to_policy_group(username, groups_by_name[name]["id"]))
        return created_count, added_count


def prompt_password() -> str:
    password = getpass.getpass("Demo user password (input hidden): ")
    confirm = getpass.getpass("Confirm demo user password: ")
    if password != confirm:
        raise AdminApiError("The password entries did not match.")
    if len(password) < 12:
        raise AdminApiError("Use a demo password with at least 12 characters.")
    return password


def run() -> int:
    args = parse_args()
    env = project_env()
    base_url = (args.base_url or env.get("KEYCLOAK_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise AdminApiError("Set KEYCLOAK_BASE_URL or pass --base-url (include the /auth path when configured).")
    admin_name = env.get("KEYCLOAK_ADMIN_USERNAME", "").strip()
    admin_password = env.get("KEYCLOAK_ADMIN_PASSWORD", "").strip()
    if not admin_name:
        admin_name = input("Keycloak admin username: ").strip()
    if not admin_password:
        admin_password = getpass.getpass("Keycloak admin password (input hidden): ")

    admin = KeycloakAdmin(base_url, args.realm, args.client_id, admin_name, admin_password, auth_realm=args.auth_realm)
    admin.authenticate()

    if args.command == "create":
        require_demo_username(args.username)
        if args.matrix_access and (args.username in MATRIX_ACCESS_RESERVED_USERS or args.username.startswith("demo-admin-")):
            raise AdminApiError("Matrix application access is managed only for ordinary synthetic humans.")
        for role in args.role:
            admin.validate_room_role(role)
        user = admin.find_user(args.username)
        if user is None:
            password = seeded_password_for_user(env, args.username) or prompt_password()
            user = admin.create_user(args.username, args.display_name, args.clearance, password)
            print(f"Created demo user {args.username}.")
        else:
            admin.update_user(user, display_name=args.display_name, clearance=args.clearance)
            print(f"Updated demo user {args.username}; existing password was left unchanged.")
        admin.update_roles(user, args.role, [])
        if args.matrix_access:
            admin.update_matrix_access(user, grant=True)
            print(f"Granted explicit Matrix application access for {args.username}.")
        if args.role:
            print(f"Ensured {len(set(args.role))} requested room role(s) for {args.username}.")
    elif args.command == "update":
        require_demo_username(args.username)
        if args.display_name is None and args.clearance is None:
            raise AdminApiError("Provide --display-name and/or --clearance.")
        user = admin.require_user(args.username)
        admin.update_user(user, display_name=args.display_name, clearance=args.clearance)
        print(f"Updated demo user {args.username}.")
    elif args.command == "password":
        require_demo_username(args.username)
        user = admin.require_user(args.username)
        admin.set_password(user, prompt_password())
        print(f"Updated password for demo user {args.username}.")
    elif args.command == "roles":
        require_demo_username(args.username)
        if not args.add and not args.remove:
            raise AdminApiError("Provide at least one --add or --remove role.")
        user = admin.require_user(args.username)
        admin.update_roles(user, args.add, args.remove)
        print(f"Reconciled requested room roles for {args.username}.")
    elif args.command == "matrix-access":
        require_demo_username(args.username)
        user = admin.require_user(args.username)
        admin.update_matrix_access(user, grant=args.grant)
        if args.grant:
            print(f"Granted Matrix application access for {args.username}.")
        else:
            print(f"Removed Matrix application access for {args.username}. Policy-sync must lock MAS and revoke Matrix sessions; verify access/refresh denial after convergence. Other Keycloak sessions are unchanged.")
    elif args.command == "group":
        if args.group_action == "list":
            groups = admin.list_policy_groups()
            if not groups:
                print("No top-level direct-chat policy groups found.")
            else:
                print("GROUP ID\tNAME\tPATH")
                for group in groups:
                    print(f"{group['id']}\t{group['name']}\t{group.get('path', '')}")
        elif args.group_action == "create":
            group, created = admin.create_policy_group(args.name)
            action = "Created" if created else "Already exists"
            print(f"{action} direct-chat policy group {group['name']} (ID: {group['id']}).")
        elif args.group_action in {"add-user", "remove-user"}:
            if args.group_action == "add-user":
                changed = admin.add_user_to_policy_group(args.username, args.group_id)
                action = "Added" if changed else "Already a member"
            else:
                changed = admin.remove_user_from_policy_group(args.username, args.group_id)
                action = "Removed" if changed else "Was not a member"
            print(f"{action}: {args.username} in direct-chat policy group {args.group_id}.")
        elif args.group_action == "bootstrap-seeded":
            created_count, added_count = admin.bootstrap_seeded_policy_groups()
            print(f"Seed bootstrap complete: created {created_count} policy group(s), added {added_count} user membership(s).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except AdminApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
