# Installation

This guide installs the public Compose demonstration using synthetic accounts and data. Set your own hostname and generate fresh secrets before first use. Production deployments have separate operational requirements. [Security](SECURITY.md) · [Operational scope](OPERATIONS.md#demo-and-production-boundaries)

## Prerequisites

- A Linux `amd64` host with Docker Engine, Docker Compose 2.40.3 or newer, Python 3.12 or newer and OpenSSL. The Compose version must support `up --wait`, service build contexts, and normalizing aliases shared by the web, Matrix and authentication hostnames. Preparation builds the patched MAS runtime and uses its CLI to generate keys.
- Network access to fetch pinned build inputs, plus sufficient memory and disk space for the Keycloak and Element Web builds. [Keycloak build](../../deployment/keycloak/README.md) · [Element Web build](../../deployment/element/README.md)
- Keep the normal Git checkout permissions on the mounted source directories `src/synapse-policy/` and `web/export-portal/`. Synapse and Nginx need read and directory traversal access to these files. Generated configuration and credentials have separate private permissions.
- A stable Matrix server name and public HTTPS hostname. Changing the Matrix server name after users or rooms exist is a migration, not a normal configuration edit.
- DNS and a TLS certificate for the public hostname. Use a trusted certificate for any non-local deployment.
- A client-reachable LiveKit node address and a host firewall that permits the published web and explicitly configured call-media ports.

## Configure

1. Run `python3 scripts/prepare-demo.py` from the repository root. It creates `.env`, generates unique service and demo-user credentials, builds the Keycloak realm import, generates MAS keys and runtime configuration, and prepares local development TLS files. It does not start the application stack.
2. Edit `.env`: set the reachable `DEMO_HOSTNAME`, permanent `MATRIX_SERVER_NAME`, client-reachable `LIVEKIT_NODE_IP`, and the exact `ADMIN_ALLOWED_CIDRS`. The hostname, realm issuer, public URLs, and TLS inputs must agree. Do not leave example CIDRs or `.invalid` hostnames in a reachable deployment.
3. Review all six subnet settings before starting: `COMPOSE_EDGE_SUBNET`, `COMPOSE_APPLICATION_SUBNET`, `COMPOSE_CONTROL_SUBNET`, `COMPOSE_KEYCLOAK_DATA_SUBNET`, `COMPOSE_SYNAPSE_DATA_SUBNET`, and `COMPOSE_MAS_DATA_SUBNET`. Choose mutually disjoint ranges that do not overlap existing Docker networks or host routes. Inspect existing network IPAM with `docker network inspect NETWORK_NAME --format '{{json .IPAM.Config}}'` and Linux routes with `ip -4 route show table all`; replace `NETWORK_NAME` with each relevant network from `docker network ls`. The legacy `COMPOSE_NETWORK_SUBNET` setting does not select the six new bridges. See [Network zones](../architecture/network-zones.md).
4. For an external deployment, set `TLS_MODE=provided` and point `TLS_CERT_FILE` and `TLS_KEY_FILE` to the trusted certificate chain and protected private key. The generated self-signed CA is only for isolated development.
5. Review `config/branding/brand.json`, `config/policies/room-access.json`, and the generated realm import. Do not edit generated files as the durable source of configuration.

## Optional deployment override

The public default installation needs no override or private helper: leave both `DEMO_COMPOSE_OVERRIDE` and `DEMO_COMPOSE_OVERRIDE_VALIDATOR` unset. Do not add empty assignments for them.

An operator can create a deployment-specific Compose override and a trusted local Python validator, then configure their paths in `.env`, for example:

```dotenv
DEMO_COMPOSE_OVERRIDE=deployment/local/compose.override.yaml
DEMO_COMPOSE_OVERRIDE_VALIDATOR=deployment/local/validate-override.py
```

Both files must already exist at normalized project-relative paths, without parent-directory components (`..`), absolute paths, or symlinks. For these two settings, plain nonempty paths, spaces around `=`, and an optional `export` prefix are supported. Quoted keys or values, interpolation, inline comments, bare/colon declarations, a key prefixed with a byte-order mark, empty values, and duplicate keys after normalization are rejected.

The helper runs the selected validator with the current Python interpreter and `-B`, from the project root, passing `--project-root ABSOLUTE_PROJECT_ROOT --pin ABSOLUTE_OVERRIDE_PATH`. The validator is operator-chosen local code: review it and return exit status `0` only when the complete override satisfies the intended deployment policy. A nonzero status rejects it; the hook does not supply an approval policy itself.

`scripts/start-demo.sh` checks the pair before preparation or container changes and uses the validated override after the base Compose file for every Compose invocation. A missing configured file, incomplete pair, invalid declaration, or rejected override aborts startup. The reconciler uses the same selection. Run `python3 scripts/demo_compose.py` for that preflight directly, or add `--require-override` when an override is mandatory for your deployment.

## Start

Run `python3 scripts/validate-demo-config.py`, then `scripts/start-demo.sh` from the repository root for a first installation. Startup builds required images, starts Keycloak for explicit user/configuration reconciliation, starts the remaining Compose services, and runs health checks. Review the script before adapting it to an unattended deployment; it performs writes and service starts.

The default Compose file builds the patched Keycloak 26.8.1 derivative from the
public source. PostgreSQL configuration and the `/auth` path are built into
its optimized runtime. For existing environments, preserve the databases and
review version and image changes before using the startup command.
[Keycloak build](../../deployment/keycloak/README.md) · [Operations](OPERATIONS.md)

`validate-demo-config.py` checks the tracked seed, policy, and branding inputs. It does not inspect existing Docker IPAM or host-route conflicts; complete the subnet review above before starting.

Startup generates the local credential handoff in `internal/deploy/local/demo-credentials.txt` and `internal/deploy/local/generated/handoff/credential-manifest.json`. These private, owner-only files are created locally and excluded from source control. Open the `MATRIX_PUBLIC_URL` from that handoff and use the Keycloak sign-in flow. If `DEMO_TARGET` or `DEMO_GENERATED_DIR` changes, the handoff path changes accordingly.

For local use, use a separate Compose project and non-production domains. Set
`DEMO_BIND_HOST=127.0.0.1` and `LIVEKIT_BIND_HOST=127.0.0.1` to restrict the
published web and media sockets to the host. Use an advertised LiveKit address
reachable by the test clients. Databases and private application APIs stay on
the Compose networks. [Calls](CALLS.md)

## Verify and continue

Run `scripts/check-demo-health.py` after startup. It checks selected HTTP endpoints; it does not prove all policy, export, call-media, backup, or recovery properties. Follow the [Administration](ADMINISTRATION.md), [Operations](OPERATIONS.md), [Native clients](NATIVE-CLIENTS.md), and [Calls](CALLS.md) guides for those areas.

Never run `scripts/reset-demo.sh` as an upgrade step. It removes Compose volumes and data; see [Operations](OPERATIONS.md).
