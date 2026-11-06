# Operations

This guide describes operational boundaries of the Compose deployment. Keep
target-specific operator tooling and evidence under private `internal/`.
[Public source release](RELEASING.md)

## Demo and production boundaries

The public Compose setup is for evaluating the integration with synthetic
accounts and data. The operator owns the production threat model, identity and
administrator boundaries, data handling, TLS and network isolation, patching,
monitoring, and tested backup and recovery procedures. The single-host network
model demonstrates logical separation. A production DMZ and any required
availability or retention controls need deployment-specific implementation and
evidence. The project is provided as is under its applicable license terms.
[Network zones](../architecture/network-zones.md) · [Security](SECURITY.md) · [License](../../LICENSE)

## Persistent state

Compose stores data in named volumes. The important state includes:

| State | Why it matters |
| --- | --- |
| Keycloak PostgreSQL and Keycloak data | Accounts, realm changes, sessions, and identity-provider state |
| Synapse PostgreSQL and `/data` | Matrix users, rooms, events, media metadata, signing key, and homeserver configuration |
| MAS PostgreSQL and generated MAS configuration | Authentication identities, sessions, signing/encryption keys, and delegated login state |
| OrgBot state directory | Matrix device/crypto store required to receive E2EE keys and decrypt later events |
| Private generated configuration and bootstrap volumes | Service credentials, administrator compatibility token, and the separate OrgBot session |
| Policy and chat-guard volumes | Current room map, expiring authorization snapshots, and direct-room registry; snapshots are regenerated, but registry/marker integrity is security-sensitive |

The OrgBot crypto store is not disposable cache. Losing it can make previously shared E2EE history unreadable to the bot. Keep the administrator-token volume separate from the OrgBot non-admin session volume; do not broaden either mount to other services.

## Routine changes

1. Review source changes, image pins, configuration diffs, upstream advisories, and the security scan for the actual images being released.
2. Back up the databases and persistent crypto/state volumes before changing versions or authentication configuration.
3. Validate generated configuration and the resolved Compose model before applying changes.
4. Apply upgrades in a maintenance window, one dependency layer at a time. Watch health checks and policy-sync output, then verify that existing users, room IDs, encryption state, service-account membership, and access decisions remain consistent.
5. Keep a rollback plan that identifies the matching image versions and data snapshot. Database migrations may make an old image incompatible with newer data; do not assume that rolling back containers alone is safe.

The startup script is an installation/bootstrap workflow with writes and service starts; inspect it before adapting it for routine upgrades. Keycloak realm import is primarily an initial import. Existing realms require the explicit reconciliation operations supported by the repository scripts.

## Preserve deployment overrides

If an installation uses `DEMO_COMPOSE_OVERRIDE` and `DEMO_COMPOSE_OVERRIDE_VALIDATOR`, preserve both settings and their reviewed files through updates and rollback. The [installation guide](INSTALLATION.md#optional-deployment-override) defines the paired paths, supported syntax, validator CLI contract, and pre-preparation rejection behavior. Leave both keys unset for the public default; empty assignments are invalid.

The startup script and reconciler apply the same validated base-plus-override selection. A manual `docker compose` invocation using only `deployment/compose/compose.yaml` bypasses that selection. Before a manual operation, run `python3 scripts/demo_compose.py --require-override`, then include the selected file after the base file with a second `-f` argument. Keep the exact reviewed image references and matching startup commands together in the override so a later normal restart cannot silently revert to a different image.

For topology changes, review all six `COMPOSE_*_SUBNET` settings against current Docker IPAM and host routes before recreation, as described in [Installation](INSTALLATION.md#configure) and [Network zones](../architecture/network-zones.md). The tracked configuration validator does not perform that live inventory. Validate a resolved Compose model with `config --quiet` rather than printing its expanded secrets into logs.

### Deploy prebuilt reviewed images

Set `DEMO_USE_PREBUILT_IMAGES=true` in the private `.env` when deploying a complete set of images that has already been built and reviewed. The startup script checks that every resolved image exists locally before it recreates services, then uses `--no-build --pull never` throughout. A missing image fails the start; it does not trigger a download or rebuild. Use immutable image IDs or reviewed digest references in the deployment override, and retain the corresponding image archives for recovery. With the setting omitted or exactly `false`, the normal source-build workflow remains available. Unsupported values and duplicate settings are rejected before preparation.

This flag controls image acquisition. It does not turn the bootstrap script into a read-only operation: configuration preparation, realm reconciliation and service recreation still occur. Review those changes and make the coordinated backup first.

## Proxy client addresses

When an additional reverse proxy connects through a Docker-published host
socket, Nginx may see a host gateway rather than the outer proxy's container IP.
Inspect the actual connection path before setting `TRUSTED_PROXY_CIDRS`.
Trust only the exact verified peers, and restrict the backend's host-socket and
forwarded access to the approved ingress first. Keep operator CIDRs separate
from these proxy peers; adding a gateway to the operator allowlist is not a
substitute for recovering the original client address.

The renderer uses Nginx's `real_ip_recursive on`: for a trusted peer,
`X-Forwarded-For` selects the last address outside the trusted peer list. See the
[Nginx real-IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html).
Compare a unique request at the outer proxy and backend while sending a forged
loopback header. The backend must log the actual public source. Also check that
the trusted ingress can reach an operator route for an allowed forwarded
address, while an untrusted application peer forging that address receives `403`.
These checks complement authentication and the existing operator CIDRs.

## Existing-realm Matrix admission upgrade

An older realm may have linked Matrix users but no `matrix-access` assignments. Before activating the new MAS-only login gate, deliberately choose which identities retain Matrix access. For the 22 built-in seed accounts, the explicit maintenance command is:

```sh
KEYCLOAK_ADMIN_USE_DOCKER=1 python3 scripts/reconcile-demo-keycloak-users.py --grant-matrix-access-existing-demo-users
```

Run it only on the chosen installation host with its private `.env` and Docker access. It verifies the exact public seed identities and grants the role before binding the client flow. Additional synthetic users require their own explicit `manage-demo-user.py matrix-access --grant` decisions. This is an opt-in migration, not a routine startup step: repeating it would regrant access to seed users whose direct role was intentionally removed. Normal startup does not request this migration flag and must preserve withdrawals.

## Secrets and credentials

- Keep `.env`, generated MAS keys/configuration, Keycloak client secrets, Synapse admin compatibility tokens, bot session JSON, LiveKit credentials, and private TLS keys out of Git and logs.
- Use distinct, randomly generated secrets for each service and environment. Restrict file access and secret mounts to the services that need them.
- The provisioner-issued Synapse administrator compatibility token has no expiry in the pinned MAS version. Protect its storage and rotate/revoke it deliberately; do not describe it as short-lived.
- OrgBot uses a separate non-admin Matrix session and persistent E2EE device store. Replacing the session or device without a controlled recovery can interrupt sync or lose access to keys.
- Rotate credentials by following each component's documented procedure and verifying dependent services after the change. Replacing an encryption/signing key with a random value is not a safe generic rotation procedure.

## Backups and recovery

The public operator commands do not create a consistent backup of all databases, files, secrets, and crypto state. A real backup plan must coordinate database-native dumps with copies of persistent Synapse, MAS, Keycloak, and OrgBot state, preserve ownership and restrictive permissions, and protect encryption keys separately. A volume copy taken while a database or crypto store is changing may not be consistent.

Before restoring:

1. Restore into an isolated environment with matching image versions and configuration.
2. Confirm database integrity, Matrix signing identity, MAS keys, Keycloak issuer/realm, and OrgBot device identity.
3. Verify the restored room map, encryption state, bot key publication, and policy snapshots before opening user access.
4. Test login, policy denial, export authorization, and a new encrypted event before returning the environment to service.

These are validation requirements, not a claim that every recovery path has been tested. Export ZIPs are partial evidence packages and are not a substitute for database backups or E2EE key backups.

## Destructive reset

`scripts/reset-demo.sh` requires an explicit confirmation value and runs Compose down with volume removal. It deletes persistent service data, including user, room, event, and crypto state. Do not use it for an upgrade, routine restart, or recovery attempt. Preserve required backups and verify the target Compose project name before any reset.

## Health and monitoring

`scripts/check-demo-health.py` checks selected service endpoints. A green HTTP health check does not prove role reconciliation, room-key delivery, complete export history, or call-media reachability. Monitor service health, policy snapshot freshness, disk capacity, certificate expiry, failed authorization attempts, database health, and backup completion separately.

## Related guides

- [Installation](INSTALLATION.md)
- [Security](SECURITY.md)
- [Export limitations](EXPORT.md)
