# OrgBot

OrgBot is a dedicated Matrix client for E2EE-aware room export. Synapse only
relays encrypted events; it does not decrypt or gain access to this bot's
plaintext. The bot can read an encrypted event only after its own `matrix-nio`
device has received the required room keys and successfully decrypted it. A bot
device is therefore an additional trusted endpoint and must be disclosed to
room members. OrgBot has no user clearance, room role, or Matrix administrator
power.

## Runtime contract

Build `src/org-bot/Dockerfile`. Keep the service on a private Compose network
and publish no host port. Mount the SDK store at `/var/lib/orgbot` on persistent
private storage. It contains the Matrix device identity, encryption keys, and
sync state. The directory is set to mode `0700`; protect its volume and backups
like credentials. Replacing or losing this store can make old encrypted events
undecryptable. Do not copy it into a public repository or customer image.

Mount policy-sync's shared `/data/room-map.json` read-only at the same
path in OrgBot. The map supplies the baseline room allowlist, names, and
classifications; if it is missing or invalid, those baseline rooms cannot be
exported.

For policy direct rooms, mount `/run/chat-guard/direct-rooms.json` and the fresh
`/data/chat-policy.json` read-only. The registry supplies exact room IDs and
the two local human participants, but does not grant authority by itself. Before
joining or exporting, OrgBot checks that the chat-policy snapshot is unexpired,
both enabled users remain listed, and their explicit `direct_groups` entries
share the same exact Keycloak group ID whose exact name starts with the
configured nonempty, dot-terminated prefix (normally `1to1chat.`). Group path
is metadata only: a nested group explicitly listed in `direct_groups` may
qualify, while ancestor membership is never inferred. Substring matches,
ordinary groups, service/admin accounts, malformed or duplicate entries, and
stale snapshots do not grant access. The snapshot must have
`generated_at <= now < expires_at` and a lifetime no longer than 60 seconds.
OrgBot joins only a bot-targeted invite for a registry room when its
inviter is one of the authorized pair. Direct rooms are exportable only after
the registry says `status: "ready"` and the client SDK sync state confirms the
bot is joined to the encrypted room. A `pending_bot` room can be joined; its
events remain non-exportable until a fresh registry snapshot marks it ready.
The bounded cache is retained during that short transition so messages received
after the real encrypted join are not lost; OrgBot purges it when the grant is
removed or the SDK reports that the room is unencrypted.
The export classification is `unclassified_direct_chat`; it describes the
authorization mechanism, not message sensitivity. The registry rank is
metadata ordering only and is not a security level.

Required environment variables:

| Variable | Purpose |
| --- | --- |
| `ORG_BOT_HOMESERVER_URL` | Matrix client API URL reachable on the private Compose network |
| `ORG_BOT_USER_ID` | Dedicated Matrix account created by the provisioner |
| `ORG_BOT_DEVICE_ID` | Stable device identifier; changing it creates a different crypto device |
| `ORG_BOT_SESSION_FILE` | Read-only mount of the provisioner-issued OrgBot Matrix session JSON; default deployment path is `/run/mas-bootstrap/orgbot-session.json` |
| `ORG_BOT_EXPORT_TOKEN` | Random, private bearer credential shared only with Export Service; minimum 32 characters |
| `ORG_BOT_STORE_PATH` | Persistent SDK state directory; defaults to `/var/lib/orgbot` |

Optional settings: `ORG_BOT_ROOM_MAP_PATH` (default `/data/room-map.json`),
`ORG_BOT_DIRECT_ROOMS_PATH` (default `/run/chat-guard/direct-rooms.json`),
`ORG_BOT_CHAT_POLICY_PATH` (default `/data/chat-policy.json`),
`ORG_BOT_HTTP_PORT` (default `8090`), `ORG_BOT_CACHE_LIMIT` (default 5,000
events), `ORG_BOT_CACHE_MAX_BYTES` (default 64 MiB),
`ORG_BOT_EXPORT_MAX_EVENTS` (default 5,000 per room),
`ORG_BOT_MAX_ATTACHMENT_BYTES` (default 25 MiB per attachment), and
`ORG_BOT_SYNC_STALE_SECONDS` (default 90). OrgBot runs as container UID 1000
because the shared room map is mode `0600` and is also read by policy-sync and
Export Service. The export
endpoint returns plaintext and file bytes, so restrict network access to Export
Service and do not enable request or response body logging in proxies.

## Local API

- `GET /healthz` — readiness only, after a successful recent Matrix sync and a
  server-side `keys/query` confirms that the configured device ID and both
  public identity keys match the persistent nio crypto store.
- `GET /v1/rooms/{room_id}/readiness` — requires the same private bearer token
  as exports. Returns `200` with only the room ID, `encrypted: true`, algorithm,
  bot user/device IDs, and last successful sync time when sync is fresh (90
  seconds by default), the SDK room contains the encryption event and the bot
  is still a joined member. For registered direct rooms, a fresh valid registry
  and group-policy snapshot must also still authorize the pair and mark the
  room ready. A stale cached room after leave/revocation therefore returns
  `503`. Policy-sync separately confirms the bot's membership via Synapse
  before it grants demo-user access. Otherwise returns `503` with a reason code. The
  freshness window can be changed with `ORG_BOT_SYNC_STALE_SECONDS`. Device
  identity is rechecked every five minutes and immediately after process start.
- `POST /v1/rooms/export` — bearer protected; accepts the request forwarded by
  Export Service:

```json
{
  "request_id": "example-request-id",
  "requested_by": "keycloak-subject",
  "scope": "selected",
  "room_ids": ["!room:example.org"]
}
```

`scope` is `selected` or `all`. Export Service must first enforce the actor's
administrator role and organization room allowlist. OrgBot independently
validates the request shape and requires itself to be joined to every requested
room. It trusts the private bearer caller for the actor and scope authorization;
the endpoint must never be exposed publicly.

The provisioner issues a non-admin Matrix compatibility session and writes
`{"schema_version":1,"user_id":"@orgbot:<server>","device_id":"FOLDFENCEORGBOT","access_token":"<opaque>","admin":false}`
to the owner-only session file. The file must be a regular, non-symlink file
owned by the OrgBot process with mode `0600`; its schema and exact user/device
identity are checked before use. OrgBot calls Matrix `whoami` and requires the
configured user and device, then calls Synapse's user joined-rooms admin
endpoint and requires the expected `403 M_FORBIDDEN` response before restoring
the token in `matrix-nio`. Any other response fails startup closed. No password
fallback exists. Subsequent Matrix client API calls and sync use the Synapse
URL. MAS v1.28.0's CLI-issued compatibility session/access token has no expiry
value. OrgBot loads the file at startup and does not silently mint or reload a
replacement while running. Explicit token revocation therefore requires
stopping OrgBot and performing a deliberate session recovery; keep the device
ID and persistent crypto store unchanged. Before readiness, OrgBot
queries the server for its own device and compares the returned Ed25519 and
Curve25519 public identity keys with those derived from its persistent private
store. If the server has no record for that device, nio is instructed to
republish the same persisted identity and OrgBot queries again. A present but
mismatched identity fails closed. This also recovers the case where nio's
persisted `shared` flag says keys were uploaded but the server record is gone.
The public keys are used only for this comparison and are not logged. This
matches the configured MAS delegation path and avoids asking Synapse's legacy
password-login endpoint to authenticate a delegated account.

An accepted export returns `application/zip` and these headers:

- `X-Export-Format: matrix-portable-v1`
- `X-Export-Room-Ids: <sorted comma-separated exact room IDs>`
- `X-Export-Completeness: partial`

Every archive contains `manifest.json`, `messages.ndjson`, and an
`attachments/` directory when media is available. NDJSON records retain the
event ID, room ID/name/classification, sender Matrix ID/display name, Matrix
millisecond timestamp and UTC timestamp, event/message type, body,
`content_status`, attachment paths, and encryption/decryption provenance. The
manifest uses `format_version: "matrix-portable-v1"` and
`incompleteness_reasons`. Undecrypted events are included with a null body and
`content_status: "undecrypted"`; they are never represented as empty successful
messages. The manifest records each attachment's generated archive path,
original display name, MIME type, byte length, SHA-256 and
`availability: "available"`/`"unavailable"`. Available media is downloaded by
matrix-nio; encrypted Matrix media is decrypted using the file key delivered
for that media, then archived as its original bytes. Missing files retain an
unavailable manifest entry and reason.

## Coverage boundary and risks

The implementation exports only events delivered to this running SDK client
and retained in its bounded in-memory cache. It does not backfill history. It
cannot prove the absence of earlier events, events omitted by sync pagination,
events lost on restart, cache evictions, or events for which this device never
received keys. Therefore every archive is marked `partial`, with
`history_before_process_start_not_available` in its manifest. Individual
undecrypted events, missing media, unknown room metadata, and size-limit skips
are also marked with explicit statuses and reasons. `partial` means the ZIP was
produced; it is not a completeness or legal-retention attestation.

Direct-room invitations and exports are checked against both the guarded room
registry and the current, expiring user-group snapshot. If either file is
missing, malformed, duplicated, or stale, direct-room joins and exports fail
closed. OrgBot retains its existing room membership and bounded cache during
that unknown-policy window because missing policy data does not prove a grant
was revoked; it accepts no invite, captures no new direct-room event, and does
not export those rooms until the inputs recover. When a fresh valid snapshot
confirms a pair is no longer authorized, or the SDK confirms a joined room is
unencrypted, OrgBot purges that room's cached records and leaves when needed.
Synapse's chat guard is the enforcement layer; these checks are a second
client-side gate.

The bot keeps exported text and media only in bounded process memory while
serving the ZIP. It does not log plaintext, message bodies, file names, bearer
tokens, login secrets, access tokens, or cryptographic keys. The SDK's private
device store is persistent and sensitive. A persistent plaintext event store,
historical backfill, redaction handling, and export-size streaming are not
implemented. The supported `matrix-nio` API is pinned and exercised by the
SDK-backed tests, but those tests do not establish complete history or
successful decryption of every event. Media downloads are buffered by the SDK before
the configured post-download size check when the homeserver omits a size; keep
the bot isolated and the trusted media source controlled.

The per-room readiness response is a client-side attestation based on OrgBot's
current SDK state and the server's published device-key record. It does not
independently prove the homeserver's room state or guarantee that decryption
keys are available. Verify message and media decryption with an isolated
synthetic end-to-end fixture before relying on an export integration. Such a
check still does not prove complete room history or that every key reached the
bot.

`requirements.txt` pins the two direct dependencies; `requirements.lock` pins
and hashes the full Python 3.12 dependency graph for supported platforms. The
image installs only from that hash-checked lock. The current SDK release uses
vodozemac for E2EE; the offline media round-trip test checks its encrypted-file
decryptor. Exercise restart recovery and missing-key or missing-media behavior
in the target deployment. Native mobile clients, Matrix ringing and actual
audio/video transport require separate client acceptance. OrgBot cannot backfill
events, so exports remain `partial` and do not attest to complete history.

Run the portable offline OrgBot tests with
`python3 -m unittest discover -s src/org-bot -p 'test_*.py' -v`. These do not
contact a homeserver or claim complete history. `matrix-nio` is distributed
under the ISC license; see [third-party runtime notices](../../THIRD-PARTY-RUNTIME-NOTICES.md).
