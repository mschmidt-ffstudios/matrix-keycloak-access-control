#!/usr/bin/env python3
"""Generic optional Compose override hook; no Docker operations are performed."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

class ComposeConfigurationError(ValueError):
    pass

OVERRIDE_KEYS = {"DEMO_COMPOSE_OVERRIDE", "DEMO_COMPOSE_OVERRIDE_VALIDATOR", "DEMO_USE_PREBUILT_IMAGES"}

def require(condition, message):
    if not condition:
        raise ComposeConfigurationError(message)

def override_declaration(line: str):
    declaration = line.strip()
    had_bom = declaration.startswith("\ufeff")
    declaration = declaration.lstrip("\ufeff").strip()
    if not declaration or declaration.startswith("#"):
        return None
    declaration = re.sub(r"^export\s+", "", declaration, count=1)
    token = re.match(r"(?P<quote>['\"]?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)", declaration)
    if token is None or token.group("key") not in OVERRIDE_KEYS:
        return None
    key = token.group("key")
    require(not had_bom and not token.group("quote"),
            "BOM-prefixed or quoted Compose override keys are unsupported")
    assignment = re.fullmatch(r"\s*=\s*([A-Za-z0-9._/-]+)\s*", declaration[token.end():])
    require(assignment is not None,
            "Known Compose override keys require a plain, nonempty project-relative path assignment")
    return key, assignment.group(1)


def environment_values(text: str):
    values = {}
    for line in text.splitlines():
        declaration = override_declaration(line)
        if declaration is None:
            continue
        key, value = declaration
        require(key not in values, "Duplicate Compose override setting")
        values[key] = value
    return values


def configured_file(root: Path, value: str) -> Path:
    relative = Path(value)
    if (not value or relative.is_absolute() or ".." in relative.parts
            or str(relative) != value):
        raise ComposeConfigurationError("Compose override paths must be normalized project-relative paths")
    path = root / relative
    current = path
    while current != root:
        if current.is_symlink():
            raise ComposeConfigurationError("Compose override paths must not contain symlinks")
        current = current.parent
    if not path.is_file():
        raise ComposeConfigurationError("Configured Compose override or validator is missing")
    return path

def compose_file_arguments(root: Path, env_file: Path = Path(".env"), *, require_override: bool = False) -> list[str]:
    root = root.resolve()
    environment = env_file if env_file.is_absolute() else root / env_file
    values = environment_values(environment.read_text(encoding="utf-8")) if environment.exists() else {}
    override = values.get("DEMO_COMPOSE_OVERRIDE", "")
    validator = values.get("DEMO_COMPOSE_OVERRIDE_VALIDATOR", "")
    arguments = ["-f", "deployment/compose/compose.yaml"]
    if not (override or validator) and not require_override:
        return arguments
    if not override or not validator:
        raise ComposeConfigurationError("Compose override and validator must both be configured")
    override_file = configured_file(root, override)
    validator_file = configured_file(root, validator)
    result = subprocess.run([sys.executable, "-B", str(validator_file), "--project-root", str(root),
                             "--pin", str(override_file)], cwd=root, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        raise ComposeConfigurationError("Configured Compose override validator rejected the active pin")
    return [*arguments, "-f", str(override_file)]


def image_mode(root: Path, env_file: Path = Path(".env")) -> str:
    environment = env_file if env_file.is_absolute() else root / env_file
    values = environment_values(environment.read_text(encoding="utf-8")) if environment.exists() else {}
    value = values.get("DEMO_USE_PREBUILT_IMAGES", "false")
    require(value in {"true", "false"}, "DEMO_USE_PREBUILT_IMAGES must be exactly true or false")
    return "prebuilt" if value == "true" else "build"


def resolved_service_image(root: Path, env_file: Path, service: str, docker: list[str]) -> str:
    files = compose_file_arguments(root, env_file)
    result = subprocess.run([*docker, "compose", "--env-file", str(env_file), *files,
                             "config", "--format", "json"], cwd=root,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=30, check=False)
    require(result.returncode == 0, "Could not resolve prebuilt service image; private output omitted")
    try:
        image = json.loads(result.stdout)["services"][service]["image"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ComposeConfigurationError("Resolved prebuilt service image is missing") from exc
    require(isinstance(image, str) and image and not any(ch.isspace() for ch in image),
            "Resolved prebuilt service image is invalid")
    return image

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-override", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--image-mode", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        mode = image_mode(root, args.env_file)
        flags = compose_file_arguments(root, args.env_file,
                                       require_override=args.require_override)
        if args.image_mode:
            print(mode)
        elif len(flags) > 2:
            print(flags[-1])
        return 0
    except (ComposeConfigurationError, OSError, UnicodeError) as exc:
        print(f"Compose configuration rejected before preparation: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
