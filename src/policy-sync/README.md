# Matrix room policy synchronizer

This worker treats Keycloak as the source of truth for 20 regular seeded demo
users, two isolated `export-test-*` accounts, and additional synthetic `demo-*`
accounts created through the Keycloak Admin API.
It excludes service accounts and ignores accounts without exactly one known
`security_clearance` value. For managed accounts, it reads the attribute and the
`matrix-synapse` client roles named by `keycloak_role_pattern` in
`config/policies/room-access.json`. A user is admitted only when the account is
enabled, the effective `matrix-access` realm role is present, clearance meets the room minimum, and exactly one valid role is
assigned for that room. Missing users, unknown clearance values, conflicting
roles, or missing roles deny access.

The effective `matrix-access` realm role is a Matrix admission requirement.
Users without it can remain enabled in Keycloak but are excluded from Matrix
admission. Withdrawing it asks the private provisioner to lock the MAS identity
and finish its browser, OAuth, and compatibility sessions. Regranting the role
unlocks MAS; it does not restore finished sessions or delivered room content.
Administrative personal access tokens require a separate revoke operation;
MAS locking rejects them while locked, but `kill-sessions` does not delete
them.
The worker reads effective realm-role mappings separately from room client
roles.

The worker asks the private MAS provisioner to create/link the matching Matrix
identity to the Keycloak user subject before changing Matrix room access. It
creates
each configured room as private, non-federated, invite-only, and encrypted with
`m.megolm.v1.aes-sha2`. Each cycle reconciles encryption, history visibility,
room power levels, joins, invites, and revocations. Repeated operations are
safe; transient failures are retried on the next configured sync interval.
If a configured alias belongs to a room created by another account, or has no
E2EE state, synchronization fails closed instead of taking it over or applying
encryption to an existing plaintext history.

| Keycloak room role | Matrix power | Effect |
| --- | ---: | --- |
| `reader` | 0 | Read room history and receive future messages |
| `writer` | 10 | Send messages and reactions |
| `moderator` | 50 | Redact messages; cannot invite, kick, ban, or change room state |

Room admission also checks the user's clearance against the room's minimum.
Management thresholds stay at 100, so a room role cannot bypass the policy
worker by changing membership or room state. The optional comma-separated
`MATRIX_SERVICE_USER_IDS` environment variable allows those local service
accounts in every configured policy room. The worker first sets each account to
power 0, then joins it through the Synapse Admin API. It verifies membership and
polls OrgBot's private room-readiness endpoint before joining any eligible demo
user. It does not assign service accounts a Keycloak role, and power 0 is below
the message threshold of 10. Compose must pass this variable for a bot account
to be included.

OrgBot readiness is an attestation from the bot process that its Matrix sync is
healthy and its SDK sees itself joined to the room with the E2EE algorithm. It
is not independent proof that the device has received a room key or can decrypt
messages. If readiness fails or times out, policy-sync leaves demo joins closed
for that room and withholds a ready room map.

The policy seed contains six user-facing rooms and four isolated `[EXPORT
TEST]` rooms. Test-only accounts receive roles only in the latter. After every
room has reconciled successfully, the worker atomically writes a room map to
`ROOM_MAP_OUTPUT` (default `/data/room-map.json`) with mode `0600`.
The document contains the logical and Matrix room IDs, aliases, names, and
minimum clearance classifications. The worker removes an old map on startup or
after a failed sync. During initial bootstrap it may publish a
`status: provisioning` map with the validated rooms prepared so far, so OrgBot
can recognize a room before its readiness is polled. Export rejects that
provisional map. Only `status: ready` marks a complete successful
reconciliation. During ordinary reconciliation, the previous ready map is
retained until the final atomic replacement.
Mount the same volume read-only in OrgBot and Export Service at the configured
path. Policy Sync is the room-map writer.

Before it asks Synapse to join the service account or any demo user, policy-sync
also publishes a temporary `JOIN_POLICY_OUTPUT` snapshot (default
`/data/join-policy.json`). It contains the actual room IDs prepared so far and
only the current role-and-clearance grants for those rooms. It uses the same
start-of-Keycloak-read timestamp and maximum 60-second lifetime. Synapse may use
it only to admit a planned user to an admin-created managed room; it grants no
message or export access. Policy-sync removes it after the full chat-policy
snapshot is published, and removes it on startup or any failed reconciliation.

After the same successful cycle, policy-sync atomically writes
`CHAT_POLICY_OUTPUT` (default `/data/chat-policy.json`). This is the local
authorization snapshot for the Synapse direct-chat guard. It records the actual
Matrix room IDs, each room's currently authorized user IDs, the Matrix server
name, the OrgBot/service and policy-admin IDs, and enabled managed users with
their exact Keycloak group IDs. The snapshot identifies the
`matrix-access` role and each admitted user's current Matrix/export
entitlements; Export Service rechecks these against the token subject.
Its `generated_at` is captured before the first
Keycloak read and `expires_at` is no more than 60 seconds later. A slow or failed
reconciliation produces no usable snapshot; the guard must deny when the file
is missing, malformed, or expired.

Direct-chat permission comes only from an explicitly returned Keycloak group
whose name begins with
`policy.direct_chats.group_prefix` (default `1to1chat.`). The entire Keycloak
group ID is the sharing key: two users can chat only when both direct group
lists contain the same prefixed ID. Ordinary organization and department
groups do not grant access. The worker does not infer nested or inherited group
membership. The optional `policy.direct_chats.snapshot_ttl_seconds` controls
the snapshot lifetime and is capped at 60 seconds.

The snapshot file is mode `0640`; its containing directory is mode `0750`, so
Synapse can read it through its configured primary policy group (GID 1000). The room map
remains mode `0600` for its existing same-UID readers. Compose should mount the
snapshot read-only into Synapse and fail closed if that file is absent or stale.

## Runtime contract

Compose supplies `MATRIX_SERVER_NAME`, a read-only mount of the private
`SYNAPSE_ADMIN_TOKEN_FILE`, `MAS_PROVISIONER_API_URL`,
`MAS_PROVISIONER_API_TOKEN`, `KEYCLOAK_POLICY_CLIENT_SECRET`,
`KEYCLOAK_PUBLIC_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_SERVICE_CLIENT_ID` (the
`matrix-policy-sync` service account), and `KEYCLOAK_ROLE_CLIENT_ID` (the
`matrix-synapse` room-role container). Optional settings include
`POLICY_SYNC_INTERVAL_SECONDS`, `MATRIX_SERVICE_USER_IDS`,
`ORG_BOT_READINESS_TOKEN`, `ROOM_MAP_OUTPUT`, `CHAT_POLICY_OUTPUT`,
`JOIN_POLICY_OUTPUT`,
`ORG_BOT_READINESS_URL` (default `http://org-bot:8090`),
`ORG_BOT_READINESS_TIMEOUT_SECONDS` (default 120), and
`ORG_BOT_READINESS_POLL_INTERVAL_SECONDS` (default 2). Compose should pass the
bot's private export bearer secret as `ORG_BOT_READINESS_TOKEN`. The two
read-only config mounts are:

- `/app/config/demo-users.json`
- `/app/config/room-access.json`

The shared `/data` volume is writable here and read-only in OrgBot and Export
Service. Because the room map has mode `0600`, those readers run with the same
container UID as the Node worker; do not loosen the file permissions.

The Keycloak service account needs user, direct-group, and client-role query
permissions. The worker reads the currently verified Synapse admin token from the private MAS
provisioner volume and calls the private Synapse Admin API. User creation and
Keycloak subject linking are delegated to MAS through the provisioner's
internal authenticated API. No container or homeserver is contacted by this
image at build time.

The worker never exports or decrypts messages. Matrix power levels and room
membership are enforced server-side, but removing a user cannot erase messages
or encryption keys that their devices already received. A revoked user may
retain previously downloaded plaintext or keys; E2EE does not provide retroactive
revocation.

Run the local authorization, group-isolation, pagination, startup-order,
snapshot-expiry, and revocation checks with
`node --test src/policy-sync/*.test.mjs`. They do not require a running
homeserver or Keycloak instance. See the [administration guide](../../docs/guides/ADMINISTRATION.md)
and [operations guide](../../docs/guides/OPERATIONS.md) for deployment use.
