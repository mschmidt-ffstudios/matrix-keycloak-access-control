# Example: Scoped export and integrity checks

A protected export is a ZIP containing a manifest, newline-delimited event records, and available attachment bytes. The excerpt shows synthetic archive entries.

~~~text
manifest.json
messages.ndjson
attachments/
  940ffb8fc9eedd9c606a.bin
~~~

A manifest can state its coverage explicitly:

~~~json
{
  "format_version": "matrix-portable-v1",
  "request_id": "synthetic-request-001",
  "scope": "selected",
  "requested_room_ids": ["!room:example.org"],
  "included_room_ids": ["!room:example.org"],
  "completeness": "partial",
  "incompleteness_reasons": ["history_before_process_start_not_available"],
  "attachments": [{
    "event_id": "$event",
    "room_id": "!room:example.org",
    "path": "attachments/940ffb8fc9eedd9c606a.bin",
    "file_name": "example.bin",
    "media_type": "application/octet-stream",
    "byte_length": 4,
    "sha256": "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7",
    "availability": "available",
    "reason": null,
    "encrypted_at_rest_in_matrix": true
  }]
}
~~~

Each event record identifies the room and event, sender, timestamp, content status, and encryption provenance. An unavailable or undecrypted event should remain explicit rather than being represented as an empty successful message.

Check the returned completeness header and manifest, compare requested and included room IDs, and compare the downloaded ZIP's SHA-256 with the response digest. Check each attachment's byte length and digest as well. These checks cover the returned bytes and declared scope. The manifest records known coverage limits. [Export guide](../guides/EXPORT.md)
