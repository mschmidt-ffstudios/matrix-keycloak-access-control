# Administration

This guide separates four decisions that are easy to conflate: whether a person can sign in to Matrix, which rooms they may enter, what they may do in each room, and whether they may export data. Keycloak is the source of these assignments; Policy Sync applies room access in Synapse.

The room rules are defined in `config/policies/room-access.json` and interpreted by this project. Keycloak stores role/group assignments and the application login flow; its Authorization Services policy editor is not the decision engine for Matrix room requests.

## Access model

| Control | Keycloak source | Effect |
| --- | --- | --- |
| Matrix sign-in | Realm role `matrix-access` | MAS browser sign-in is denied when the role is absent. The Keycloak account remains enabled. The public demo seed explicitly grants this role to its test users. |
| Room admission | `matrix-access`, `security_clearance` attribute, and one explicit `matrix-synapse` client role per room | An enabled user must meet the clearance threshold and hold exactly one valid room role. |
| Room actions | Room role mapped to Matrix power level | Matrix enforces the resulting membership and event power levels on the server. |
| Export | `matrix-access` plus realm role `org-export-admin` | Authorizes privileged export within the current scope, including rooms the requester cannot join. The grant affects exports only. |
| Direct-chat eligibility | Matrix-enabled users with valid clearance and an exact shared Keycloak group ID in the configured `1to1chat.` namespace | Enables a pair to request a direct room. |

The Matrix login entitlement is distinct from room roles. A user can be enabled in Keycloak but unable to sign in to Matrix. Removing a room role removes that room access after policy reconciliation; removing `matrix-access` also locks the MAS identity and ends its Matrix sessions through the configured provisioner path. Already delivered messages, plaintext, and E2EE keys cannot be recalled.

Policy Sync manages the demo's synthetic account namespace. It accepts ordinary `demo-*` accounts and numbered `export-test-XX` fixtures that pass the clearance check. Built-in administrative and service identities are handled separately.

The generator's realm and issuer default is `foldfence-demo`, and the management CLI defaults to that realm. Its default Keycloak base URL is the Matrix origin plus `/auth`, for example `https://matrix.example.invalid/auth`. Changing the realm name requires coordinated changes to the generator, realm import, issuer, client configuration, reconciliation scripts, and operator tools; changing only the CLI flag is insufficient.

## Room catalog and roles

Use these exact clearance values in Keycloak and configuration. The English labels explain the demo values.

| Clearance value | English label | Rank |
| --- | --- | ---: |
| `basis` | Basic | 0 |
| `intern` | Internal | 1 |
| `vertraulich` | Confidential | 2 |
| `streng_vertraulich` | Strictly confidential | 3 |

The checked-in policy currently defines these ten managed rooms:

| Room ID | Minimum clearance | Exact Keycloak client roles |
| --- | --- | --- |
| `allgemein` | `basis` | `room.allgemein.reader`, `room.allgemein.writer`, `room.allgemein.moderator` |
| `pummel` | `intern` | `room.pummel.reader`, `room.pummel.writer`, `room.pummel.moderator` |
| `speicher` | `vertraulich` | `room.speicher.reader`, `room.speicher.writer`, `room.speicher.moderator` |
| `leitung` | `streng_vertraulich` | `room.leitung.reader`, `room.leitung.writer`, `room.leitung.moderator` |
| `abteilung_x` | `basis` | `room.abteilung_x.reader`, `room.abteilung_x.writer`, `room.abteilung_x.moderator` |
| `abteilung_y` | `basis` | `room.abteilung_y.reader`, `room.abteilung_y.writer`, `room.abteilung_y.moderator` |
| `export_test_allgemein` | `basis` | `room.export_test_allgemein.reader`, `room.export_test_allgemein.writer`, `room.export_test_allgemein.moderator` (test-only) |
| `export_test_pummel` | `basis` | `room.export_test_pummel.reader`, `room.export_test_pummel.writer`, `room.export_test_pummel.moderator` (test-only) |
| `export_test_speicher` | `basis` | `room.export_test_speicher.reader`, `room.export_test_speicher.writer`, `room.export_test_speicher.moderator` (test-only) |
| `export_test_leitung` | `basis` | `room.export_test_leitung.reader`, `room.export_test_leitung.writer`, `room.export_test_leitung.moderator` (test-only) |

Every room has three possible client roles, named `room.<room-id>.<role>` on the Keycloak client `matrix-synapse`:

| Role | Matrix power level | Capability |
| --- | ---: | --- |
| `reader` | 0 | Read the room and receive events; cannot send ordinary messages |
| `writer` | 10 | Send messages, encrypted events, reactions, and call-membership events |
| `moderator` | 50 | Writer actions plus redact messages; does not meet room-management thresholds |

The role is explicit per room; it is not inherited from clearance or from another room. For example, a `vertraulich` user with `room.speicher.reader` may read Speicher, while the same user's lack of a Pummel role means no Pummel access. The clearance label is an admission threshold, not a different encryption algorithm or cryptographic strength.

The policy sets invite, kick, ban, and state-event thresholds to 100. Demo user roles do not grant those management actions. Managed rooms are invite-only and encrypted with Megolm; history visibility is `joined`.

## Create and change a demo user

The CLI manages synthetic `demo-*` accounts and assigns roles for rooms outside the `test_only` fixture set. Its authentication realm defaults to the selected realm. Set `KEYCLOAK_ADMIN_USERNAME` and `KEYCLOAK_ADMIN_PASSWORD` privately to a delegated realm administrator, or, when intentionally using the generated bootstrap owner credentials from `.env`, pass `--auth-realm master` from the permitted management network. Do not send master credentials to the demo realm. Replace example URLs with `KEYCLOAK_PUBLIC_URL`, including a non-standard port when configured. The [Postman guide](../../api/postman/README.md) documents the equivalent credential setup.

Use the supported management CLI with an owner-approved Keycloak Admin REST account. Keep administrator credentials in the configured private environment; do not place them in shell history or paste them into Postman requests.

~~~sh
python3 scripts/manage-demo-user.py \
  --base-url https://matrix.example.invalid/auth \
  --realm foldfence-demo create \
  --username demo-alex \
  --display-name 'Alex Example' \
  --clearance intern \
  --role room.pummel.writer \
  --matrix-access
~~~

The `--matrix-access` flag grants the login role to a new or existing account. Without the flag, `create` leaves an existing grant as it was and creates new users without the entitlement. Use `matrix-access --revoke` to withdraw it. `create` also accepts repeated `--role` arguments. Policy Sync links the Matrix identity to the Keycloak subject and applies effective room grants.

When the project `.env` is configured, the CLI can read its Keycloak URL and administrator login settings. Other supported operations include:

~~~sh
python3 scripts/manage-demo-user.py --realm foldfence-demo update --username demo-alex --clearance vertraulich
python3 scripts/manage-demo-user.py --realm foldfence-demo roles --username demo-alex --add room.speicher.reader
python3 scripts/manage-demo-user.py --realm foldfence-demo matrix-access --username demo-alex --revoke
python3 scripts/manage-demo-user.py --realm foldfence-demo group list
~~~

Use `password` to set a password through the hidden prompt. Room roles can be added or removed individually. `matrix-access --revoke` removes the direct role; the running policy/provisioner path must complete the MAS lock and session revocation before treating sign-in as withdrawn. Keycloak account enablement remains a separate administrative control.

## Direct-chat groups

Direct-chat eligibility uses direct group memberships returned by Keycloak. The group name must begin with `1to1chat.` and both people must share the same exact Keycloak group ID. No nested-group inference is made. A group such as `department.sales` or `organization.example` never grants direct-chat permission. The Matrix server checks the current snapshot, membership, room marker, encryption state, and bot readiness; a client-side `m.direct` label is not authorization.

Add or remove a person by the exact group UUID shown by `group list`:

```sh
python3 scripts/manage-demo-user.py --realm foldfence-demo group add-user --group-id '<group-uuid>' --username demo-alex
python3 scripts/manage-demo-user.py --realm foldfence-demo group remove-user --group-id '<group-uuid>' --username demo-alex
```

Only use groups whose names are in the configured direct-chat namespace. Membership changes take effect after the next successful policy refresh; stale or unavailable policy data fails closed for new protected actions. A confirmed revocation removes the room grant, but cannot erase content or keys already received by devices.

## Keycloak locations

- Human user attributes: in the `foldfence-demo` Admin Console, open **Users → selected user → Attributes** and set `security_clearance` to one of the four allowed values.
- Human login/export roles: open **Users → selected user → Role mapping → Assign role**, choose the realm-role filter, and select `matrix-access` and/or `org-export-admin`. The latter grants scoped export only.
- Per-room grants: on the same Role mapping screen, choose the client-role filter and client `matrix-synapse`; assign only the exact `room.<room-id>.<reader|writer|moderator>` roles listed above.
- Direct-chat groups: use **Groups** to create or inspect a top-level group with the configured `1to1chat.` prefix, then manage the user's group membership. Pair authorization uses the exact group ID, not its display name alone.
- Delegated demo administrators: open **Users → selected admin → Role mapping → Assign role**, choose the client-role filter and client `realm-management` to view or assign the delegated roles. These two interactive Keycloak-only identities have the following seven roles:

| Role | Effect in this realm |
| --- | --- |
| `manage-users` | View and manage realm users, map roles, and manage group membership; also grants management of realm groups. It does not grant client or realm configuration management. |
| `view-users` | View realm users and groups; does not grant edits. |
| `query-users` | Find/search users in the Admin Console; by itself it does not permit viewing user details. |
| `query-groups` | Find/search groups in the Admin Console; by itself it does not permit viewing group details. |
| `view-realm` | View realm settings and metadata; does not grant realm changes. |
| `view-clients` | View client details; does not grant client changes. |
| `query-clients` | Find/search clients in the Admin Console; by itself it does not permit viewing client details. |

- Policy Sync service account: open **Clients → `matrix-policy-sync` → Service account roles → Assign role**, choose client `realm-management`, and inspect the three assigned client roles: `query-users` to find users, `view-users` to read user records and their group memberships, and `view-clients` to inspect client metadata needed for role mapping. It is noninteractive and is not a human Matrix account.

The Keycloak bootstrap/owner administrator is a separate platform identity. Human Matrix users are linked to Keycloak subjects by MAS. The two seeded demo administrator identities have no Matrix user ID, clearance, room roles, groups, or `matrix-access`. Their `manage-users` role can still change users' credentials, group membership, and application role assignments across the demo realm. Treat these accounts as privileged realm operators for synthetic data. OrgBot and the Matrix policy administrator are noninteractive Matrix service identities. OrgBot has read access for export and a separate device session.

For the semantics of Keycloak's built-in realm-management roles, see the [Keycloak 26.8.1 Server Administration Guide](https://www.keycloak.org/docs/26.8.1/server_admin/). The role list above reflects the project generator's current assignment.

For existing realms, importing a realm JSON does not update an already imported realm. Review and run the explicit reconcile step for supported client scopes and templates. Do not use an ordinary startup reconciliation to silently regrant a manually withdrawn Matrix entitlement.

## See also

- [Installation](INSTALLATION.md)
- [Operations and recovery boundaries](OPERATIONS.md)
- [Room-policy example](../examples/room-policy.md)
- [Group-based direct-chat example](../examples/group-based-direct-chat-policy.md)
