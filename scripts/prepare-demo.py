#!/usr/bin/env python3
"""Prepare private runtime files from the tracked demo inputs."""

from __future__ import annotations

import io
import json
import importlib.util
import ipaddress
import os
import secrets
import shlex
import shutil
import ssl
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_VALUE = Path(os.environ.get("DEMO_ENV_FILE", ".env"))
ENV_FILE = (ROOT / ENV_FILE_VALUE).resolve() if not ENV_FILE_VALUE.is_absolute() else ENV_FILE_VALUE.resolve()

SECRET_VARIABLES = (
    "KEYCLOAK_BOOTSTRAP_PASSWORD",
    "KEYCLOAK_DB_PASSWORD",
    "SYNAPSE_DB_PASSWORD",
    "SYNAPSE_MACAROON_SECRET",
    "SYNAPSE_FORM_SECRET",
    "SYNAPSE_CHAT_POLICY_SECRET",
    "MAS_DB_PASSWORD",
    "MAS_SYNAPSE_SHARED_SECRET",
    "MAS_KEYCLOAK_CLIENT_SECRET",
    "MAS_PROVISIONER_API_TOKEN",
    "KEYCLOAK_POLICY_CLIENT_SECRET",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_AS_TOKEN",
    "LIVEKIT_HS_TOKEN",
    "ORG_BOT_EXPORT_TOKEN",
)
MAS_PROVIDER_ID = "01JHF4Q2XQ5A0G3J7K8M9N2P1R"
KEYCLOAK_DEMO_ADMIN_USERS = (
    ("demo-admin-01", "Demo Realm Administrator 01"),
    ("demo-admin-02", "Demo Realm Administrator 02"),
)
KEYCLOAK_DEMO_ADMIN_ROLES = (
    "manage-users",
    "view-users",
    "query-users",
    "query-groups",
    "view-realm",
    "view-clients",
    "query-clients",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for source in (ROOT / ".env.example", ENV_FILE):
        if not source.exists():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key] = value

    for name in SECRET_VARIABLES:
        if result.get(name, "").strip() in ("", "CHANGE_ME"):
            result[name] = secrets.token_urlsafe(30)
    # OrgBot uses a provisioner-issued Matrix session file; there is no bot
    # password to generate or preserve in the private environment.
    result.pop("ORG_BOT_PASSWORD", None)
    password_mode = result.get("DEMO_USER_PASSWORD_MODE", "individual").strip().lower()
    if password_mode not in {"individual", "shared-legacy"}:
        raise SystemExit("DEMO_USER_PASSWORD_MODE must be individual or shared-legacy.")
    result["DEMO_USER_PASSWORD_MODE"] = password_mode
    if password_mode == "shared-legacy":
        if result.get("DEMO_USER_PASSWORD", "").strip() in ("", "CHANGE_ME"):
            result["DEMO_USER_PASSWORD"] = secrets.token_urlsafe(30)
    else:
        # Individual credentials live only in the target's private generated
        # handoff manifest; do not keep an unused shared password in .env.
        result.pop("DEMO_USER_PASSWORD", None)
    target = result.get("DEMO_TARGET", "local").strip().lower()
    if not target or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in target):
        raise SystemExit("DEMO_TARGET must use lowercase letters, digits, underscores, or hyphens.")
    result["DEMO_TARGET"] = target
    if result.get("LIVEKIT_API_KEY", "").strip() in ("", "CHANGE_ME"):
        result["LIVEKIT_API_KEY"] = "demo" + secrets.token_hex(4)
    normalize_public_urls(result)
    return result


def private_generated_dir(env: dict[str, str]) -> Path:
    """Resolve a target-specific generated directory relative to the Compose file."""
    configured = env.get("DEMO_GENERATED_DIR", "../../internal/generated").strip()
    if not configured:
        raise SystemExit("DEMO_GENERATED_DIR must name a private generated directory.")
    path = Path(configured)
    if path.is_absolute():
        raise SystemExit("DEMO_GENERATED_DIR must be relative to deployment/compose/.")
    resolved = (ROOT / "deployment" / "compose" / path).resolve()
    try:
        resolved.relative_to((ROOT / "internal").resolve())
    except ValueError:
        raise SystemExit("DEMO_GENERATED_DIR must remain inside the private internal/ directory.") from None
    return resolved


def normalize_public_urls(env: dict[str, str]) -> None:
    hostname = env.get("DEMO_HOSTNAME", "").strip()
    if not hostname or ":" in hostname or "://" in hostname or "/" in hostname or any(char.isspace() for char in hostname):
        raise SystemExit("DEMO_HOSTNAME must be a DNS name or IPv4 address without a scheme, port, or path.")
    https_port = int(env.get("DEMO_HTTPS_PORT", "21123"))
    http_port = int(env.get("DEMO_HTTP_PORT", "21122"))
    if not (1 <= https_port <= 65535 and 1 <= http_port <= 65535):
        raise SystemExit("DEMO_HTTP_PORT and DEMO_HTTPS_PORT must be valid TCP ports.")
    if https_port == 80:
        raise SystemExit("DEMO_HTTPS_PORT cannot use port 80, which the proxy reserves for HTTP.")
    if https_port == http_port:
        raise SystemExit("DEMO_HTTP_PORT and DEMO_HTTPS_PORT must be different.")

    authority = hostname if https_port == 443 else f"{hostname}:{https_port}"
    base_url = f"https://{authority}"
    auth_hostname = env.get("MATRIX_AUTH_HOSTNAME", "").strip()
    if not auth_hostname or auth_hostname in {"CHANGE_ME", "auth.demo.example.invalid"}:
        auth_hostname = hostname
    if "://" in auth_hostname or "/" in auth_hostname or ":" in auth_hostname or any(char.isspace() for char in auth_hostname):
        raise SystemExit("MATRIX_AUTH_HOSTNAME must be a DNS name without a scheme, port, or path.")
    auth_authority = auth_hostname if https_port == 443 else f"{auth_hostname}:{https_port}"
    if env.get("MAS_UPSTREAM_PROVIDER_ID", "").strip() in ("", "CHANGE_ME"):
        env["MAS_UPSTREAM_PROVIDER_ID"] = MAS_PROVIDER_ID
    if env["MAS_UPSTREAM_PROVIDER_ID"] != MAS_PROVIDER_ID:
        raise SystemExit("MAS_UPSTREAM_PROVIDER_ID is fixed for this seeded demo identity mapping.")
    env.update(
        {
            "DEMO_BASE_URL": base_url,
            "MATRIX_SERVER_NAME": (
                hostname
                if env.get("MATRIX_SERVER_NAME", "").strip() in ("", "CHANGE_ME", "demo.example.invalid")
                else env["MATRIX_SERVER_NAME"].strip()
            ),
            "MATRIX_PUBLIC_URL": base_url,
            "MATRIX_AUTH_HOSTNAME": auth_hostname,
            "MATRIX_AUTH_PUBLIC_URL": f"https://{auth_authority}/mas",
            "KEYCLOAK_PUBLIC_URL": f"{base_url}/auth",
            "KEYCLOAK_ISSUER_URL": f"{base_url}/auth/realms/foldfence-demo",
            "ELEMENT_PUBLIC_URL": base_url,
            "LIVEKIT_PUBLIC_URL": f"wss://{authority}/livekit/sfu",
            "LIVEKIT_JWT_PUBLIC_URL": f"{base_url}/livekit/jwt",
        }
    )


def write_env(env: dict[str, str]) -> None:
    ENV_FILE.write_text(
        "# Private generated demo settings. Keep this file out of Git.\n"
        + "\n".join(f"{key}={value}" for key, value in env.items())
        + "\n",
        encoding="utf-8",
    )
    ENV_FILE.chmod(0o600)


def credential_manifest_path(private: Path) -> Path:
    return private / "handoff" / "credential-manifest.json"


def private_json_write(path: Path, document: dict) -> None:
    """Write credential material atomically with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.is_symlink():
        raise SystemExit("The private credential manifest path must not be a symbolic link.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".credential-manifest-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(document, output, indent=2, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def private_text_write(path: Path, content: str) -> None:
    """Write a private text credential list without a permissive creation window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SystemExit("The private credential list path must not be a symbolic link.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".demo-credentials-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _existing_manifest_passwords(path: Path, target: str, password_mode: str) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise SystemExit("The private credential manifest path must not be a symbolic link.")
    # Tighten permissions before reading a pre-existing manifest made by an
    # older generator or handoff helper.
    path.chmod(0o600)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit("The existing private credential manifest is unreadable; refusing to replace it.") from None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise SystemExit("The existing private credential manifest has an unsupported schema version.")
    if manifest.get("target") != target:
        raise SystemExit("The private credential manifest belongs to a different deployment target.")
    if manifest.get("password_mode") != password_mode:
        raise SystemExit("The credential mode changed; use a fresh target generated directory instead of rotating users silently.")
    accounts = manifest.get("accounts")
    if not isinstance(accounts, list):
        raise SystemExit("The existing private credential manifest has an invalid accounts list.")
    passwords: dict[str, str] = {}
    for account in accounts:
        if not isinstance(account, dict) or account.get("kind") != "human":
            continue
        username = account.get("username")
        password = account.get("password")
        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
            raise SystemExit("The existing private credential manifest has an incomplete human account entry.")
        if username in passwords:
            raise SystemExit("The existing private credential manifest repeats a human username.")
        passwords[username] = password
    return passwords


def ensure_user_passwords(private: Path, env: dict[str, str], users_doc: dict) -> dict[str, str]:
    """Return stable per-user passwords, generating only missing individual credentials."""
    users = users_doc.get("users")
    if not isinstance(users, list):
        raise SystemExit("The demo user seed must contain a users list.")
    usernames = [user.get("username") for user in users if isinstance(user, dict)]
    if len(usernames) != len(users) or any(not isinstance(name, str) or not name for name in usernames):
        raise SystemExit("Every demo user seed entry must have a username.")
    if len(set(usernames)) != len(usernames):
        raise SystemExit("The demo user seed contains duplicate usernames.")

    target = env.get("DEMO_TARGET", private.name).strip()
    password_mode = env.get("DEMO_USER_PASSWORD_MODE", "individual").strip().lower()
    if password_mode not in {"individual", "shared-legacy"}:
        raise SystemExit("DEMO_USER_PASSWORD_MODE must be individual or shared-legacy.")
    manifest_path = credential_manifest_path(private)
    existing = _existing_manifest_passwords(manifest_path, target, password_mode)

    if password_mode == "shared-legacy":
        shared_password = env.get("DEMO_USER_PASSWORD", "").strip()
        if not shared_password or shared_password == "CHANGE_ME":
            raise SystemExit("Shared-legacy mode requires a generated or configured DEMO_USER_PASSWORD.")
        if any(password != shared_password for password in existing.values()):
            raise SystemExit("The existing manifest does not match the configured shared-legacy password; refusing to reset users.")
        return {username: shared_password for username in usernames}

    passwords = {username: existing[username] for username in usernames if username in existing}
    for username in usernames:
        if username not in passwords:
            passwords[username] = secrets.token_urlsafe(24)
    if len(set(passwords.values())) != len(passwords):
        raise SystemExit("Individual demo user passwords must be unique; use a fresh generated directory to repair the bundle.")
    return passwords


def ensure_demo_admin_passwords(
    private: Path,
    env: dict[str, str],
    reserved_passwords=(),
) -> dict[str, str]:
    """Add stable private passwords for the two Keycloak-only demo admins."""
    manifest_path = credential_manifest_path(private)
    reserved = set(reserved_passwords)
    existing = {}
    if manifest_path.exists():
        if manifest_path.is_symlink():
            raise SystemExit("The private credential manifest path must not be a symbolic link.")
        manifest_path.chmod(0o600)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SystemExit("The existing private credential manifest is unreadable; refusing to rotate demo admins.") from None
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise SystemExit("The existing private credential manifest has an unsupported schema version.")
        if manifest.get("target") != env.get("DEMO_TARGET", private.name):
            raise SystemExit("The private credential manifest belongs to a different deployment target.")
        if manifest.get("password_mode") != env.get("DEMO_USER_PASSWORD_MODE", "individual"):
            raise SystemExit("The credential mode changed; refusing to rotate demo admins silently.")
        previous = manifest.get("keycloak_demo_admins")
        if previous is not None:
            if not isinstance(previous, list) or len(previous) != len(KEYCLOAK_DEMO_ADMIN_USERS):
                raise SystemExit("The existing manifest has an incomplete Keycloak demo admin password list.")
            expected = {item[0] for item in KEYCLOAK_DEMO_ADMIN_USERS}
            for account in previous:
                if not isinstance(account, dict):
                    raise SystemExit("The existing manifest has an invalid Keycloak demo admin entry.")
                username = account.get("username")
                password = account.get("password")
                if username not in expected or not isinstance(password, str) or not password:
                    raise SystemExit("The existing manifest has an incomplete Keycloak demo admin entry.")
                if username in existing:
                    raise SystemExit("The existing manifest repeats a Keycloak demo admin.")
                existing[username] = password
            if set(existing) != expected:
                raise SystemExit("The existing manifest does not contain both Keycloak demo admins.")

    if len(set(existing.values())) != len(existing) or any(password in reserved for password in existing.values()):
        raise SystemExit("Existing Keycloak demo admin passwords conflict; refusing to rotate them silently.")
    passwords = dict(existing)
    occupied = reserved | set(passwords.values())
    for username, _display_name in KEYCLOAK_DEMO_ADMIN_USERS:
        if username not in passwords:
            password = secrets.token_urlsafe(24)
            while password in occupied:
                password = secrets.token_urlsafe(24)
            passwords[username] = password
            occupied.add(password)
    return passwords


def safe_credential_data(
    env: dict[str, str],
    brand: dict,
    users_doc: dict,
    user_passwords: dict[str, str],
    target: str,
    password_mode: str,
    demo_admin_passwords: dict[str, str] | None = None,
) -> dict:
    """Build only the human credential handoff fields; exclude service secrets and tokens."""
    element_url = env.get("ELEMENT_PUBLIC_URL", env.get("MATRIX_PUBLIC_URL", "")).rstrip("/")
    matrix_url = env.get("MATRIX_PUBLIC_URL", element_url).rstrip("/")
    keycloak_base = env.get("KEYCLOAK_PUBLIC_URL", "").rstrip("/")
    realm_url = f"{keycloak_base}/realms/foldfence-demo" if keycloak_base else ""
    urls = {
        "matrix_homeserver": matrix_url,
        "element_web": element_url,
        "matrix_authentication_service": env.get("MATRIX_AUTH_PUBLIC_URL", "").rstrip("/"),
        "keycloak_realm": realm_url,
        "keycloak_admin_master": f"{keycloak_base}/admin/master/console/" if keycloak_base else "",
        "keycloak_admin_realm": f"{keycloak_base}/admin/foldfence-demo/console/" if keycloak_base else "",
        "export_portal": f"{element_url}/export/" if element_url else "",
    }
    server_name = env.get("MATRIX_SERVER_NAME", "").strip()
    if not server_name:
        raise SystemExit("MATRIX_SERVER_NAME is required for the private credential manifest.")
    if not env.get("KEYCLOAK_BOOTSTRAP_ADMIN", "").strip() or not env.get("KEYCLOAK_BOOTSTRAP_PASSWORD", "").strip():
        raise SystemExit("Keycloak bootstrap admin credentials are required for the private credential manifest.")
    matrix_access = read_json(ROOT / "config" / "keycloak" / "matrix-access.json")
    matrix_access_role = matrix_access.get("role", {}).get("name")
    if matrix_access_role != "matrix-access":
        raise SystemExit("The seeded Matrix login entitlement must use the matrix-access realm role.")

    expected_demo_admins = {username for username, _display_name in KEYCLOAK_DEMO_ADMIN_USERS}
    if not isinstance(demo_admin_passwords, dict) or set(demo_admin_passwords) != expected_demo_admins:
        raise SystemExit("Both Keycloak demo admin passwords must be generated into the private manifest.")
    admin_password_values = list(demo_admin_passwords.values())
    if any(not isinstance(password, str) or not password for password in admin_password_values):
        raise SystemExit("Keycloak demo admin passwords must be non-empty strings.")
    if (
        len(set(admin_password_values)) != len(admin_password_values)
        or set(admin_password_values) & (set(user_passwords.values()) | {env["KEYCLOAK_BOOTSTRAP_PASSWORD"]})
    ):
        raise SystemExit("Keycloak demo admin passwords must be unique from the root and demo user passwords.")
    reserved_usernames = {
        env.get("KEYCLOAK_BOOTSTRAP_ADMIN", "admin"),
        env.get("ORG_BOT_USERNAME", "orgbot"),
        env.get("SYNAPSE_ADMIN_USER", "demo-policy-admin"),
        *(user.get("username") for user in users_doc.get("users", []) if isinstance(user, dict)),
    }
    if expected_demo_admins & reserved_usernames:
        raise SystemExit("A Keycloak demo admin username conflicts with an existing seeded or service identity.")

    keycloak_demo_admins = [
        {
            "username": username,
            "display_name": display_name,
            "password": demo_admin_passwords[username],
            "interactive_login_allowed": True,
            "login_url": urls["keycloak_admin_realm"],
            "realm_management_roles": list(KEYCLOAK_DEMO_ADMIN_ROLES),
            "notes": "Keycloak demo realm administrator; no Matrix clearance, room roles, groups, or Matrix user ID",
        }
        for username, display_name in KEYCLOAK_DEMO_ADMIN_USERS
    ]

    human_accounts = []
    for user in users_doc.get("users", []):
        username = user["username"]
        password = user_passwords.get(username)
        if not isinstance(password, str) or not password:
            raise SystemExit(f"No private password is available for seeded demo user {username}.")
        human_accounts.append(
            {
                "kind": "human",
                "username": username,
                "matrix_user_id": f"@{username}:{server_name}",
                "display_name": user.get("display_name", username),
                "password": password,
                "clearance": user.get("clearance", ""),
                "room_roles": list(user.get("room_roles", [])),
                # This handoff mirrors the fixed realm import seed. New users
                # created through the user-management flow are not in users_doc
                # and receive no entitlement by default.
                "realm_roles": list(dict.fromkeys([*user.get("realm_roles", []), matrix_access_role])),
                "groups": list(user.get("groups", [])),
                "interactive_login_allowed": True,
                "login_url": urls["element_web"],
            }
        )

    bot_username = env.get("ORG_BOT_USERNAME", "orgbot").strip()
    bot = {
        "kind": "service",
        "service": "orgbot",
        "username": bot_username,
        "matrix_user_id": f"@{bot_username}:{server_name}",
        "display_name": env.get("ORG_BOT_DISPLAY_NAME", "Organization Demo Export Bot"),
        "password": None,
        "interactive_login_allowed": False,
    }
    policy_admin_username = env.get("SYNAPSE_ADMIN_USER", "demo-policy-admin").strip()
    policy_admin = {
        "kind": "policy_admin",
        "username": policy_admin_username,
        "matrix_user_id": f"@{policy_admin_username}:{server_name}",
        "display_name": "Matrix policy service administrator",
        "password": None,
        "interactive_login_allowed": False,
    }
    return {
        "schema_version": 1,
        "target": target,
        "password_mode": password_mode,
        "status": "prepared_import_not_confirmed_active",
        "product_name": brand.get("product_name", "Organization Messenger"),
        "urls": urls,
        "keycloak_admin": {
            "username": env.get("KEYCLOAK_BOOTSTRAP_ADMIN", "admin"),
            "password": env.get("KEYCLOAK_BOOTSTRAP_PASSWORD", ""),
            "login_url": urls["keycloak_admin_master"],
        },
        "keycloak_demo_admins": keycloak_demo_admins,
        "accounts": [*human_accounts, bot, policy_admin],
    }


def write_private_credentials(private: Path, manifest: dict) -> None:
    """Write the readable credential list without printing secret values to stdout."""
    credentials = private.parent / "demo-credentials.txt"
    lines = [
        f"{manifest.get('product_name', 'Organization Messenger')} — private demo credentials",
        "",
        "Synthetic demo accounts. These credentials are prepared for the realm import; preparation alone does not prove they are active on a running service.",
        f"Target: {manifest['target']}",
        f"Password mode: {manifest['password_mode']}",
        "",
        "Service links:",
    ]
    lines.extend(f"  {name}: {value}" for name, value in manifest["urls"].items() if value)
    admin = manifest["keycloak_admin"]
    lines.extend(
        [
            "",
            "Keycloak administrator:",
            f"  Username: {admin['username']}",
            f"  Password: {admin['password']}",
            f"  Login: {admin['login_url']}",
            "",
            "Keycloak demo-realm administrators:",
        ]
    )
    for account in manifest.get("keycloak_demo_admins", []):
        lines.extend(
            [
                f"  {account['display_name']} — {account['username']}",
                f"    Password: {account['password']}",
                f"    Keycloak realm-management roles: {', '.join(account['realm_management_roles'])}",
                f"    No Matrix clearance, room roles, groups, or Matrix user ID.",
                f"    Login: {account['login_url']}",
            ]
        )
    lines.extend(["", "Human demo users:"])
    for account in manifest["accounts"]:
        if account.get("kind") != "human":
            continue
        lines.extend(
            [
                f"  {account['display_name']} — {account['username']} ({account['matrix_user_id']})",
                f"    Password: {account['password']}",
                f"    Clearance label: {account['clearance']}",
                f"    Room roles: {', '.join(account['room_roles']) or 'none'}",
                f"    Keycloak groups: {', '.join(account['groups']) or 'none'}",
                f"    Login: {account['login_url']}",
            ]
        )
    lines.extend(["", "Noninteractive service identities:"])
    for account in manifest["accounts"]:
        if account.get("kind") == "human":
            continue
        lines.append(f"  {account['display_name']} — {account['matrix_user_id']} (interactive login disabled; no human password)")
    private_text_write(credentials, "\n".join(lines) + "\n")


def brand_config(env: dict[str, str]) -> dict:
    relative = env.get("DEMO_BRAND_CONFIG", "config/branding/brand.json")
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        raise SystemExit("DEMO_BRAND_CONFIG must point to a file inside the project directory.") from None
    if not path.is_file():
        raise SystemExit(f"Configured brand file does not exist: {relative}")
    return read_json(path)


def brand_values(raw: dict) -> dict:
    if "brand" in raw:
        result = dict(raw["brand"])
        targets = raw.get("targets", {})
        keycloak = targets.get("keycloak", {}) if isinstance(targets, dict) else {}
        element = targets.get("element_web", {}) if isinstance(targets, dict) else {}
        result.setdefault("keycloak", {})
        result["keycloak"].setdefault(
            "theme_name",
            keycloak.get("login_theme", "fold-fence-demo") if isinstance(keycloak, dict) else "fold-fence-demo",
        )
        result.setdefault("element_web", {})
        if isinstance(element, dict):
            result["element_web"].update(element)
        return result
    return raw


def deterministic_id(username: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"matrix-keycloak-demo:{username}"))


def split_display_name(display_name: str) -> tuple[str, str]:
    parts = display_name.split(maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (display_name, "Demo")


def asset_suffix(asset: str | None, default: str = ".svg") -> str:
    suffix = Path(asset or "").suffix.lower()
    return suffix if suffix in {".svg", ".png", ".jpg", ".jpeg", ".webp"} else default


def render_keycloak(
    private: Path,
    env: dict[str, str],
    brand: dict,
    users_doc: dict,
    policy: dict,
    user_passwords: dict[str, str] | None = None,
    demo_admin_passwords: dict[str, str] | None = None,
) -> None:
    realm_name = "foldfence-demo"
    client_id = "matrix-synapse"
    sync_client_id = "matrix-policy-sync"
    mas_client_id = "matrix-authentication-service"
    matrix_access = read_json(ROOT / "config" / "keycloak" / "matrix-access.json")
    if matrix_access.get("role", {}).get("name") != "matrix-access" or matrix_access.get("client_id") != mas_client_id:
        raise ValueError("Matrix access must be a realm role scoped to the MAS upstream client")
    oidc_scopes = read_json(ROOT / "config" / "keycloak" / "mas-client-scopes.json")
    export_scopes = read_json(ROOT / "config" / "keycloak" / "export-client-scopes.json")
    if export_scopes.get("client_id") != "matrix-export-client" or export_scopes.get("realm_roles") != ["org-export-admin"]:
        raise ValueError("Export client scope must grant only the organization export role")
    if oidc_scopes.get("client_id") != mas_client_id:
        raise ValueError("MAS OIDC client scopes are assigned to an unexpected Keycloak client")
    scope_names = {scope.get("name") for scope in oidc_scopes.get("client_scopes", [])}
    if set(oidc_scopes.get("default_client_scopes", [])) != scope_names:
        raise ValueError("Every MAS default client scope must have a configured definition")
    client_uuid = deterministic_id(client_id)
    sync_uuid = deterministic_id(sync_client_id)

    client_roles = []
    for room in policy["rooms"]:
        for role in policy["roles"]:
            client_roles.append({"name": f"room.{room['id']}.{role}", "description": f"{role} access in {room['name']}"})

    protocol_mappers = [
        {
            "name": "security-clearance",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "consentRequired": False,
            "config": {
                "user.attribute": "security_clearance",
                "claim.name": "security_clearance",
                "jsonType.label": "String",
                "multivalued": "false",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
            },
        }
    ]

    if user_passwords is None:
        # Compatibility for isolated callers and older offline tests. The
        # normal preparation path always supplies the target password bundle.
        legacy_password = env.get("DEMO_USER_PASSWORD", "")
        user_passwords = {user["username"]: legacy_password for user in users_doc["users"]}

    kc_users = []
    for user in users_doc["users"]:
        first_name, last_name = split_display_name(user["display_name"])
        password = user_passwords.get(user["username"])
        if not isinstance(password, str) or not password:
            raise ValueError(f"No password is available for seeded Keycloak user {user['username']}.")
        kc_users.append(
            {
                "id": deterministic_id(user["username"]),
                "username": user["username"],
                "enabled": True,
                "email": f"{user['username']}@foldfence-demo.invalid",
                "firstName": first_name,
                "lastName": last_name,
                "attributes": {"security_clearance": [user["clearance"]]},
                "clientRoles": {client_id: user["room_roles"]},
                # The static demo identities (including isolated export fixtures)
                # are intentionally entitled. The separate user-management path
                # does not use this seed default for newly created accounts.
                "realmRoles": list(dict.fromkeys([*user.get("realm_roles", []), matrix_access["role"]["name"]])),
                "groups": user.get("groups", []),
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            }
        )

    # Normal preparation passes stable per-target credentials from the private
    # manifest. Legacy isolated callers may omit admins in their test fixture.
    if demo_admin_passwords is not None:
        if set(demo_admin_passwords) != {username for username, _ in KEYCLOAK_DEMO_ADMIN_USERS}:
            raise ValueError("Both Keycloak demo admin passwords are required for the realm import.")
        demo_admin_values = [demo_admin_passwords[username] for username, _ in KEYCLOAK_DEMO_ADMIN_USERS]
        if (
            any(not isinstance(password, str) or not password for password in demo_admin_values)
            or len(set(demo_admin_values)) != len(demo_admin_values)
            or set(demo_admin_values) & (set(user_passwords.values()) | {env.get("KEYCLOAK_BOOTSTRAP_PASSWORD", "")})
        ):
            raise ValueError("Keycloak demo admin passwords must be unique from root and seeded user passwords.")
        seeded_names = {user["username"] for user in users_doc["users"]}
        if seeded_names & {username for username, _ in KEYCLOAK_DEMO_ADMIN_USERS}:
            raise ValueError("Keycloak demo admin usernames cannot overlap seeded Matrix users.")
        for username, display_name in KEYCLOAK_DEMO_ADMIN_USERS:
            password = demo_admin_passwords[username]
            if not isinstance(password, str) or not password:
                raise ValueError(f"No private password is available for Keycloak demo admin {username}.")
            first_name, last_name = split_display_name(display_name)
            kc_users.append(
                {
                    "id": deterministic_id(username),
                    "username": username,
                    "enabled": True,
                    "email": f"{username}@foldfence-demo.invalid",
                    "firstName": first_name,
                    "lastName": last_name,
                    "credentials": [{"type": "password", "value": password, "temporary": False}],
                    "clientRoles": {"realm-management": list(KEYCLOAK_DEMO_ADMIN_ROLES)},
                }
            )

    kc_users.append(
        {
            "username": f"service-account-{sync_client_id}",
            "enabled": True,
            "serviceAccountClientId": sync_client_id,
            "clientRoles": {"realm-management": ["query-users", "view-users", "view-clients"]},
        }
    )

    realm = {
        "realm": realm_name,
        "displayName": brand.get("organization_name", "Organization Demo"),
        "enabled": True,
        "groups": [
            {"id": deterministic_id(f"group:{group['name']}"), "name": group["name"]}
            for group in users_doc.get("keycloak_groups", [])
        ],
        "sslRequired": "external",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "rememberMe": False,
        "verifyEmail": False,
        "loginTheme": brand.get("keycloak", {}).get("theme_name", "fold-fence-demo"),
        "accountTheme": brand.get("keycloak", {}).get("theme_name", "fold-fence-demo"),
        "roles": {
            "realm": [
                {"name": "org-export-admin", "description": "May request scoped organization room exports"},
                matrix_access["role"],
            ],
            "client": {client_id: client_roles},
        },
        "clients": [
            {
                "id": client_uuid,
                "clientId": client_id,
                "name": "Matrix policy role container",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "standardFlowEnabled": False,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": [],
                "webOrigins": [],
                "protocolMappers": protocol_mappers,
            },
            {
                "id": deterministic_id(mas_client_id),
                "clientId": mas_client_id,
                "name": "Matrix Authentication Service upstream OIDC",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "standardFlowEnabled": True,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "secret": env["MAS_KEYCLOAK_CLIENT_SECRET"],
                "defaultClientScopes": oidc_scopes["default_client_scopes"],
                "redirectUris": [
                    f"{env['MATRIX_AUTH_PUBLIC_URL']}/upstream/callback/{env['MAS_UPSTREAM_PROVIDER_ID']}"
                ],
                "webOrigins": [],
                "attributes": {"backchannel.logout.url": f"{env['MATRIX_AUTH_PUBLIC_URL']}/upstream/backchannel-logout/{env['MAS_UPSTREAM_PROVIDER_ID']}", "backchannel.logout.session.required": "true"},
            },
            {
                "id": sync_uuid,
                "clientId": sync_client_id,
                "name": "Matrix room policy synchronizer",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "standardFlowEnabled": False,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": True,
                "secret": env["KEYCLOAK_POLICY_CLIENT_SECRET"],
            },
            {
                "id": deterministic_id("matrix-export-client"),
                "clientId": "matrix-export-client",
                "name": "Matrix demo export client",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": True,
                "standardFlowEnabled": True,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "fullScopeAllowed": False,
                "protocolMappers": export_scopes["protocol_mappers"],
                "redirectUris": ["https://oauth.pstmn.io/v1/callback", f"{env['MATRIX_PUBLIC_URL']}/export/"],
                "webOrigins": [env["MATRIX_PUBLIC_URL"]],
                "attributes": {"pkce.code.challenge.method": "S256"},
            },
        ],
        "users": kc_users,
        "scopeMappings": [{"client": export_scopes["client_id"], "roles": export_scopes["realm_roles"]}],
        "clientScopes": [
            {**scope, "id": deterministic_id(f"client-scope:{scope['name']}")}
            for scope in oidc_scopes["client_scopes"]
        ],
    }

    destination = private / "keycloak" / "import" / f"{realm_name}-realm.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o750)
    destination.write_text(json.dumps(realm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    destination.chmod(0o640)


def yaml_string(value: str) -> str:
    """Emit a quoted YAML scalar using JSON's compatible string escaping."""
    return json.dumps(value, ensure_ascii=False)


def share_config_tree(directory: Path) -> None:
    """Give services in the generated-config group read access to a mount."""
    directory.chmod(0o750)
    for path in directory.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o750 if path.is_dir() else 0o640)


def ensure_mas_generated_config(private: Path, *, image: str) -> Path:
    """Ask the selected local MAS CLI to create its private signing/encryption keys."""
    destination = private / "mas" / "config-generated.yaml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        destination.chmod(0o640)
        return destination
    docker_command = shlex.split(os.environ.get("DOCKER_COMMAND", "docker"))
    if not docker_command or shutil.which(docker_command[0]) is None:
        raise SystemExit("Docker CLI is required once to generate Matrix Authentication Service signing keys.")
    try:
        with destination.open("wb") as output:
            subprocess.run(
                [*docker_command, "run", "--rm", "--pull=never", image, "config", "generate"],
                check=True,
                stdout=output,
                stderr=subprocess.DEVNULL,
            )
        if destination.stat().st_size < 100:
            raise OSError("generated configuration was unexpectedly small")
    except (OSError, subprocess.CalledProcessError):
        destination.unlink(missing_ok=True)
        raise SystemExit("Could not generate the private Matrix Authentication Service key configuration.") from None
    destination.chmod(0o640)
    return destination


def render_mas(private: Path, env: dict[str, str], brand: dict) -> None:
    # MAS loads templates from one directory rather than overlaying an override
    # directory onto its built-ins. Copy the templates from the exact pinned
    # image, then replace only the browser login page with our SSO-only view.
    mas_dir = private / "mas"
    mas_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    mas_dir.chmod(0o750)
    templates_dir = mas_dir / "templates"
    docker_command = shlex.split(os.environ.get("DOCKER_COMMAND", "docker"))
    if not docker_command or shutil.which(docker_command[0]) is None:
        raise SystemExit("Docker CLI is required to prepare the pinned MAS login template.")
    try:
        spec = importlib.util.spec_from_file_location("prepare_compose_images", Path(__file__).with_name("demo_compose.py"))
        compose_images = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compose_images)
        if compose_images.image_mode(ROOT, ENV_FILE) == "build":
            files = compose_images.compose_file_arguments(ROOT, ENV_FILE)
            subprocess.run(
                [*docker_command, "compose", "--env-file", str(ENV_FILE), *files, "build", "mas"],
                cwd=ROOT,
                check=True,
            )
        template_image = compose_images.resolved_service_image(ROOT, ENV_FILE, "mas", docker_command)
        ensure_mas_generated_config(private, image=template_image)
        created = subprocess.run(
            [*docker_command, "create", "--pull=never", template_image],
            check=True,
            capture_output=True,
            text=True,
        )
        container_id = created.stdout.strip()
        if not container_id:
            raise OSError("Docker did not return a container ID")
        with tempfile.TemporaryDirectory(prefix=".mas-templates-", dir=mas_dir) as temporary:
            staged_templates = Path(temporary)
            try:
                template_archive = subprocess.run(
                    [
                        *docker_command,
                        "cp",
                        f"{container_id}:/usr/local/share/mas-cli/templates/.",
                        "-",
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                ).stdout
            finally:
                subprocess.run(
                    [*docker_command, "rm", "--force", container_id],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            # The Docker CLI can run under sudo. Extract as the invoking user
            # so the staged files remain writable and removable during setup.
            with tarfile.open(fileobj=io.BytesIO(template_archive), mode="r:*") as archive:
                archive.extractall(staged_templates, filter="data")
            login_template = staged_templates / "pages" / "login.html"
            custom_login_template = ROOT / "config" / "mas" / "templates" / "pages" / "login.html"
            if not login_template.is_file() or not custom_login_template.is_file():
                raise OSError("The pinned MAS image or project login template is missing")
            shutil.copyfile(custom_login_template, login_template)
            if templates_dir.exists():
                shutil.rmtree(templates_dir)
            shutil.copytree(staged_templates, templates_dir)
            share_config_tree(templates_dir)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        if templates_dir.exists() and not (templates_dir / "pages" / "login.html").is_file():
            shutil.rmtree(templates_dir, ignore_errors=True)
        raise SystemExit("Could not prepare the browser-only MAS SSO template from the pinned image.") from error
    lines = [
        "http:",
        f"  public_base: {yaml_string(env['MATRIX_AUTH_PUBLIC_URL'] + '/')}",
        f"  issuer: {yaml_string(env['MATRIX_AUTH_PUBLIC_URL'] + '/')}",
        "templates:",
        '  path: "/config/templates"',
        "database:",
        f"  uri: {yaml_string('postgresql://mas:' + env['MAS_DB_PASSWORD'] + '@mas-db:5432/mas')}",
        "matrix:",
        "  kind: synapse",
        f"  homeserver: {yaml_string(env['MATRIX_SERVER_NAME'])}",
        '  endpoint: "http://synapse:8008"',
        f"  secret: {yaml_string(env['MAS_SYNAPSE_SHARED_SECRET'])}",
        "account:",
        "  password_registration_enabled: false",
        "  password_registration_email_required: false",
        "  displayname_change_allowed: false",
        "passwords:",
        "  enabled: false",
        "upstream_oauth2:",
        "  providers:",
        f"    - id: {env['MAS_UPSTREAM_PROVIDER_ID']}",
        f"      issuer: {yaml_string(env['KEYCLOAK_ISSUER_URL'])}",
        '      token_endpoint_auth_method: "client_secret_basic"',
        '      client_id: "matrix-authentication-service"',
        f"      client_secret: {yaml_string(env['MAS_KEYCLOAK_CLIENT_SECRET'])}",
        '      scope: "openid profile"',
        f"      human_name: {yaml_string(brand.get('organization_name', 'Organization Identity'))}",
        "      registration_token_required: true",
        "      on_backchannel_logout: logout_all",
        "      claims_imports:",
        "        localpart:",
        "          action: require",
        '          template: "{{ user.preferred_username }}"',
        "        displayname:",
        "          action: suggest",
        '          template: "{{ user.name }}"',
        "branding:",
        f"  service_name: {yaml_string(brand.get('product_name', 'Organization Messenger'))}",
        "",
    ]
    destination = private / "mas" / "config.yaml"
    destination.write_text("\n".join(lines), encoding="utf-8")
    destination.chmod(0o640)


def render_element(private: Path, env: dict[str, str], brand: dict) -> None:
    element = read_json(ROOT / "config/element/config.json")
    element["default_server_config"]["m.homeserver"]["base_url"] = env["MATRIX_PUBLIC_URL"]
    element["default_server_config"]["m.homeserver"]["server_name"] = env["MATRIX_SERVER_NAME"]
    element["brand"] = brand.get("product_name", "Organization Messenger")
    element["element_call"]["brand"] = f"{brand.get('product_name', 'Organization Messenger')} Call"
    element_logo_key = brand.get("element_web", {}).get("auth_header_logo", "logo_wide")
    if element_logo_key not in {"logo_wide", "logo_mark"}:
        raise SystemExit("element_web.auth_header_logo must be 'logo_wide' or 'logo_mark'.")
    element_logo_name = "logo-wide" if element_logo_key == "logo_wide" else "logo-mark"
    element_logo_suffix = asset_suffix(brand.get(element_logo_key))
    element.setdefault("branding", {})["auth_header_logo_url"] = f"/brand/{element_logo_name}{element_logo_suffix}"
    element["branding"]["logo_link_url"] = "/"
    out = private / "element"
    out.mkdir(parents=True, exist_ok=True)
    # These files are public browser assets served by the nginx image user.
    config_path = out / "config.json"
    config_path.write_text(json.dumps(element, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    config_path.chmod(0o644)
    assets = out / "brand"
    assets.mkdir(exist_ok=True, mode=0o755)
    assets.chmod(0o755)
    wide_suffix = asset_suffix(brand.get("logo_wide"))
    copy_brand_asset(brand.get("logo_wide"), assets / f"logo-wide{wide_suffix}")
    mark_suffix = asset_suffix(brand.get("logo_mark"))
    copy_brand_asset(brand.get("logo_mark"), assets / f"logo-mark{mark_suffix}")


def render_keycloak_theme(private: Path, brand: dict) -> None:
    theme_name = brand.get("keycloak", {}).get("theme_name", "fold-fence-demo")
    theme_source = ROOT / "config/keycloak/themes/fold-fence-demo"
    theme_out = private / "keycloak" / "themes" / theme_name
    if theme_out.exists():
        shutil.rmtree(theme_out)
    shutil.copytree(theme_source, theme_out)
    colors = brand.get("colors", {})
    css = (
        ":root {\n"
        f"  --pf-v5-global--primary-color--100: {colors.get('primary', '#146B8C')};\n"
        f"  --pf-v5-global--primary-color--200: {colors.get('accent', '#B4D334')};\n"
        "}\n"
        f".login-pf, .pf-v5-c-login {{ background-color: {colors.get('background', '#0B2034')}; }}\n"
        f".kc-logo-text {{ background-image: url('../img/brand-logo{asset_suffix(brand.get('logo_wide'))}'); background-size: contain; width: 20rem; height: 6rem; }}\n"
        "#kc-header-wrapper { text-align: center; }\n"
        "#kc-header-wrapper::before { content: ''; display: block; width: 4.5rem; height: 4.5rem; margin: 0 auto 1rem; "
        f"background: url('../img/brand-mark{asset_suffix(brand.get('logo_mark'))}') center / contain no-repeat; }}\n"
    )
    for theme_part in ("login", "account"):
        css_dir = theme_out / theme_part / "resources" / "css"
        img_dir = theme_out / theme_part / "resources" / "img"
        css_dir.mkdir(parents=True, exist_ok=True)
        img_dir.mkdir(parents=True, exist_ok=True)
        (css_dir / "brand.css").write_text(css, encoding="utf-8")
        copy_brand_asset(brand.get("logo_wide"), img_dir / f"brand-logo{asset_suffix(brand.get('logo_wide'))}")
        copy_brand_asset(brand.get("logo_mark"), img_dir / f"brand-mark{asset_suffix(brand.get('logo_mark'))}")
        if brand.get("login_background"):
            background_suffix = asset_suffix(brand.get("login_background"), ".jpg")
            background_file = img_dir / f"brand-background{background_suffix}"
            copy_brand_asset(brand.get("login_background"), background_file)
            css_path = css_dir / "brand.css"
            css_path.write_text(
                css + f".login-pf body {{ background-image: url('../img/brand-background{background_suffix}'); background-size: cover; }}\n",
                encoding="utf-8",
            )
    theme_out.parent.chmod(0o750)
    share_config_tree(theme_out)


def copy_brand_asset(asset: str | None, target: Path) -> None:
    if not asset:
        return
    candidates = [ROOT / "internal" / "branding" / asset, ROOT / "config" / "branding" / asset]
    source = next((candidate for candidate in candidates if candidate.is_file()), None)
    if source is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)


def nginx_admin_networks(value: str, setting: str = "ADMIN_ALLOWED_CIDRS") -> list[str]:
    """Only literal management CIDRs may enter the generated Nginx ACL."""
    if not isinstance(value, str) or not value.strip() or value.strip() == "CHANGE_ME":
        raise ValueError(f"{setting} must name the approved source networks")
    networks = []
    for item in value.split(","):
        try:
            network = ipaddress.ip_network(item.strip(), strict=True)
        except ValueError:
            raise ValueError(f"{setting} contains an invalid or non-canonical CIDR") from None
        if network.prefixlen == 0 or network.is_multicast or network.network_address.is_unspecified:
            raise ValueError(f"{setting} must not grant all Internet addresses")
        if str(network) not in networks:
            networks.append(str(network))
    return networks


def matrix_public_authority(public_url: str) -> str:
    """Delegate OpenID discovery to the public HTTPS endpoint, not its identity."""
    try:
        if not isinstance(public_url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in public_url):
            raise ValueError
        parsed = urlsplit(public_url)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.netloc.endswith(":")):
            raise ValueError
        port = 443 if port is None else port
        if not 1 <= port <= 65535:
            raise ValueError
        if ":" in host:
            if "%" in host:
                raise ValueError
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        else:
            host = host.encode("idna").decode("ascii")
            labels = host.rstrip(".").split(".")
            if (len(host.rstrip(".")) > 253 or any(
                    not label or len(label) > 63 or not label[0].isalnum() or not label[-1].isalnum()
                    or any(not (char.isalnum() or char == "-") for char in label)
                    for label in labels)):
                raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("MATRIX_PUBLIC_URL must be an HTTPS origin with a valid public host and port") from None
    return f"{host}:{port}"


def render_nginx(private: Path, env: dict[str, str]) -> None:
    template = (ROOT / "deployment/nginx/default.conf.template").read_text(encoding="utf-8")
    https_port = int(env["DEMO_HTTPS_PORT"])
    # The canonical hostname resolves directly to Nginx inside Compose. Keep
    # that TLS route reachable on the same port advertised to outside clients.
    canonical_tls_listener = f"\n    listen {https_port} ssl;" if https_port != 443 else ""
    trusted_proxy_rules = ""
    trusted_proxy_value = env.get("TRUSTED_PROXY_CIDRS", "").strip()
    if trusted_proxy_value:
        trusted_networks = nginx_admin_networks(trusted_proxy_value, "TRUSTED_PROXY_CIDRS")
        trusted_proxy_rules = "\n".join(f"    set_real_ip_from {network};" for network in trusted_networks)
        trusted_proxy_rules += "\n    real_ip_header X-Forwarded-For;\n    real_ip_recursive on;"
    auth_blocks = ""
    if env["MATRIX_AUTH_HOSTNAME"].lower() != env["DEMO_HOSTNAME"].lower():
        auth_blocks = f"""
server {{
    listen 80;
    server_name {env['MATRIX_AUTH_HOSTNAME']};
    access_log /var/log/nginx/access.log demo_safe;
    return 308 https://$host:{env['DEMO_HTTPS_PORT']}$request_uri;
}}

server {{
    listen 443 ssl;{canonical_tls_listener}
    http2 on;
    server_name {env['MATRIX_AUTH_HOSTNAME']};
    access_log /var/log/nginx/access.log demo_safe;
{trusted_proxy_rules}
    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 10m;

    location ^~ /mas/ {{
        proxy_pass http://mas_backend/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 300s;
    }}

    location / {{ return 308 /mas/; }}
}}
""".strip()
    admin_networks = nginx_admin_networks(env.get(
        "ADMIN_ALLOWED_CIDRS", "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    ))
    admin_rules = "\n".join(f"        allow {network};" for network in admin_networks) + "\n        deny all;"
    substitutions = {
        "%%TRUSTED_PROXY_RULES%%": trusted_proxy_rules,
        "%%ADMIN_ACCESS_RULES%%": admin_rules,
        "%%DEMO_HOSTNAME%%": env["DEMO_HOSTNAME"],
        "%%MATRIX_AUTH_HOSTNAME%%": env["MATRIX_AUTH_HOSTNAME"],
        "%%AUTH_HOST_BLOCKS%%": auth_blocks,
        "%%CANONICAL_TLS_LISTENER%%": canonical_tls_listener,
        "%%DEMO_HTTPS_PORT%%": env["DEMO_HTTPS_PORT"],
        "%%MATRIX_SERVER_NAME%%": env["MATRIX_SERVER_NAME"],
        "%%MATRIX_PUBLIC_AUTHORITY%%": matrix_public_authority(env["MATRIX_PUBLIC_URL"]),
        "%%MATRIX_PUBLIC_URL%%": env["MATRIX_PUBLIC_URL"],
        "%%KEYCLOAK_PUBLIC_URL%%": env["KEYCLOAK_PUBLIC_URL"],
        "%%LIVEKIT_JWT_PUBLIC_URL%%": env["LIVEKIT_JWT_PUBLIC_URL"],
    }
    for key, value in substitutions.items():
        template = template.replace(key, value)
    out = private / "nginx"
    out.mkdir(parents=True, exist_ok=True)
    (out / "default.conf").write_text(template, encoding="utf-8")


def render_livekit(private: Path, env: dict[str, str]) -> None:
    tcp_port = int(env.get("LIVEKIT_TCP_PORT", "7881"))
    udp_port = int(env.get("LIVEKIT_UDP_PORT", "50100"))
    if not 1 <= tcp_port <= 65535 or not 1 <= udp_port <= 65535:
        raise ValueError("LiveKit TCP/UDP ports must be valid port numbers")
    content = f"""port: 7880
rtc:
  tcp_port: {tcp_port}
  udp_port: {udp_port}
  use_external_ip: true
keys:
  {json.dumps(env['LIVEKIT_API_KEY'])}: {json.dumps(env['LIVEKIT_API_SECRET'])}
room:
  auto_create: false
logging:
  level: info
"""
    path = private / "livekit" / "livekit.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o640)

    registration = [
        'id: "livekit-jwt-service"',
        f"as_token: {yaml_string(env['LIVEKIT_AS_TOKEN'])}",
        f"hs_token: {yaml_string(env['LIVEKIT_HS_TOKEN'])}",
        'sender_localpart: "_livekit_jwt_service"',
        "namespaces:",
        "  users:",
        "    - exclusive: false",
        '      regex: ".*"',
        "url: null",
        "io.element.msc4502.scopes:",
        '  - "urn:matrix:client:io.element.msc4502:rooms:is_joined"',
        'io.element.msc4512.proxy_prefix: "rtc/livekit"',
        'io.element.msc4512.proxy_url: "http://lk-jwt-service:8080/"',
        "",
    ]
    registration_path = private / "livekit" / "livekit-registration.yaml"
    registration_path.write_text("\n".join(registration), encoding="utf-8")
    registration_path.chmod(0o640)


def make_tls_files(private: Path, env: dict[str, str]) -> None:
    cert_dir = private / "tls"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert = cert_dir / "fullchain.pem"
    key = cert_dir / "privkey.pem"
    ca_cert = cert_dir / "dev-root-ca.pem"
    ca_key = cert_dir / "dev-root-ca.key"
    server_cert = cert_dir / "server.pem"
    tls_mode = env.get("TLS_MODE", "development-self-signed").strip()
    if tls_mode not in {"development-self-signed", "provided"}:
        raise SystemExit("TLS_MODE must be development-self-signed or provided.")

    if tls_mode == "provided":
        cert_source = tls_source(env.get("TLS_CERT_FILE", ""), "TLS_CERT_FILE")
        key_source = tls_source(env.get("TLS_KEY_FILE", ""), "TLS_KEY_FILE")
        verify_tls_pair(cert_source, key_source, env["DEMO_HOSTNAME"], env["MATRIX_AUTH_HOSTNAME"])
        shutil.copyfile(cert_source, cert)
        shutil.copyfile(key_source, key)
        cert.chmod(0o600)
        key.chmod(0o600)
        write_mas_ca_bundle(private / "mas" / "mas-ca-bundle.pem")
        return

    if ca_cert.exists() and ca_key.exists() and server_cert.exists() and key.exists():
        try:
            verify_tls_pair(server_cert, key, env["DEMO_HOSTNAME"], env["MATRIX_AUTH_HOSTNAME"])
            subprocess.run(
                ["openssl", "verify", "-CAfile", str(ca_cert), str(server_cert)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cert.write_bytes(server_cert.read_bytes())
            cert.chmod(0o644)
            ca_cert.chmod(0o644)
            ca_key.chmod(0o600)
            key.chmod(0o600)
            write_mas_ca_bundle(private / "mas" / "mas-ca-bundle.pem", ca_cert)
            return
        except SystemExit:
            for path in (ca_cert, ca_key, server_cert, key, cert):
                path.unlink(missing_ok=True)
        except (OSError, subprocess.CalledProcessError):
            for path in (ca_cert, ca_key, server_cert, key, cert):
                path.unlink(missing_ok=True)

    reuse_existing_ca = ca_cert.exists() and ca_key.exists()
    if not reuse_existing_ca and cert.exists() and key.exists():
        # Upgrade the already trusted development certificate in place when it
        # was generated as a CA certificate. Reusing its public root preserves
        # the trust anchor on devices while replacing the invalid server leaf.
        try:
            verify_tls_pair(cert, key, env["DEMO_HOSTNAME"], env["MATRIX_AUTH_HOSTNAME"])
            certificate_text = subprocess.run(
                ["openssl", "x509", "-in", str(cert), "-noout", "-text"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
            if b"CA:TRUE" in certificate_text:
                shutil.copyfile(cert, ca_cert)
                shutil.copyfile(key, ca_key)
                reuse_existing_ca = True
        except (SystemExit, OSError, subprocess.CalledProcessError):
            pass

    hostname = env["DEMO_HOSTNAME"]
    auth_hostname = env["MATRIX_AUTH_HOSTNAME"]
    ca_config = cert_dir / "ca.cnf"
    ca_config.write_text(
        "[req]\n"
        "distinguished_name = dn\n"
        "prompt = no\n"
        "x509_extensions = v3_ca\n"
        "[dn]\n"
        "CN = Foldfence Internal Demo Root CA\n"
        "[v3_ca]\n"
        "basicConstraints = critical,CA:TRUE,pathlen:0\n"
        "keyUsage = critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier = hash\n"
        "authorityKeyIdentifier = keyid:always,issuer\n",
        encoding="ascii",
    )
    server_extensions = [
        "basicConstraints = critical,CA:FALSE",
        "keyUsage = critical,digitalSignature,keyEncipherment",
        "extendedKeyUsage = serverAuth",
        "subjectKeyIdentifier = hash",
        "authorityKeyIdentifier = keyid,issuer",
        "subjectAltName = @alt_names",
        "[alt_names]",
    ]
    names = (hostname, auth_hostname)
    dns_index = 0
    ip_index = 0
    for name in dict.fromkeys(names):
        try:
            parsed_ip = ipaddress.ip_address(name)
        except ValueError:
            dns_index += 1
            server_extensions.append(f"DNS.{dns_index} = {name}")
        else:
            ip_index += 1
            family = "IP" if parsed_ip.version == 4 else "IP"
            server_extensions.append(f"{family}.{ip_index} = {name}")
    extension_file = cert_dir / "server.ext"
    extension_file.write_text("\n".join(server_extensions) + "\n", encoding="ascii")

    if not reuse_existing_ca:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes", "-days", "3650", "-newkey", "rsa:3072",
                "-keyout", str(ca_key), "-out", str(ca_cert), "-subj", "/CN=Foldfence Internal Demo Root CA",
                "-config", str(ca_config), "-extensions", "v3_ca",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    csr = cert_dir / "server.csr"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(key), "3072"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["openssl", "req", "-new", "-key", str(key), "-out", str(csr), "-subj", f"/CN={hostname}"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
            "-CAcreateserial", "-out", str(server_cert), "-days", "825", "-sha256", "-extfile", str(extension_file),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["openssl", "verify", "-CAfile", str(ca_cert), str(server_cert)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    verify_tls_pair(server_cert, key, hostname, auth_hostname)
    cert.write_bytes(server_cert.read_bytes())
    cert.chmod(0o600)
    server_cert.chmod(0o600)
    ca_cert.chmod(0o644)
    ca_key.chmod(0o600)
    key.chmod(0o600)
    csr.unlink(missing_ok=True)
    (cert_dir / "dev-root-ca.srl").unlink(missing_ok=True)
    ca_config.unlink(missing_ok=True)
    extension_file.unlink(missing_ok=True)
    write_mas_ca_bundle(private / "mas" / "mas-ca-bundle.pem", ca_cert)


def write_mas_ca_bundle(destination: Path, additional_ca: Path | None = None) -> None:
    """Keep the platform roots and append the internal demo root when present."""
    verify_paths = ssl.get_default_verify_paths()
    bundle_candidates = [
        Path(verify_paths.cafile) if verify_paths.cafile else None,
        Path("/etc/ssl/certs/ca-certificates.crt"),
        Path("/etc/ssl/cert.pem"),
        Path("/etc/pki/tls/certs/ca-bundle.crt"),
    ]
    system_bundle = next((path for path in bundle_candidates if path and path.is_file()), None)
    if system_bundle is None:
        raise SystemExit("A system CA bundle is required to configure MAS TLS verification.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    contents = system_bundle.read_bytes()
    if additional_ca is not None:
        contents += b"\n" + additional_ca.read_bytes()
    destination.write_bytes(contents)
    destination.chmod(0o644)


def tls_source(value: str, name: str) -> Path:
    if not value or value == "CHANGE_ME":
        raise SystemExit(f"{name} must point to a private TLS file when TLS_MODE=provided.")
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"Configured {name} file is missing.")
    return path


def certificate_matches_hostname(decoded_certificate: dict, hostname: str) -> bool:
    """Match DNS names and IP SANs without relying on removed ssl.match_hostname."""
    try:
        requested_ip = ipaddress.ip_address(hostname)
    except ValueError:
        requested_ip = None

    subject_alt_names = decoded_certificate.get("subjectAltName", ())
    if requested_ip is not None:
        for kind, value in subject_alt_names:
            if kind == "IP Address":
                try:
                    if ipaddress.ip_address(value) == requested_ip:
                        return True
                except ValueError:
                    continue
        return False

    try:
        requested_dns = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False

    dns_names = [value for kind, value in subject_alt_names if kind == "DNS"]
    if not dns_names:
        dns_names = [
            value
            for relative_distinguished_name in decoded_certificate.get("subject", ())
            for attribute, value in relative_distinguished_name
            if attribute == "commonName"
        ]

    for name in dns_names:
        try:
            certificate_dns = name.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            continue
        if certificate_dns == requested_dns:
            return True
        if certificate_dns.startswith("*.") and certificate_dns.count("*") == 1:
            suffix = certificate_dns[2:]
            if requested_dns.endswith("." + suffix):
                leftmost = requested_dns[: -(len(suffix) + 1)]
                if leftmost and "." not in leftmost:
                    return True
    return False


def verify_tls_pair(cert: Path, key: Path, *hostnames: str) -> None:
    if shutil.which("openssl") is None:
        raise SystemExit("openssl is required to validate the configured TLS certificate.")
    try:
        subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "0"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        decoded_certificate = ssl._ssl._test_decode_cert(str(cert))
        for hostname in hostnames:
            if not certificate_matches_hostname(decoded_certificate, hostname):
                raise ValueError(f"certificate does not cover {hostname}")
        cert_public = subprocess.run(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        cert_der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=cert_public, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        key_der = subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        if cert_der != key_der:
            raise ValueError("certificate and private key do not match")
    except (OSError, subprocess.CalledProcessError, ValueError, ssl.SSLError):
        raise SystemExit("TLS certificate must be valid for every configured public hostname and match the configured private key.") from None


def main() -> None:
    if shutil.which("openssl") is None:
        raise SystemExit("openssl is required to create the local TLS certificate")
    env = read_env()
    private = private_generated_dir(env)
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    brand = brand_values(brand_config(env))
    if env.get("ORG_BOT_DISPLAY_NAME", "").strip() in {
        "", "Fold Fence Export Bot", "Organization Demo Bot", "Organization Demo Export Bot",
    }:
        env["ORG_BOT_DISPLAY_NAME"] = f"{brand.get('organization_name', 'Organization')} OrgBot"
    write_env(env)
    users = read_json(ROOT / "config/seed/demo-users.json")
    policy = read_json(ROOT / "config/policies/room-access.json")
    user_passwords = ensure_user_passwords(private, env, users)
    demo_admin_passwords = ensure_demo_admin_passwords(
        private,
        env,
        [*user_passwords.values(), env["KEYCLOAK_BOOTSTRAP_PASSWORD"]],
    )
    manifest = safe_credential_data(
        env,
        brand,
        users,
        user_passwords,
        target=env["DEMO_TARGET"],
        password_mode=env["DEMO_USER_PASSWORD_MODE"],
        demo_admin_passwords=demo_admin_passwords,
    )
    manifest_path = credential_manifest_path(private)
    private_json_write(manifest_path, manifest)
    render_keycloak(private, env, brand, users, policy, user_passwords, demo_admin_passwords)
    render_element(private, env, brand)
    render_keycloak_theme(private, brand)
    render_nginx(private, env)
    render_livekit(private, env)
    render_mas(private, env, brand)
    make_tls_files(private, env)
    write_private_credentials(private, manifest)
    print(f"Prepared private realm import, Element config, Keycloak theme, Nginx config, LiveKit config and TLS files in {private.relative_to(ROOT)}.")
    print(f"Review {private.parent.relative_to(ROOT) / 'demo-credentials.txt'} and {manifest_path.relative_to(ROOT)}; trust the generated certificate on demo devices before using microphone/camera.")


if __name__ == "__main__":
    main()
