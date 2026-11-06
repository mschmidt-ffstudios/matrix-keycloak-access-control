# Example: Export page

An export administrator opens the configured export page, signs in through the identity provider using the authorization-code flow with PKCE, and selects rooms currently allowed by policy. The browser uses a public client; it must not contain a client secret.

The service validates the token issuer and client, then checks that the subject currently has both the Matrix application entitlement and the export role. It compares every requested room ID against the current policy scope and fails closed if required inputs are missing or stale. Export authorization is a separate, privileged data-access grant and can cover rooms the requester cannot join as a Matrix user.

A successful response is a ZIP with a completeness status and digest. Inspect the archive manifest and room scope before relying on its contents. Archives may be partial because the export bot cannot backfill old history or prove complete key coverage. See [Protected exports](../guides/EXPORT.md) and [Administration](../guides/ADMINISTRATION.md).
