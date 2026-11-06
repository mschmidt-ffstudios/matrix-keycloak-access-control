#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

read -r -a DOCKER_COMMAND_ARGS <<< "${DOCKER_COMMAND:-docker}"
if [[ "${#DOCKER_COMMAND_ARGS[@]}" -eq 0 ]]; then
  printf '%s\n' 'DOCKER_COMMAND must name an available Docker CLI command.' >&2
  exit 2
fi

# Validate an optional deployment-specific override before preparation changes files.
# The ordinary public installation needs no override.
COMPOSE_OVERRIDE="$(python3 scripts/demo_compose.py)"
IMAGE_MODE="$(python3 scripts/demo_compose.py --image-mode)"
COMPOSE_FILE_ARGS=(-f deployment/compose/compose.yaml)
if [[ -n "$COMPOSE_OVERRIDE" ]]; then COMPOSE_FILE_ARGS+=(-f "$COMPOSE_OVERRIDE"); fi

python3 scripts/prepare-demo.py
python3 - <<'PY'
import os
from pathlib import Path

env_path = Path('.env')
lines = env_path.read_text(encoding='utf-8').splitlines()
values = {}
for line in lines:
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    values[key] = value

generated = Path('deployment/compose') / values.get('DEMO_GENERATED_DIR', '../../internal/generated')
generated = generated.resolve()
gid = str(os.stat(generated / 'mas' / 'config.yaml').st_gid)
updated = []
found = False
for line in lines:
    if line.startswith('DEMO_CONFIG_GID='):
        updated.append(f'DEMO_CONFIG_GID={gid}')
        found = True
    else:
        updated.append(line)
if not found:
    updated.append(f'DEMO_CONFIG_GID={gid}')
env_path.write_text('\n'.join(updated) + '\n', encoding='utf-8')
env_path.chmod(0o600)
print(gid)
PY
COMPOSE_PROJECT_NAME="$(python3 - <<'PY'
from pathlib import Path
import re

values = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key] = value
name = values.get('COMPOSE_PROJECT_NAME', 'matrix-keycloak-demo')
if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
    raise SystemExit('COMPOSE_PROJECT_NAME must contain only lowercase letters, digits, underscores, or hyphens.')
print(name)
PY
)"
python3 - <<'PY'
from pathlib import Path

values = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    values[key] = value

invalid = (
    not values.get('DEMO_HOSTNAME')
    or values['DEMO_HOSTNAME'].endswith('.invalid')
    or values.get('MATRIX_SERVER_NAME') in ('', 'demo.example.invalid')
    or values.get('LIVEKIT_NODE_IP') in ('', '192.0.2.10', 'CHANGE_ME')
)
if invalid:
    raise SystemExit('Edit .env first: set the demo DNS name/IP and the reachable LiveKit node address.')
PY

COMPOSE_COMMAND=("${DOCKER_COMMAND_ARGS[@]}" compose --project-name "$COMPOSE_PROJECT_NAME" --env-file .env "${COMPOSE_FILE_ARGS[@]}")
IMAGE_FLAGS=(--no-build)
if [[ "$IMAGE_MODE" == prebuilt ]]; then
  IMAGE_FLAGS=(--no-build --pull never)
  # Fail before recreation if any accepted local image is absent. No registry
  # lookup, build, pull or tag replacement occurs in this deployment mode.
  image_list="$("${COMPOSE_COMMAND[@]}" config --images)"
  [[ -n "$image_list" ]] || { printf 'No configured prebuilt images.\n' >&2; exit 2; }
  while IFS= read -r image; do
    "${DOCKER_COMMAND_ARGS[@]}" image inspect "$image" >/dev/null
  done <<< "$image_list"
else
  # Build the local image set once before starting anything. The renderer
  # shares Synapse's image and has no build stanza of its own.
  "${COMPOSE_COMMAND[@]}" build
fi

"${COMPOSE_COMMAND[@]}" up -d "${IMAGE_FLAGS[@]}" --force-recreate synapse-config
# Generated service configs and branding files are bind-mounted. Compose does not
# detect edits to their contents, so recreate services that load them at startup.
# Configure the MAS-only entitlement flow before starting any browser-facing
# Matrix services. This keeps first boot closed while Keycloak imports/reconciles.
"${COMPOSE_COMMAND[@]}" up -d "${IMAGE_FLAGS[@]}" --force-recreate --wait --wait-timeout 900 keycloak
KEYCLOAK_ADMIN_USE_DOCKER=1 python3 scripts/reconcile-demo-keycloak-users.py
# Recreate Nginx too so it resolves the new container addresses for its upstreams.
"${COMPOSE_COMMAND[@]}" up -d "${IMAGE_FLAGS[@]}" --force-recreate --wait --wait-timeout 900 mas synapse element-web nginx
"${COMPOSE_COMMAND[@]}" up -d "${IMAGE_FLAGS[@]}" --wait --wait-timeout 900
"${COMPOSE_COMMAND[@]}" up -d "${IMAGE_FLAGS[@]}" --force-recreate --wait --wait-timeout 900 policy-sync
python3 scripts/check-demo-health.py
DEMO_PUBLIC_URL="$(python3 - <<'PY'
from pathlib import Path

for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if line.startswith('MATRIX_PUBLIC_URL='):
        print(line.split('=', 1)[1])
        break
else:
    raise SystemExit('MATRIX_PUBLIC_URL is missing from .env')
PY
)"
printf 'Demo services are ready at %s\n' "$DEMO_PUBLIC_URL"
