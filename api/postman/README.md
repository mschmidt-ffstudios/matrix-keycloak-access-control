# Postman user, room-role, and direct-chat group management

## Matrix application entitlement

This workflow manages the separate Matrix application entitlement. Install the
Keycloak client flow and MAS/policy services together, then verify their
login/session effects on the selected deployment.

Creating a user does **not** grant Matrix access. The separate
`matrix-access` realm role is opt-in and independent of room roles, clearance
and direct-chat groups. Under **Matrix application access**, run **Find demo
user**, **Find Matrix access role**, then **Grant Matrix access**. The stored
username and exact user UUID must still match; known administrators and service
identities are excluded. The collection cannot select an arbitrary realm role.

**Revoke Matrix access** removes that role and checks that no effective or
inherited grant remains. It deliberately does not log out Keycloak or disable
the user. Policy-sync must converge: the private provisioner locks the MAS
account and kills its Matrix sessions. Verify subsequent Matrix access and
refresh denial before treating revocation as complete. Other Keycloak
applications and their SSO sessions stay independent.

The CLI exposes the same explicit operations:

```sh
python3 scripts/manage-demo-user.py create --username demo-new --display-name New --clearance basis --matrix-access
python3 scripts/manage-demo-user.py matrix-access --username demo-new --grant
python3 scripts/manage-demo-user.py matrix-access --username demo-new --revoke
```

Without `--matrix-access`, a new CLI-created user has no Matrix entitlement. Reconciliation
of an existing user preserves its access unless an explicit grant/revoke is
requested. Entitlement revocation does not substitute for room-specific grant
management, and inherited access must be removed at its group/composite source.

Import `matrix-keycloak-demo.postman_collection.json` and `environment.example.json`. Set the active environment's Keycloak URL and admin password locally; enter a demo-user password only when creating or resetting a user. The committed environment is a template and contains no credentials.

For first use, authenticate, find the `matrix-synapse` role-container client, create the demo user, then find the user. The remaining requests update the display name and clearance, set the password, find a room role, and add or remove that role. For role assignment, set `role_name` to `room.<room>.<reader|writer|moderator>` and run “Find room role” before “Add” or “Remove room role.” The public collection and CLI support six ordinary rooms: `allgemein`, `pummel`, `speicher`, `leitung`, `abteilung_x`, and `abteilung_y`. Isolated export-fixture room roles are excluded from ordinary user management.

These requests use Keycloak's `admin-cli` password grant for the demo administrator. Keep the environment local and do not commit saved Postman environments or exported tokens. Use only synthetic accounts and data.

Authentication uses the configured demo `realm`, with `demo-admin-01` as the
delegated default. Create/update requests split the supplied display name into
profile fields and use the synthetic surname `Demo` for a single-word name;
they also populate the synthetic email required by the normal Keycloak profile.
The CLI uses the same surname fallback. Existing Matrix display names are not
changed merely by repairing missing Keycloak profile metadata.

## CLI alternative

The CLI reads the local `.env` as plain key/value data without executing it. It accepts `KEYCLOAK_BASE_URL`, `KEYCLOAK_ADMIN_USERNAME`, and `KEYCLOAK_ADMIN_PASSWORD` from the process environment; if a prepared project `.env` is present, it uses the private Keycloak bootstrap credentials and public URL as defaults. It never prints credentials or access tokens.

CLI authentication defaults to the selected demo realm, matching delegated
`demo-admin-01` credentials. If intentionally using the private owner credentials
from `.env`, pass `--auth-realm master` explicitly and use the approved
management route; the public master-realm restriction remains in place.

```sh
python3 scripts/manage-demo-user.py create \
  --username demo-21 \
  --display-name "Demo User 21" \
  --clearance intern \
  --role room.allgemein.writer \
  --role room.pummel.reader

python3 scripts/manage-demo-user.py update \
  --username demo-21 \
  --display-name "Demo User 21 — Pummel" \
  --clearance vertraulich

python3 scripts/manage-demo-user.py roles \
  --username demo-21 \
  --add room.speicher.reader \
  --remove room.pummel.reader

python3 scripts/manage-demo-user.py password --username demo-21
```

New CLI users use the shared synthetic demo password from `.env` when available. If it is absent, the script prompts without echoing input. Re-running `create` updates the name/clearance and ensures the requested roles without resetting an existing password. Role reconciliation touches only the room roles defined in `config/policies/room-access.json`.

Keycloak is the role source of truth. The Matrix policy synchronizer applies room membership and power levels on its configured interval, so revocation is not instantaneous. Verify the resulting Matrix access after synchronization; do not treat the Keycloak edit alone as proof of enforcement.

## Direct-chat policy groups

The separate `group` commands manage only top-level policy groups whose name starts with the configured `direct_chats.group_prefix` in `config/policies/room-access.json` (`1to1chat.` in this demo). A non-empty scope is required. Ordinary department groups cannot be managed through these commands and do not grant direct-chat permission.

```sh
python3 scripts/manage-demo-user.py group list
python3 scripts/manage-demo-user.py group create --name 1to1chat.orga-a.abteilung-x
python3 scripts/manage-demo-user.py group add-user --username demo-21 --group-id GROUP_UUID_FROM_LIST
python3 scripts/manage-demo-user.py group remove-user --username demo-21 --group-id GROUP_UUID_FROM_LIST
```

The membership operations use the exact Keycloak group ID from `group list`; the group name is not accepted as a substitute. These operations use Keycloak's group membership endpoints and leave the user's room-role mappings unchanged. Listing reads all pages. The optional `group bootstrap-seeded` command explicitly creates the groups in `keycloak_groups` and adds the paths in each seed user's `groups` list. It is additive, is not called at startup, and does not remove manual memberships.

The Postman collection includes list, create, add-user, and remove-user requests. Keep the environment's `policy_group_prefix` aligned with `direct_chats.group_prefix` in the policy JSON. The list request accepts `group_first` and `group_page_size`; repeat it with the next offset when a page is full. Its variables identify the selected policy group by name and exact UUID.

In the implementation, Keycloak manages the group data and the policy snapshot feeds the Synapse guard. The synchronizer checks every 20 seconds by default; direct-chat policy snapshots expire within 60 seconds, and the guard denies on missing or stale data. This repository behavior still requires deployment and live API acceptance on the target before claiming it is active there.
