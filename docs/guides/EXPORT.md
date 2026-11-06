# Protected exports

The export page provides a separate administrative data-access workflow. A requester must authenticate through Keycloak using the `matrix-export-client` PKCE flow and hold both the current `matrix-access` and `org-export-admin` grants. The Export Service checks the token issuer and client, current entitlement, and selected room IDs against fresh policy data before contacting OrgBot. It checks authorization again before delivering the archive.

The grant can cover every room in the ready managed-room map and registered encrypted direct rooms that are ready and whose two participants still share an eligible group. Scope follows current policy and OrgBot's joined and confirmed-encrypted room state. The requester's personal Matrix membership does not limit this administrative grant. Treat every returned ZIP as sensitive plaintext. [Administration](ADMINISTRATION.md)

## Endpoints and status

- `GET /export/` serves the browser interface.
- `GET /_export/v1/rooms` returns currently authorized room choices. Send `Authorization: Bearer <Keycloak access token>`.
- `POST /_export/v1/exports` requests selected rooms or all rooms currently in scope. Send the same bearer header and `Content-Type: application/json`.
- `GET /_export/healthz` is a liveness endpoint, not proof of authorization or export readiness.

Missing/invalid authentication returns `401`; an authenticated but unauthorized requester or scope returns `403`. Missing, malformed, or expired policy inputs fail closed rather than falling back to a static room list.

The room-list response is JSON with the exact Matrix room IDs:

~~~json
{"rooms":[{"room_id":"!abc123:example.org","name":"Example room","classification":"intern","test_only":false,"exportable":true}]}
~~~

For a selected request, supply one or more IDs returned by that endpoint. For all rooms currently allowed to the requester, use `scope: "all"` as the alternative request body with the same headers:

~~~http
POST /_export/v1/exports
Authorization: Bearer <Keycloak access token>
Content-Type: application/json

{"scope":"selected","room_ids":["!abc123:example.org"]}
~~~

~~~json
{"scope":"all"}
~~~

An all-scope request uses the complete current server-side room scope, which may include rooms tagged test-only. Do not treat the browser's selection as an authorization boundary.

A successful response is `200 application/zip` and includes `Content-Disposition`, `Cache-Control: no-store`, `X-Export-Request-Id`, `X-Export-Completeness`, `X-Export-SHA256`, `X-Export-Format: matrix-portable-v1`, and `X-Export-Room-Ids` (the sorted exact room IDs). Invalid or oversized requests can return `400` or `413`. An invalid or failed OrgBot response can return `502`. Unavailable policy or audit data can return `503`. Errors before download starts use JSON without message contents.

## Portable archive shape

OrgBot produces a ZIP with `manifest.json`, `messages.ndjson`, and `attachments/` when media is present. The manifest records the requested and included Matrix room IDs, request metadata, completeness status, incompleteness reasons, room encryption/decryption state, and attachment metadata. Each NDJSON record carries event and room IDs, sender identity, timestamps, type, content status, and encryption provenance. Unavailable or undecrypted content is represented with an explicit status rather than silently treated as an empty successful message.

For each available attachment, the manifest records its archive path, file name, media type, byte length, and SHA-256. The export service returns the exact room IDs, a completeness value, the format label, and an archive SHA-256 response header. These values support integrity and scope checks; they do not prove that the archive contains every historical event.

These shortened examples show the field names and record types. An archive includes the manifest and one JSON object per line in `messages.ndjson` for each exported event.

~~~json
{
  "format_version": "matrix-portable-v1",
  "request_id": "synthetic-request-001",
  "requested_by": "synthetic-export-subject",
  "scope": "selected",
  "requested_room_ids": ["!abc123:example.org"],
  "included_room_ids": ["!abc123:example.org"],
  "created_at": "2026-01-01T00:00:00Z",
  "completeness": "partial",
  "incompleteness_reasons": ["history_before_process_start_not_available"],
  "source": {
    "matrix_user_id": "@orgbot:example.org",
    "device_id": "EXAMPLEBOT",
    "client_sdk": "matrix-nio",
    "homeserver_decryption_claim": false
  },
  "history_coverage": {
    "status": "partial",
    "available_from": "2026-01-01T00:00:00Z",
    "boundary": "events yielded to this running OrgBot process and retained in its bounded in-memory cache",
    "complete": false,
    "reason": "No historical backfill is performed."
  },
  "rooms": [{
    "room_id": "!abc123:example.org",
    "room_name": "Example room",
    "classification": "intern",
    "authorization_basis": "keycloak_room_role_and_clearance",
    "encrypted": true,
    "algorithm": "m.megolm.v1.aes-sha2",
    "decrypted_event_count": 1,
    "undecrypted_event_count": 0,
    "history_status": "partial",
    "history_reason": "history_before_process_start_not_available"
  }],
  "encrypted_rooms": {
    "!abc123:example.org": {
      "algorithm": "m.megolm.v1.aes-sha2",
      "decryption_status": "partial",
      "key_coverage": "unknown",
      "decrypted_by": {"user_id":"@orgbot:example.org","device_id":"EXAMPLEBOT"}
    }
  },
  "attachments": []
}
~~~

~~~json
{"record_type":"message","event_id":"$synthetic-event","room_id":"!abc123:example.org","room_name":"Example room","classification":"intern","authorization_basis":"keycloak_room_role_and_clearance","user_id":"@demo-alex:example.org","display_name":"Alex Example","timestamp":1767225600000,"timestamp_utc":"2026-01-01T00:00:00Z","type":"m.room.message","message_type":"m.text","content_status":"available","content_status_reason":null,"body":"synthetic example","encryption_provenance":{"encrypted":true,"algorithm":"m.megolm.v1.aes-sha2","decryption_status":"decrypted","key_coverage":"unknown","decrypted_by_user_id":"@orgbot:example.org","decrypted_by_device_id":"EXAMPLEBOT"},"attachment_paths":[],"attachments":[]}
~~~

See the [OrgBot archive contract](../../src/org-bot/README.md) for the complete implementation description.

## Completeness boundary

Archives are marked `partial`. OrgBot exports events observed by its running Matrix client and retained in a bounded in-memory cache. It does not backfill old history or prove that every key, event, or attachment was received. Process restarts, cache limits, missing room keys, and unavailable media can leave gaps. A correctly hashed ZIP can still be incomplete.

OrgBot is a trusted E2EE participant: it can decrypt content delivered to its own device and can return that plaintext to an authorized export request. Protect its session, device store, and export endpoint accordingly. Export availability is not independent proof that all messages were decrypted.

## Operator checks

After receiving an archive, verify the HTTP completeness header, the manifest's `completeness` and `incompleteness_reasons`, and the exact requested/included room IDs. Recompute the downloaded ZIP's SHA-256 and compare it to `X-Export-SHA256`. For each attachment, compare the extracted file's byte length and SHA-256 with its manifest entry. Inspect NDJSON `content_status` and encryption provenance for unavailable or undecrypted records. These checks establish that the bytes match the returned digest and the archive's declared scope; metadata and hashes do not establish complete history, complete key coverage, authorship, or legal retention.

The public verification procedure above applies to exported archives. Private fixture tests exercise specific expected records and attachments.

## See also

- [Administration](ADMINISTRATION.md) for export-role assignment
- [Security](SECURITY.md) for service boundaries
- [Scoped export and integrity example](../examples/evidence-safe-export.md)
