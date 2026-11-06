#!/usr/bin/env python3
"""Reconcile MAS OIDC scopes and the managed demo-user clearance attribute."""

from __future__ import annotations

import argparse
import importlib.util
import json
import ipaddress
import os
import shlex
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# Load the sibling by file path: regression tools also import this script via
# spec_from_file_location without putting scripts/ on sys.path.
_COMPOSE_SPEC = importlib.util.spec_from_file_location(
    "demo_compose", Path(__file__).with_name("demo_compose.py")
)
if _COMPOSE_SPEC is None or _COMPOSE_SPEC.loader is None:
    raise ImportError("Could not load the Compose configuration helper")
_COMPOSE_MODULE = importlib.util.module_from_spec(_COMPOSE_SPEC)
_COMPOSE_SPEC.loader.exec_module(_COMPOSE_MODULE)
compose_file_arguments = _COMPOSE_MODULE.compose_file_arguments


ROOT = Path(__file__).resolve().parents[1]


def read_env() -> dict[str, str]:
    env_file = Path(os.environ.get("DEMO_ENV_FILE", ".env"))
    if not env_file.is_absolute():
        env_file = ROOT / env_file
    result: dict[str, str] = {}
    for path in (ROOT / ".env.example", env_file):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key] = value
    return result


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def internal_keycloak_url(env: dict[str, str]) -> str:
    """Find only this Compose project's private Keycloak backend for bootstrap.

    The public proxy depends on policy readiness, which itself requires this
    profile setup. Use the local Docker bridge before the public route exists.
    Public issuer URLs and TLS validation are left unchanged.
    """
    project = env.get("COMPOSE_PROJECT_NAME", "matrix-keycloak-demo")
    docker = shlex.split(os.environ.get("DOCKER_COMMAND", "docker"))
    env_file = Path(os.environ.get("DEMO_ENV_FILE", ".env"))
    command = [*docker, "compose", "--project-name", project, "--env-file", str(env_file),
               *compose_file_arguments(ROOT, env_file), "ps", "-q", "keycloak"]
    try:
        identifier = subprocess.check_output(command, cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
        if not identifier or any(char not in "0123456789abcdef" for char in identifier):
            raise ValueError("The target Keycloak container is not running")
        containers = json.loads(subprocess.check_output(
            [*docker, "inspect", identifier], stderr=subprocess.DEVNULL, text=True))
        container = containers[0]
        labels = container["Config"].get("Labels", {})
        if (labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != "keycloak"):
            raise ValueError("The Keycloak container does not belong to the selected target")
        networks = container["NetworkSettings"]["Networks"]
        # Multi-homed containers have several addresses. Select the application
        # network explicitly; never concatenate or accept an arbitrary bridge.
        if project + "_application" in networks:
            selected = networks[project + "_application"]
            subnet = ipaddress.ip_network(env.get("COMPOSE_APPLICATION_SUBNET", "10.234.1.0/24"))
        else:
            # Existing installations can bootstrap the reviewed update before
            # their coordinated network migration. A present invalid new
            # address never falls back to this legacy network.
            selected = networks[project + "_default"]
            subnet = ipaddress.ip_network(env.get("COMPOSE_NETWORK_SUBNET", "10.231.0.0/24"))
        address = ipaddress.ip_address(selected["IPAddress"])
        if not address.is_private or address not in subnet or address.version != 4:
            raise ValueError("The target Keycloak address is outside the private project network")
    except (OSError, subprocess.CalledProcessError, IndexError, KeyError, json.JSONDecodeError):
        raise ValueError("Could not discover the private target Keycloak container") from None
    return f"http://{address}:8080/auth"


def tls_context(env: dict[str, str]) -> ssl.SSLContext:
    if env.get("TLS_MODE") != "development-self-signed":
        return ssl.create_default_context()
    generated = Path(env.get("DEMO_GENERATED_DIR", "../../internal/generated"))
    if not generated.is_absolute():
        generated = ROOT / "deployment" / "compose" / generated
    generated = generated.resolve()
    try:
        generated.relative_to((ROOT / "internal").resolve())
    except ValueError:
        raise ValueError("DEMO_GENERATED_DIR must remain inside internal/ for development TLS") from None
    ca = generated / "tls" / "dev-root-ca.pem"
    if not ca.is_file():
        ca = ROOT / "internal" / "deploy" / "internal-homeserver" / "tls" / "dev-root-ca.pem"
    if not ca.is_file():
        raise ValueError("The public development root CA is missing")
    return ssl.create_default_context(cafile=str(ca))


class KeycloakAdmin:
    def __init__(self, base_url: str, context: ssl.SSLContext, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.context = context
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        status, payload, _headers = self.request_with_headers(method, path, body)
        return status, payload

    def request_with_headers(
        self, method: str, path: str, body: dict | None = None, *, allow_http_errors: set[int] | None = None
    ) -> tuple[int, object, dict[str, str]]:
        headers = {"Accept": "application/json"}
        data = None
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=20) as response:
                payload = response.read()
                return response.status, json.loads(payload) if payload else None, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            if allow_http_errors and exc.code in allow_http_errors:
                return exc.code, None, dict(exc.headers.items())
            # Do not echo response bodies; they may contain provider diagnostics.
            raise RuntimeError(f"Keycloak {method} {urllib.parse.urlsplit(request.full_url).path} returned HTTP {exc.code}") from None


def reconcile_mas_client_scopes(admin: KeycloakAdmin, realm: str, scope_config: dict) -> None:
    encoded_realm = urllib.parse.quote(realm, safe="")
    status, existing_scopes = admin.request("GET", f"admin/realms/{encoded_realm}/client-scopes")
    if status != 200 or not isinstance(existing_scopes, list):
        raise RuntimeError("Keycloak client-scope catalog could not be read")
    scopes_by_name = {scope.get("name"): scope for scope in existing_scopes if scope.get("name")}

    for configured in scope_config["client_scopes"]:
        name = configured["name"]
        existing = scopes_by_name.get(name)
        scope_id = existing.get("id") if existing else configured["id"]
        scope_path = f"admin/realms/{encoded_realm}/client-scopes/{urllib.parse.quote(scope_id, safe='')}"
        if existing:
            admin.request("PUT", scope_path, {**configured, "id": scope_id})
        else:
            admin.request("POST", f"admin/realms/{encoded_realm}/client-scopes", {**configured, "id": scope_id})

    _, clients = admin.request(
        "GET",
        f"admin/realms/{encoded_realm}/clients?{urllib.parse.urlencode({'clientId': scope_config['client_id']})}",
    )
    client = next((entry for entry in clients if entry.get("clientId") == scope_config["client_id"]), None)
    if not client or not client.get("id"):
        raise RuntimeError("The Matrix Authentication Service OIDC client is missing")
    client_id = urllib.parse.quote(client["id"], safe="")

    _, refreshed_scopes = admin.request("GET", f"admin/realms/{encoded_realm}/client-scopes")
    ids_by_name = {scope.get("name"): scope.get("id") for scope in refreshed_scopes if scope.get("name")}
    _, assigned_defaults = admin.request(
        "GET", f"admin/realms/{encoded_realm}/clients/{client_id}/default-client-scopes"
    )
    assigned_names = {scope.get("name") for scope in assigned_defaults if scope.get("name")}
    for name in scope_config["default_client_scopes"]:
        scope_id = ids_by_name.get(name)
        if not scope_id:
            raise RuntimeError(f"Required OIDC client scope {name} is missing")
        if name not in assigned_names:
            admin.request(
                "PUT",
                f"admin/realms/{encoded_realm}/clients/{client_id}/default-client-scopes/{urllib.parse.quote(scope_id, safe='')}",
            )

    _, verified_defaults = admin.request(
        "GET", f"admin/realms/{encoded_realm}/clients/{client_id}/default-client-scopes"
    )
    verified_names = {scope.get("name") for scope in verified_defaults if scope.get("name")}
    missing = set(scope_config["default_client_scopes"]) - verified_names
    if missing:
        raise RuntimeError("Keycloak did not retain the required MAS OIDC client scopes")


def admin_token(base_url: str, context: ssl.SSLContext, env: dict[str, str]) -> str:
    username = env.get("KEYCLOAK_BOOTSTRAP_ADMIN", "")
    password = env.get("KEYCLOAK_BOOTSTRAP_PASSWORD", "")
    if not username or not password or password == "CHANGE_ME":
        raise ValueError("Keycloak bootstrap administrator credentials are missing")
    data = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": username,
            "password": password,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/realms/master/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    transient_statuses = {429, 502, 503, 504}
    for attempt in range(60):
        try:
            with urllib.request.urlopen(request, context=context, timeout=20) as response:
                payload = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in transient_statuses or attempt == 59:
                raise RuntimeError(f"Keycloak admin token request returned HTTP {exc.code}") from None
            time.sleep(2)
        except (urllib.error.URLError, OSError):
            if attempt == 59:
                raise RuntimeError("Keycloak did not become reachable during bootstrap") from None
            time.sleep(2)
    else:  # pragma: no cover - the loop either breaks or raises
        raise RuntimeError("Keycloak admin token request did not complete")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Keycloak admin token response did not contain an access token")
    return token


def configure_export_client_scopes(admin: KeycloakAdmin, realm: str, config: dict) -> None:
    """Emit only the export role for the existing PKCE client, idempotently."""
    if config.get("client_id") != "matrix-export-client" or config.get("realm_roles") != ["org-export-admin"]:
        raise ValueError("Export client scope must contain only org-export-admin")
    base = f"admin/realms/{urllib.parse.quote(realm, safe='')}"
    _, clients = admin.request("GET", f"{base}/clients?{urllib.parse.urlencode({'clientId': config['client_id']})}")
    client = next((item for item in clients if item.get("clientId") == config["client_id"]), None)
    if not client or not client.get("id"):
        raise RuntimeError("The export PKCE client is missing")
    path = f"{base}/clients/{urllib.parse.quote(client['id'], safe='')}"
    _, current = admin.request("GET", path)
    if current.get("fullScopeAllowed") is not False:
        admin.request("PUT", path, {**current, "fullScopeAllowed": False})
    _, existing = admin.request("GET", f"{path}/protocol-mappers/models")
    by_name = {item.get("name"): item for item in existing}
    for configured in config["protocol_mappers"]:
        mapper = by_name.get(configured["name"])
        if mapper is None:
            admin.request("POST", f"{path}/protocol-mappers/models", configured)
        elif any(mapper.get(key) != value for key, value in configured.items()):
            admin.request("PUT", f"{path}/protocol-mappers/models/{urllib.parse.quote(mapper['id'], safe='')}",
                          {**configured, "id": mapper["id"]})
    _, existing_roles = admin.request("GET", f"{path}/scope-mappings/realm")
    extra = [role for role in existing_roles if role.get("name") not in config["realm_roles"]]
    if extra:
        admin.request("DELETE", f"{path}/scope-mappings/realm", extra)
    names = {role.get("name") for role in existing_roles}
    for name in config["realm_roles"]:
        if name not in names:
            _, role = admin.request("GET", f"{base}/roles/{urllib.parse.quote(name, safe='')}")
            admin.request("POST", f"{path}/scope-mappings/realm", [role])
    _, verified_client = admin.request("GET", path)
    _, verified_roles = admin.request("GET", f"{path}/scope-mappings/realm")
    _, verified_mappers = admin.request("GET", f"{path}/protocol-mappers/models")
    if (verified_client.get("fullScopeAllowed") is not False
            or {role.get("name") for role in verified_roles} != set(config["realm_roles"])
            or any(not any(all(actual.get(key) == value for key, value in expected.items())
                           for actual in verified_mappers) for expected in config["protocol_mappers"])):
        raise RuntimeError("Keycloak did not retain the restricted export role claim")


def configure_profile(admin: KeycloakAdmin, realm: str, attribute: dict) -> None:
    encoded_realm = urllib.parse.quote(realm, safe="")
    path = f"admin/realms/{encoded_realm}/users/profile"
    status, current = admin.request("GET", path)
    if status != 200 or not isinstance(current, dict):
        raise RuntimeError("Keycloak user-profile configuration could not be read")
    profile = dict(current)
    attributes = list(profile.get("attributes") or [])
    found = False
    for index, existing in enumerate(attributes):
        if existing.get("name") == attribute["name"]:
            attributes[index] = attribute
            found = True
            break
    if not found:
        attributes.append(attribute)
    profile["attributes"] = attributes
    if profile != current:
        admin.request("PUT", path, profile)

    _, verified = admin.request("GET", path)
    configured = next((entry for entry in verified.get("attributes", []) if entry.get("name") == attribute["name"]), None)
    if (
        not configured
        or configured.get("permissions") != attribute.get("permissions")
        or configured.get("validations", {}).get("options", {}).get("options")
        != attribute.get("validations", {}).get("options", {}).get("options")
        or configured.get("annotations", {}).get("inputType") != attribute.get("annotations", {}).get("inputType")
    ):
        raise RuntimeError("Keycloak did not retain the managed security_clearance profile")


def deterministic_scope_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"matrix-keycloak-demo:client-scope:{name}"))


def split_display_name(display_name: str) -> tuple[str, str]:
    parts = display_name.split(maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (display_name, "Demo")


def configure_mas_client_scopes(admin: KeycloakAdmin, realm: str, config: dict) -> None:
    encoded_realm = urllib.parse.quote(realm, safe="")
    status, existing_scopes = admin.request("GET", f"admin/realms/{encoded_realm}/client-scopes")
    if status != 200 or not isinstance(existing_scopes, list):
        raise RuntimeError("Keycloak client-scope catalog could not be read")
    scopes_by_name = {entry.get("name"): entry for entry in existing_scopes if entry.get("name")}

    for configured in config["client_scopes"]:
        name = configured["name"]
        existing = scopes_by_name.get(name)
        scope_id = existing.get("id") if existing else deterministic_scope_id(name)
        path = f"admin/realms/{encoded_realm}/client-scopes/{urllib.parse.quote(scope_id, safe='')}"
        if existing:
            _, current = admin.request("GET", path)
            attributes = dict(current.get("attributes") or {})
            attributes.update(configured.get("attributes") or {})
            mappers = list(current.get("protocolMappers") or [])
            for configured_mapper in configured.get("protocolMappers", []):
                index = next((i for i, mapper in enumerate(mappers) if mapper.get("name") == configured_mapper["name"]), None)
                if index is None:
                    mappers.append(configured_mapper)
                else:
                    mappers[index] = configured_mapper
            payload = {
                **configured,
                "id": scope_id,
                "attributes": attributes,
                "protocolMappers": mappers,
            }
            admin.request("PUT", path, payload)
        else:
            admin.request(
                "POST",
                f"admin/realms/{encoded_realm}/client-scopes",
                {**configured, "id": scope_id},
            )

    _, clients = admin.request(
        "GET",
        f"admin/realms/{encoded_realm}/clients?{urllib.parse.urlencode({'clientId': config['client_id']})}",
    )
    client = next((entry for entry in clients if entry.get("clientId") == config["client_id"]), None)
    if not client or not client.get("id"):
        raise RuntimeError("The Matrix Authentication Service OIDC client is missing")
    client_id = urllib.parse.quote(client["id"], safe="")

    _, refreshed_scopes = admin.request("GET", f"admin/realms/{encoded_realm}/client-scopes")
    ids_by_name = {entry.get("name"): entry.get("id") for entry in refreshed_scopes if entry.get("name")}
    _, assigned = admin.request(
        "GET", f"admin/realms/{encoded_realm}/clients/{client_id}/default-client-scopes"
    )
    assigned_names = {entry.get("name") for entry in assigned if entry.get("name")}
    for name in config["default_client_scopes"]:
        scope_id = ids_by_name.get(name)
        if not scope_id:
            raise RuntimeError(f"Required OIDC client scope {name} is missing")
        if name not in assigned_names:
            admin.request(
                "PUT",
                f"admin/realms/{encoded_realm}/clients/{client_id}/default-client-scopes/{urllib.parse.quote(scope_id, safe='')}",
            )

    _, verified = admin.request(
        "GET", f"admin/realms/{encoded_realm}/clients/{client_id}/default-client-scopes"
    )
    verified_names = {entry.get("name") for entry in verified if entry.get("name")}
    if set(config["default_client_scopes"]) - verified_names:
        raise RuntimeError("Keycloak did not retain the required MAS OIDC client scopes")


def _matrix_access_base(realm: str) -> str:
    return f"admin/realms/{urllib.parse.quote(realm, safe='')}"


def matrix_access_flow_contract(config: dict) -> dict:
    flow = config["browser_flow"]
    role_name = config["role"]["name"]
    return {
        "root_alias": flow["alias"],
        "root_required_steps": [
            {"alias": flow["methods_alias"], "requirement": "REQUIRED", "priority": 10},
            {"alias": flow["missing_role_alias"], "requirement": "CONDITIONAL", "priority": 20},
        ],
        "methods_source_alias": flow["methods_source_alias"],
        "gate": {
            "alias": flow["missing_role_alias"],
            "requirement": "CONDITIONAL",
            "priority": 20,
            "condition": {
                "provider_id": flow["condition_authenticator"],
                "requirement": "REQUIRED",
                "priority": 10,
                "config": {flow["condition_role_key"]: role_name, flow["condition_negate_key"]: "true"},
            },
            "deny": {"provider_id": flow["deny_authenticator"], "requirement": "REQUIRED", "priority": 20},
        },
        "client_id": config["client_id"],
        "client_binding": "browser",
    }


def _flow_location_id(headers: dict[str, str]) -> str:
    location = next((value for key, value in headers.items() if key.lower() == "location"), "")
    flow_id = urllib.parse.urlsplit(location).path.rstrip("/").rsplit("/", 1)[-1]
    if not flow_id or "/" in flow_id:
        raise RuntimeError("Keycloak did not return an identifier for the Matrix access flow object")
    return flow_id


def ensure_matrix_access_role(admin: KeycloakAdmin, realm: str, role: dict) -> dict:
    """Ensure the exact realm role exists without assigning it to anyone."""
    role_name = role.get("name")
    if role_name != "matrix-access":
        raise ValueError("Matrix login entitlement must use the matrix-access realm role")
    base = _matrix_access_base(realm)
    _, roles = admin.request("GET", f"{base}/roles")
    if not isinstance(roles, list):
        raise RuntimeError("Keycloak realm roles could not be read")
    existing = next((item for item in roles if item.get("name") == role_name), None)
    if existing is None:
        admin.request("POST", f"{base}/roles", role)
        _, roles = admin.request("GET", f"{base}/roles")
        existing = next((item for item in roles if item.get("name") == role_name), None)
    if not existing or existing.get("clientRole") is True:
        raise RuntimeError("Keycloak did not retain matrix-access as a realm role")
    return existing


def _flow_executions(admin: KeycloakAdmin, realm: str, alias: str) -> list[dict]:
    base = _matrix_access_base(realm)
    _, executions = admin.request(
        "GET", f"{base}/authentication/flows/{urllib.parse.quote(alias, safe='')}/executions"
    )
    if not isinstance(executions, list):
        raise RuntimeError(f"Keycloak executions for flow {alias} could not be read")
    return executions


def _direct_flow_executions(admin: KeycloakAdmin, realm: str, alias: str) -> list[dict]:
    return [item for item in _flow_executions(admin, realm, alias) if item.get("level") == 0]


def _create_flow(admin: KeycloakAdmin, realm: str, *, alias: str, description: str, top_level: bool) -> str:
    base = _matrix_access_base(realm)
    status, _payload, headers = admin.request_with_headers(
        "POST",
        f"{base}/authentication/flows",
        {
            "alias": alias,
            "description": description,
            "providerId": "basic-flow",
            "topLevel": top_level,
            "builtIn": False,
        },
    )
    if status != 201:
        raise RuntimeError(f"Keycloak could not create the Matrix access flow {alias}")
    return _flow_location_id(headers)


def _create_child_flow(admin: KeycloakAdmin, realm: str, parent_alias: str, alias: str, priority: int) -> dict:
    base = _matrix_access_base(realm)
    status, _payload = admin.request(
        "POST",
        f"{base}/authentication/flows/{urllib.parse.quote(parent_alias, safe='')}/executions/flow",
        {"alias": alias, "type": "basic-flow", "provider": "", "priority": priority,
         "description": f"Managed Matrix access subflow: {alias}"},
    )
    if status not in (200, 201):
        raise RuntimeError(f"Keycloak could not add the Matrix access subflow {alias}")
    entries = _direct_flow_executions(admin, realm, parent_alias)
    matches = [item for item in entries if item.get("authenticationFlow") is True and item.get("displayName") == alias]
    if len(matches) != 1 or not matches[0].get("flowId"):
        raise RuntimeError(f"Keycloak did not retain exactly one Matrix access subflow {alias}")
    return matches[0]


def _set_flow_execution_requirement(
    admin: KeycloakAdmin, realm: str, parent_alias: str, execution: dict, requirement: str, priority: int
) -> dict:
    if execution.get("requirement") != requirement or execution.get("priority") != priority:
        admin.request(
            "PUT",
            f"{_matrix_access_base(realm)}/authentication/flows/{urllib.parse.quote(parent_alias, safe='')}/executions",
            {"id": execution["id"], "requirement": requirement, "priority": priority},
        )
        execution = next(
            item for item in _direct_flow_executions(admin, realm, parent_alias) if item.get("id") == execution["id"]
        )
    if execution.get("requirement") != requirement or execution.get("priority") != priority:
        raise RuntimeError(f"Keycloak did not retain the {requirement} requirement for {parent_alias}")
    return execution


def _ensure_child_flow(admin: KeycloakAdmin, realm: str, parent_alias: str, alias: str,
                       requirement: str, priority: int) -> dict:
    entries = _direct_flow_executions(admin, realm, parent_alias)
    matches = [item for item in entries if item.get("authenticationFlow") is True and item.get("displayName") == alias]
    if not matches:
        execution = _create_child_flow(admin, realm, parent_alias, alias, priority)
    elif len(matches) == 1:
        execution = matches[0]
    else:
        raise RuntimeError(f"Keycloak contains duplicate Matrix access subflows named {alias}")
    return _set_flow_execution_requirement(admin, realm, parent_alias, execution, requirement, priority)


def _ensure_authenticator(admin: KeycloakAdmin, realm: str, parent_alias: str, parent_id: str,
                          provider_id: str, requirement: str, priority: int) -> dict:
    entries = _direct_flow_executions(admin, realm, parent_alias)
    matches = [item for item in entries if item.get("authenticationFlow") is not True and item.get("providerId") == provider_id]
    if len(matches) > 1:
        raise RuntimeError(f"Keycloak contains duplicate {provider_id} executions in {parent_alias}")
    if matches:
        execution = matches[0]
        if execution.get("requirement") != requirement or execution.get("priority") != priority:
            admin.request(
                "PUT",
                f"{_matrix_access_base(realm)}/authentication/flows/{urllib.parse.quote(parent_alias, safe='')}/executions",
                {"id": execution["id"], "requirement": requirement, "priority": priority},
            )
            execution = next(
                item for item in _direct_flow_executions(admin, realm, parent_alias)
                if item.get("id") == execution["id"]
            )
    else:
        status, _payload, headers = admin.request_with_headers(
            "POST",
            f"{_matrix_access_base(realm)}/authentication/executions",
            {
                "parentFlow": parent_id,
                "authenticator": provider_id,
                "authenticatorFlow": False,
                "requirement": requirement,
                "priority": priority,
            },
        )
        if status != 201:
            raise RuntimeError(f"Keycloak could not add the {provider_id} Matrix access condition")
        execution_id = _flow_location_id(headers)
        execution = next(
            item for item in _direct_flow_executions(admin, realm, parent_alias) if item.get("id") == execution_id
        )
    if execution.get("requirement") != requirement or execution.get("priority") != priority:
        raise RuntimeError(f"Keycloak did not retain the {requirement} requirement for {provider_id}")
    return execution


def _ensure_role_condition_config(admin: KeycloakAdmin, realm: str, execution: dict,
                                  parent_alias: str, config_alias: str, config_values: dict) -> None:
    base = _matrix_access_base(realm)
    config_id = execution.get("authenticationConfig")
    current = None
    if isinstance(config_id, str) and config_id:
        _, current = admin.request("GET", f"{base}/authentication/config/{urllib.parse.quote(config_id, safe='')}")
    expected = config_values
    if isinstance(current, dict) and current.get("alias") == config_alias and current.get("config") == expected:
        return
    status, _payload = admin.request(
        "POST",
        f"{base}/authentication/executions/{urllib.parse.quote(execution['id'], safe='')}/config",
        {"alias": config_alias, "config": expected},
    )
    if status not in (200, 201):
        raise RuntimeError("Keycloak could not configure the missing Matrix access role condition")
    refreshed = next(
        item for item in _direct_flow_executions(admin, realm, parent_alias)
        if item.get("id") == execution["id"]
    )
    new_config_id = refreshed.get("authenticationConfig")
    if not isinstance(new_config_id, str) or not new_config_id:
        raise RuntimeError("Keycloak did not attach the Matrix access role condition configuration")
    _, verified = admin.request("GET", f"{base}/authentication/config/{urllib.parse.quote(new_config_id, safe='')}")
    if verified.get("alias") != config_alias or verified.get("config") != expected:
        raise RuntimeError("Keycloak did not retain the negated matrix-access role condition")


def _update_flow_top_level(admin: KeycloakAdmin, realm: str, flow: dict, *, top_level: bool) -> None:
    if flow.get("topLevel") is top_level:
        return
    base = _matrix_access_base(realm)
    admin.request(
        "PUT",
        f"{base}/authentication/flows/{urllib.parse.quote(flow['id'], safe='')}",
        {"id": flow["id"], "alias": flow["alias"], "description": flow.get("description", ""),
         "providerId": flow.get("providerId", "basic-flow"), "topLevel": top_level, "builtIn": False},
    )


def configure_matrix_access_browser_flow(admin: KeycloakAdmin, realm: str, config: dict) -> str:
    """Create and verify the versioned entitlement flow without binding any client."""
    base = _matrix_access_base(realm)
    contract = matrix_access_flow_contract(config)
    flow_config = config["browser_flow"]
    flows_path = f"{base}/authentication/flows"
    _, top_level_flows = admin.request("GET", flows_path)
    if not isinstance(top_level_flows, list):
        raise RuntimeError("Keycloak authentication-flow catalog could not be read")
    root = next((flow for flow in top_level_flows if flow.get("alias") == flow_config["alias"]), None)
    if root is None:
        root_id = _create_flow(
            admin, realm, alias=flow_config["alias"], description=flow_config["description"], top_level=True
        )
        root = {"id": root_id, "alias": flow_config["alias"], "topLevel": True}
    else:
        root_id = root.get("id")
    if not root_id:
        raise RuntimeError("Keycloak Matrix access browser flow has no identifier")

    root_entries = _direct_flow_executions(admin, realm, flow_config["alias"])
    method_entries = [
        item for item in root_entries
        if item.get("authenticationFlow") is True and item.get("displayName") == flow_config["methods_alias"]
    ]
    if len(method_entries) > 1:
        raise RuntimeError("Keycloak has duplicate browser-method subflows in the Matrix access flow")
    if method_entries:
        methods_id = method_entries[0].get("flowId")
    else:
        _, top_level_flows = admin.request("GET", flows_path)
        copied = next((flow for flow in top_level_flows if flow.get("alias") == flow_config["methods_alias"]), None)
        if copied is None:
            browser = next((flow for flow in top_level_flows
                            if flow.get("alias") == contract["methods_source_alias"]), None)
            if not browser:
                raise RuntimeError("Keycloak built-in Browser flow is missing; Matrix access setup cannot continue")
            status, _payload, headers = admin.request_with_headers(
                "POST",
                f"{flows_path}/{urllib.parse.quote(contract['methods_source_alias'], safe='')}/copy",
                {"newName": flow_config["methods_alias"]},
            )
            if status != 201:
                raise RuntimeError("Keycloak could not copy its built-in Browser authentication methods")
            methods_id = _flow_location_id(headers)
            top_level_flows = admin.request("GET", flows_path)[1]
            copied = next((flow for flow in top_level_flows if flow.get("id") == methods_id), None)
        if copied is None or not copied.get("id"):
            raise RuntimeError("Keycloak did not retain the copied Browser methods")
        methods_id = copied["id"]
        status, _payload, _headers = admin.request_with_headers(
            "POST",
            f"{base}/authentication/executions",
            {"parentFlow": root_id, "flowId": methods_id, "authenticatorFlow": True,
             "requirement": "REQUIRED", "priority": 10},
        )
        if status != 201:
            raise RuntimeError("Keycloak could not place Browser methods before the Matrix access gate")
    if not methods_id:
        raise RuntimeError("The Matrix access Browser methods flow has no identifier")
    root_entries = _direct_flow_executions(admin, realm, flow_config["alias"])
    method_entries = [
        item for item in root_entries
        if item.get("authenticationFlow") is True and item.get("displayName") == flow_config["methods_alias"]
    ]
    if len(method_entries) != 1:
        raise RuntimeError("The copied Browser methods are not attached exactly once")
    _set_flow_execution_requirement(admin, realm, flow_config["alias"], method_entries[0], "REQUIRED", 10)
    methods_flow = next((flow for flow in top_level_flows if flow.get("id") == methods_id), None)
    if methods_flow is not None:
        _update_flow_top_level(admin, realm, methods_flow, top_level=False)

    missing_role = _ensure_child_flow(
        admin, realm, flow_config["alias"], flow_config["missing_role_alias"],
        contract["gate"]["requirement"], contract["gate"]["priority"],
    )
    missing_role_id = missing_role.get("flowId")
    if not missing_role_id:
        raise RuntimeError("The conditional Matrix access role gate has no flow identifier")
    condition = _ensure_authenticator(
        admin, realm, flow_config["missing_role_alias"], missing_role_id,
        contract["gate"]["condition"]["provider_id"],
        contract["gate"]["condition"]["requirement"], contract["gate"]["condition"]["priority"],
    )
    _ensure_role_condition_config(
        admin, realm, condition, flow_config["missing_role_alias"], flow_config["condition_config_alias"],
        contract["gate"]["condition"]["config"],
    )
    _ensure_authenticator(
        admin, realm, flow_config["missing_role_alias"], missing_role_id,
        contract["gate"]["deny"]["provider_id"],
        contract["gate"]["deny"]["requirement"], contract["gate"]["deny"]["priority"],
    )

    return validate_matrix_access_browser_flow(admin, realm, config)


def validate_matrix_access_browser_flow(admin: KeycloakAdmin, realm: str, config: dict) -> str:
    """Read-only validation of the unbound versioned MAS browser flow."""
    base = _matrix_access_base(realm)
    contract = matrix_access_flow_contract(config)
    flow_config = config["browser_flow"]
    _, top_level_flows = admin.request("GET", f"{base}/authentication/flows")
    root = next((flow for flow in top_level_flows if flow.get("alias") == flow_config["alias"]), None)
    if not root or not root.get("id") or root.get("topLevel") is not True:
        raise RuntimeError("The versioned Matrix access Browser flow is missing or is not top-level")
    root_id = root["id"]
    root_entries = _direct_flow_executions(admin, realm, flow_config["alias"])
    role_gate_entries = _direct_flow_executions(admin, realm, flow_config["missing_role_alias"])
    direct_subflows = {
        item.get("displayName"): (item.get("requirement"), item.get("priority"))
        for item in root_entries if item.get("authenticationFlow") is True
    }
    expected_subflows = {step["alias"]: (step["requirement"], step["priority"])
                         for step in contract["root_required_steps"]}
    if len(root_entries) != 2 or direct_subflows != expected_subflows:
        raise RuntimeError("The versioned Matrix access Browser flow has an unexpected execution tree")
    methods_entry = next((item for item in root_entries
                          if item.get("displayName") == flow_config["methods_alias"]
                          and item.get("authenticationFlow") is True), None)
    methods_entries = _direct_flow_executions(admin, realm, flow_config["methods_alias"])
    if (not methods_entry or not methods_entry.get("flowId")
            or not any(item.get("providerId") == "auth-cookie" and item.get("requirement") == "ALTERNATIVE"
                       for item in methods_entries)):
        raise RuntimeError("The versioned flow does not preserve the cloned Browser cookie method")
    leaf = {item.get("providerId"): (item.get("requirement"), item.get("priority")) for item in role_gate_entries}
    expected_leaf = {
        contract["gate"]["condition"]["provider_id"]: (
            contract["gate"]["condition"]["requirement"], contract["gate"]["condition"]["priority"]
        ),
        contract["gate"]["deny"]["provider_id"]: (
            contract["gate"]["deny"]["requirement"], contract["gate"]["deny"]["priority"]
        ),
    }
    if len(role_gate_entries) != 2 or leaf != expected_leaf:
        raise RuntimeError("The Matrix access gate must contain only the required role condition and deny step")
    condition = next((item for item in role_gate_entries
                      if item.get("providerId") == contract["gate"]["condition"]["provider_id"]), None)
    condition_id = condition.get("authenticationConfig") if condition else None
    if not condition_id:
        raise RuntimeError("The versioned Matrix access role condition has no configuration")
    _, condition_config = admin.request(
        "GET", f"{base}/authentication/config/{urllib.parse.quote(condition_id, safe='')}"
    )
    if (condition_config.get("alias") != flow_config["condition_config_alias"]
            or condition_config.get("config") != contract["gate"]["condition"]["config"]):
        raise RuntimeError("The versioned flow does not deny users missing the matrix-access role")
    return root_id


def bind_matrix_access_browser_flow(admin: KeycloakAdmin, realm: str, config: dict) -> str:
    """Explicitly bind only MAS to a fully validated versioned flow."""
    root_id = validate_matrix_access_browser_flow(admin, realm, config)
    base = _matrix_access_base(realm)
    _, clients = admin.request(
        "GET", f"{base}/clients?{urllib.parse.urlencode({'clientId': config['client_id']})}"
    )
    client = next((entry for entry in clients if entry.get("clientId") == config["client_id"]), None)
    if not client or not client.get("id"):
        raise RuntimeError("The MAS Keycloak client is missing")
    client_path = f"{base}/clients/{urllib.parse.quote(client['id'], safe='')}"
    _, current_client = admin.request("GET", client_path)
    overrides = dict(current_client.get("authenticationFlowBindingOverrides") or {})
    if overrides.get("browser") != root_id:
        overrides["browser"] = root_id
        admin.request("PUT", client_path, {**current_client, "authenticationFlowBindingOverrides": overrides})
    _, verified_client = admin.request("GET", client_path)
    if (verified_client.get("authenticationFlowBindingOverrides") or {}).get("browser") != root_id:
        raise RuntimeError("Keycloak did not bind the Matrix access Browser flow to MAS")
    return root_id


def matrix_access_migration_usernames(seed: dict, config: dict) -> list[str]:
    seeded = [entry.get("username") for entry in seed.get("users", []) if isinstance(entry, dict)]
    additional = config.get("migration", {}).get("additional_existing_usernames", [])
    if additional != []:
        raise ValueError("Matrix access migration accepts only the fixed public seed allowlist")
    expected = {
        *(f"demo-{number:02d}" for number in range(1, 21)),
        "export-test-01", "export-test-02",
    }
    if (any(not isinstance(name, str) or not name for name in seeded)
            or len(seeded) != len(set(seeded))
            or set(seeded) != expected):
        raise ValueError("Matrix access migration allowlist must match exactly the 22 fixed seed identities")
    return seeded


def grant_matrix_access_to_existing_users(admin: KeycloakAdmin, realm: str, role: dict,
                                          usernames: list[str]) -> tuple[int, int]:
    """Explicit one-time upgrade: add only the role mapping; never rewrite credentials."""
    base = _matrix_access_base(realm)
    role_id = role.get("id")
    if not role_id:
        raise RuntimeError("The matrix-access role has no Keycloak identifier")
    added = 0
    for username in usernames:
        query = urllib.parse.urlencode({"username": username, "exact": "true", "briefRepresentation": "false"})
        _, matches = admin.request("GET", f"{base}/users?{query}")
        exact = [entry for entry in matches if entry.get("username") == username]
        if len(exact) != 1 or not exact[0].get("id"):
            raise RuntimeError(f"Matrix access migration expected exactly one existing user named {username}")
        user_id = urllib.parse.quote(exact[0]["id"], safe="")
        mappings_path = f"{base}/users/{user_id}/role-mappings/realm"
        _, mappings = admin.request("GET", mappings_path)
        if not any(entry.get("id") == role_id for entry in mappings):
            admin.request("POST", mappings_path, [role])
            added += 1
        _, verified = admin.request("GET", mappings_path)
        if not any(entry.get("id") == role_id or entry.get("name") == role.get("name") for entry in verified):
            raise RuntimeError(f"Keycloak did not retain matrix-access for {username}")
    return len(usernames), added


def apply_user_clearance(admin: KeycloakAdmin, realm: str, users: list[dict]) -> int:
    encoded_realm = urllib.parse.quote(realm, safe="")
    changed = 0
    for seeded_user in users:
        username = seeded_user["username"]
        query = urllib.parse.urlencode({"username": username, "exact": "true", "briefRepresentation": "false"})
        status, matches = admin.request("GET", f"admin/realms/{encoded_realm}/users?{query}")
        if status != 200 or not isinstance(matches, list):
            raise RuntimeError(f"Keycloak lookup failed for synthetic user {username}")
        match = next((candidate for candidate in matches if candidate.get("username") == username), None)
        if not match or not match.get("id"):
            raise RuntimeError(f"Keycloak user {username} is missing; refusing a partial policy seed")
        attributes = dict(match.get("attributes") or {})
        expected = [seeded_user["clearance"]]
        first_name, last_name = split_display_name(seeded_user["display_name"])
        email = f"{username}@foldfence-demo.invalid"
        profile_changed = False
        if not match.get("email"):
            match["email"] = email
            profile_changed = True
        if not match.get("firstName") or not match.get("lastName"):
            match["firstName"] = first_name
            match["lastName"] = last_name
            profile_changed = True
        if attributes.get("security_clearance") != expected or profile_changed:
            attributes["security_clearance"] = expected
            match["attributes"] = attributes
            user_id = urllib.parse.quote(match["id"], safe="")
            admin.request(
                "PUT",
                f"admin/realms/{encoded_realm}/users/{user_id}",
                {
                    "id": match["id"],
                    "username": username,
                    "enabled": match.get("enabled", True),
                    "email": match.get("email", email),
                    "firstName": match.get("firstName", first_name),
                    "lastName": match.get("lastName", last_name),
                    "attributes": attributes,
                },
            )
            changed += 1
        _, verified = admin.request("GET", f"admin/realms/{encoded_realm}/users/{urllib.parse.quote(match['id'], safe='')}")
        if (
            (verified.get("attributes") or {}).get("security_clearance") != expected
            or not verified.get("email")
            or not verified.get("firstName")
            or not verified.get("lastName")
        ):
            raise RuntimeError(f"Keycloak did not retain the demo profile and clearance for {username}")
    return changed


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile the managed demo Keycloak configuration.")
    parser.add_argument(
        "--grant-matrix-access-existing-demo-users",
        action="store_true",
        help="Explicitly migrate the reviewed existing demo-user allowlist to the matrix-access realm role.",
    )
    return parser


def reconcile_keycloak_configuration(
    admin: KeycloakAdmin,
    realm: str,
    *,
    matrix_access_config: dict,
    migration_requested: bool,
    seed: dict,
    oidc_scopes: dict,
    export_scopes: dict,
    profile_attribute: dict,
) -> tuple[int, tuple[int, int] | None]:
    """Apply the Matrix entitlement migration before making its gate reachable.

    The explicit existing-user migration is deliberately ordered before the
    MAS client flow binding. If any allowlisted user cannot be granted and
    verified, the flow remains unbound and existing Matrix logins are not cut
    off partway through the migration. The new flow is built and validated
    unbound, then bound to MAS as the final flow step.
    """
    matrix_access_role = ensure_matrix_access_role(admin, realm, matrix_access_config["role"])
    migration_summary = None
    if migration_requested:
        allowlist = matrix_access_migration_usernames(seed, matrix_access_config)
        migration_summary = grant_matrix_access_to_existing_users(
            admin, realm, matrix_access_role, allowlist
        )

    configure_matrix_access_browser_flow(admin, realm, matrix_access_config)
    bind_matrix_access_browser_flow(admin, realm, matrix_access_config)
    configure_mas_client_scopes(admin, realm, oidc_scopes)
    configure_export_client_scopes(admin, realm, export_scopes)
    configure_profile(admin, realm, profile_attribute)
    changed = apply_user_clearance(admin, realm, seed["users"])
    return changed, migration_summary


def main() -> int:
    try:
        args = build_argument_parser().parse_args()
        env = read_env()
        realm = env.get("KEYCLOAK_REALM", "foldfence-demo")
        base_url = (internal_keycloak_url(env) if os.environ.get("KEYCLOAK_ADMIN_USE_DOCKER") == "1"
                    else env.get("KEYCLOAK_PUBLIC_URL", "").strip())
        if not base_url:
            raise ValueError("KEYCLOAK_PUBLIC_URL is missing")
        context = tls_context(env)
        profile_doc = read_json(ROOT / "config" / "keycloak" / "user-profile.json")
        oidc_scopes = read_json(ROOT / "config" / "keycloak" / "mas-client-scopes.json")
        matrix_access_config = read_json(ROOT / "config" / "keycloak" / "matrix-access.json")
        export_scopes = read_json(ROOT / "config" / "keycloak" / "export-client-scopes.json")
        seed = read_json(ROOT / "config" / "seed" / "demo-users.json")
        expected_options = [entry["id"] for entry in seed["clearance_levels"]]
        configured_options = profile_doc["attribute"]["validations"]["options"]["options"]
        if configured_options != expected_options:
            raise ValueError("Keycloak clearance options must match the demo user seed")
        token = admin_token(base_url, context, env)
        admin = KeycloakAdmin(base_url, context, token)
        changed, migration_summary = reconcile_keycloak_configuration(
            admin,
            realm,
            matrix_access_config=matrix_access_config,
            migration_requested=args.grant_matrix_access_existing_demo_users,
            seed=seed,
            oidc_scopes=oidc_scopes,
            export_scopes=export_scopes,
            profile_attribute=profile_doc["attribute"],
        )
    except (OSError, ValueError, KeyError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Keycloak policy inputs could not be reconciled: {exc}", file=sys.stderr)
        return 1
    migration_note = (
        f" Explicitly migrated {migration_summary[0]} existing demo users ({migration_summary[1]} role mappings added)."
        if migration_summary is not None
        else " Existing-user Matrix access migration was not requested."
    )
    print(
        f"Keycloak MAS OIDC scopes and client-scoped Matrix access flow bound to MAS; verified clearance for "
        f"{len(seed['users'])} synthetic users ({changed} values updated).{migration_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
