#!/usr/bin/env python3
"""Check public Matrix, MAS, Keycloak, Element Web, and export endpoints."""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


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
        raise ValueError("DEMO_GENERATED_DIR must remain inside internal/ for self-signed TLS") from None
    certificate = generated / "tls" / "dev-root-ca.pem"
    if not certificate.is_file():
        certificate = ROOT / "internal" / "deploy" / "internal-homeserver" / "tls" / "dev-root-ca.pem"
    if not certificate.is_file():
        raise ValueError("The generated internal TLS root CA is missing")
    return ssl.create_default_context(cafile=str(certificate))


def fetch_json(url: str, context: ssl.SSLContext) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response was not a JSON object")
    return payload


def fetch_page(url: str, context: ssl.SSLContext) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "matrix-keycloak-demo-health/1"})
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")


def validate_endpoints(env: dict[str, str], context: ssl.SSLContext) -> list[str]:
    base = env["MATRIX_PUBLIC_URL"].rstrip("/")
    auth = env["MATRIX_AUTH_PUBLIC_URL"].rstrip("/")
    issuer = env["KEYCLOAK_ISSUER_URL"].rstrip("/")

    versions = fetch_json(f"{base}/_matrix/client/versions", context)
    if not isinstance(versions.get("versions"), list):
        raise ValueError("Matrix versions endpoint returned an invalid response")

    well_known = fetch_json(f"{base}/.well-known/matrix/client", context)
    homeserver = well_known.get("m.homeserver", {})
    if not isinstance(homeserver, dict) or homeserver.get("base_url", "").rstrip("/") != base:
        raise ValueError("Matrix client well-known points at a different homeserver")
    foci = well_known.get("org.matrix.msc4143.rtc_foci", [])
    expected_jwt_url = env.get("LIVEKIT_JWT_PUBLIC_URL", "").rstrip("/")
    if not expected_jwt_url or not isinstance(foci, list) or not any(
        isinstance(focus, dict)
        and focus.get("type") == "livekit"
        and focus.get("livekit_service_url", "").rstrip("/") == expected_jwt_url
        for focus in foci
    ):
        raise ValueError("Matrix client well-known does not advertise the configured LiveKit authorization service")

    metadata = fetch_json(f"{base}/_matrix/client/v1/auth_metadata", context)
    if metadata.get("issuer", "").rstrip("/") != auth:
        raise ValueError("Matrix OAuth metadata does not advertise the configured MAS issuer")

    mas_discovery = fetch_json(f"{auth}/.well-known/openid-configuration", context)
    if mas_discovery.get("issuer", "").rstrip("/") != auth:
        raise ValueError("MAS discovery does not advertise its configured public issuer")

    keycloak_discovery = fetch_json(f"{issuer}/.well-known/openid-configuration", context)
    if keycloak_discovery.get("issuer", "").rstrip("/") != issuer:
        raise ValueError("Keycloak discovery does not advertise the configured realm issuer")
    supported_scopes = set(keycloak_discovery.get("scopes_supported", []))
    if not {"openid", "profile"}.issubset(supported_scopes):
        raise ValueError("Keycloak OIDC discovery is missing the scopes required by Matrix Authentication Service")

    fetch_page(f"{base}/", context)
    fetch_page(f"{base}/export/", context)
    fetch_page(f"{expected_jwt_url}/healthz", context)
    return ["Matrix client API", "Matrix well-known and LiveKit foci", "Matrix OAuth/MAS metadata", "MAS OIDC discovery", "Keycloak OIDC discovery", "LiveKit authorization service", "Element Web", "export portal"]


def main() -> int:
    env_path = Path(os.environ.get("DEMO_ENV_FILE", ROOT / ".env"))
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    try:
        env = read_env(env_path)
        required = ("MATRIX_PUBLIC_URL", "MATRIX_AUTH_PUBLIC_URL", "KEYCLOAK_ISSUER_URL")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise ValueError("Missing configured URLs: " + ", ".join(missing))
        context = tls_context(env)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Demo health check could not start: {exc}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + int(os.environ.get("DEMO_HEALTH_TIMEOUT_SECONDS", "90"))
    last_error = "endpoints are not ready"
    while time.monotonic() < deadline:
        try:
            checks = validate_endpoints(env, context)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(3)
            continue
        print("Demo endpoint checks passed: " + ", ".join(checks))
        return 0

    print(f"Demo endpoint check did not pass before timeout: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
