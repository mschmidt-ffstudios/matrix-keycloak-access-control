#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
read -r -a DOCKER_COMMAND_ARGS <<< "${DOCKER_COMMAND:-docker}"
if [[ "${#DOCKER_COMMAND_ARGS[@]}" -eq 0 ]]; then
  printf '%s\n' 'DOCKER_COMMAND must name an available Docker CLI command.' >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  printf '%s\n' 'No .env exists. Run scripts/prepare-demo.py first.' >&2
  exit 2
fi

project_name="$(python3 - <<'PY'
from pathlib import Path
values = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    values[key] = value
print(values.get('COMPOSE_PROJECT_NAME', 'matrix-keycloak-demo'))
PY
)"

printf 'This deletes only the %s Compose volumes, including demo users, rooms and messages. Type RESET to continue: ' "$project_name"
read -r confirmation
if [[ "$confirmation" != RESET ]]; then
  printf '%s\n' 'Reset cancelled.'
  exit 0
fi

"${DOCKER_COMMAND_ARGS[@]}" compose --project-name "$project_name" --env-file .env -f deployment/compose/compose.yaml down --volumes --remove-orphans
