# Export service

This service is the server-side authorization and audit boundary for room
exports. It verifies each Keycloak-signed access token, requires the
`org-export-admin` realm role, and restricts requested room IDs to the private
room map and fresh chat-policy snapshot published by policy-sync. It also
includes registered direct rooms only while both listed participants still
share an exact prefixed Keycloak group ID. `scope: "all"` means every room
currently allowed by those sources, including clearly tagged export-test rooms.
Audit records capture the request ID, actor subject,
issuer, explicit scope, room IDs, outcome, and (on success) archive digest and
completeness. Accepted, denied, failed, and completed attempts are logged.
Tokens and message content are never logged. If the audit log cannot be
written, the request fails closed.

The token subject must have current `matrix-access` and `org-export-admin`
grants in the fresh snapshot. Signature and role claims in an older access
token are not sufficient after withdrawal. The same current actor and scope
checks run again after OrgBot prepares the archive, before any archive bytes
are delivered.

The Node runtime dependency is pinned by `package-lock.json`; the image uses
`npm ci` so deployment does not resolve a new package version during each
build.

## Runtime contract

Required environment variables:

- `EXPORT_OIDC_ISSUER`: exact expected token issuer (`iss`).
- `EXPORT_OIDC_JWKS_URL`: Keycloak realm JWKS URL.
- `EXPORT_ALLOWED_AZP`: comma-separated allowed Keycloak client IDs; the token's
  `azp` must match one of them.
- `EXPORT_ROOM_MAP_PATH`: path to the private policy-sync room map. Compose
  mounts the shared mode-0600 map read-only.
- `EXPORT_CHAT_POLICY_PATH`: path to the short-lived policy snapshot; Compose
  mounts the shared mode-0640 file read-only.
- `BOT_EXPORT_URL` and `BOT_EXPORT_TOKEN`: private OrgBot endpoint and
  service credential. Supply the credential through the runtime secret store.

Optional: `EXPORT_ADMIN_ROLE` (defaults to `org-export-admin`),
`EXPORT_AUDIT_PATH` (defaults to `/data/export-audit.jsonl`),
`EXPORT_DIRECT_ROOMS_PATH` (defaults to
`/run/chat-guard/direct-rooms.json`), `EXPORT_MAX_ARCHIVE_BYTES` (defaults to
200 MiB), and `PORT` (defaults to 8090). The direct-room registry is optional;
when the file is absent, only managed rooms are listed. A malformed registry
returns 503.

The service reloads the room map, chat-policy snapshot, and optional direct-room
registry on every authenticated room-list or export request. It returns 503 if
configured policy files are missing, malformed, or expired, or if the room map
is older than 60 seconds. Direct-room registry entries may have status
`pending_bot`, `policy_unavailable` or `ready`. The first two are withheld
until the registry reports
that OrgBot has joined and the room's Megolm encryption state has been verified;
only `ready` rooms can enter an export scope. This is a room-state attestation,
not independent proof that OrgBot can decrypt the history. A ready direct room
is included only if its two registry participants are current snapshot users
and share the same Keycloak group ID whose group name starts with `1to1chat.`.
Ordinary organization and department groups never grant direct-chat access.
Both room-map and chat-policy paths are required. There is no static allowlist
fallback when current authorization data is unavailable.

`GET /v1/rooms` returns current managed and authorized direct room IDs, display
names, classification labels, and test-only flags to an authenticated export
admin. The browser page at `/export/` uses this endpoint; the export API loads
the same current authorization sources again for every download.

`POST /v1/exports` accepts either
`{ "scope": "selected", "room_ids": ["!room:server"] }` or
`{ "scope": "all" }`. The service delegates archive creation to OrgBot and
requires the exact room IDs in `X-Export-Room-Ids` and a
`X-Export-Completeness` value of `complete`, `partial`, or `unknown`. The bot
must return `application/zip` and identify the format as
`X-Export-Format: matrix-portable-v1`. The service computes the final SHA-256
and returns it as `X-Export-SHA256`. Archives are size-limited and spooled to a
private temporary directory before the response is sent. `GET /healthz` is an
unauthenticated liveness check only.

The service calls `BOT_EXPORT_URL` with
`{ "request_id", "requested_by", "scope", "room_ids" }` and sends
`BOT_EXPORT_TOKEN` as a bearer credential. A returned ZIP with completeness
`partial` or `unknown` is delivered with that exact status in the response
header and audit record. `state: "completed"` means the ZIP was produced and
its digest and completeness were recorded before delivery; it does not mean
the export was complete. If delivery then fails, a failed event is appended.
Clients must inspect the header and manifest before treating the result as
complete.

## Portable ZIP contract for OrgBot

The trusted OrgBot produces decrypted content observed by its Matrix client and
the archive. The current portable ZIP contains:

- `manifest.json`: format version, request ID, creation time, requested and
  included room IDs, completeness status, and encrypted-room provenance. For
  every encrypted room, preserve the encryption state/algorithm and the
  decryption/key-coverage result; never imply that an encrypted room was
  exported completely when keys or history are missing. Include an entry for
  each attachment with its path, media type, byte length, SHA-256, and
  availability.
- `messages.ndjson`: one tagged record per cached Matrix event, including event
  ID, room ID/name/classification, `user_id`, display name, Matrix millisecond
  and UTC timestamps, event/message type, body, `content_status`, attachment
  references, and `encryption_provenance`. Undecrypted events remain explicit
  with a null body and an unavailable status.
- `attachments/`: original attachment files, with SHA-256 entries in the
  manifest. Missing or inaccessible files must be represented as unavailable
  manifest entries rather than silently omitted.

The export service relays OrgBot's ZIP and records its digest; it does not
inspect ZIP entries or independently attest to message completeness. The
`X-Export-Format` header is a contract declaration, not archive validation.
The deployment guide describes how to verify archive contents against
synthetic fixture expectations. OrgBot reports `partial` because it cannot
backfill or prove complete history.

For example, a cached decrypted event is tagged like this:

```json
{"record_type":"message","event_id":"$event","room_id":"!room:example.org","room_name":"Pummel","classification":"intern","user_id":"@demo-01:example.org","display_name":"Demo User 01","timestamp":1767225600000,"timestamp_utc":"2026-01-01T00:00:00Z","type":"m.room.message","message_type":"m.text","content_status":"available","body":"synthetic example","encryption_provenance":{"encrypted":true,"algorithm":"m.megolm.v1.aes-sha2","decryption_status":"decrypted","key_coverage":"unknown","decrypted_by_user_id":"@orgbot:example.org","decrypted_by_device_id":"FOLDFENCEORGBOT"},"attachment_paths":[],"attachments":[]}
```

## Deployment boundary

Compose wires this service behind Nginx at `/_export/` and serves the
authorization-code-with-PKCE export page at `/export/`. It reads the current
policy-sync room map and snapshot plus the Synapse guard's direct-room registry,
then calls OrgBot at its private ZIP endpoint. The service relays
`partial`/`unknown` archives with the status and audit digest; it does not
validate ZIP contents itself. The export guide describes the expected
manifest, event tags, attachment bytes, and hash checks. Synapse is not treated
as a source of decrypted E2EE plaintext.

Run the public authorization and room-ID checks with
`node --test src/export-service/*.test.mjs`. See the [export guide](../../docs/guides/EXPORT.md)
for an end-to-end verification plan.
