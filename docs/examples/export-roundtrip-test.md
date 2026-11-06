# Example: Export integrity checks

Use this checklist for a controlled export test with a known dataset.

1. Use synthetic users, encrypted rooms, known text markers, and distinct small test attachments.
2. Record the expected Matrix room IDs, event IDs, sender IDs, timestamps, attachment byte lengths, and hashes in a protected test record.
3. Request only those rooms through the protected export API.
4. Check the HTTP status and completeness header, then inspect the manifest's requested/included room IDs and incompleteness reasons.
5. Recompute the returned ZIP SHA-256 and compare it to the response header.
6. Parse each NDJSON record and compare expected event metadata. Hash each extracted attachment and compare its length and SHA-256 with the manifest.
7. Record whether all expected fixture events and files arrived. Preserve the archive's `partial` status even when the controlled test passes.

This procedure verifies the records, bytes and scope observed in the fixture. [Protected exports](../guides/EXPORT.md)
