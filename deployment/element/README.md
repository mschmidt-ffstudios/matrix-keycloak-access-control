# Policy-aware Element Web

This derivative pins Element Web v1.12.33 and adds an authenticated contact allowlist to its direct-message picker. The upstream directory search is already filtered by the native Synapse callback. Element also derives recents and suggestions from local room membership, and normally resolves an explicitly typed Matrix ID through a public profile lookup. The derivative applies the same fresh allowlist to all those sources, selections, pasted IDs and the immediate pre-draft action.

The Synapse module exposes `GET /_synapse/client/foldfence/contacts`. It authenticates the Matrix bearer token and derives the requester from that token. It accepts no requester override. It returns only contact IDs sharing an exact eligible Keycloak group UUID and the private snapshot's expiry, with `Cache-Control: no-store`. The client refreshes every five seconds while the dialog is open. Missing, malformed, expired, unavailable or wrong-user responses produce no permitted contacts. Synapse continues to enforce room creation, invitations and messages independently.

Build on a host with sufficient memory (8 GB recommended), then deploy the built image:

```sh
docker build -f deployment/element/Dockerfile -t foldfence-demo/element-web:v1.12.33-policy1 .
```

The Dockerfile verifies the complete upstream archive hash; the patcher separately verifies the exact dialog source hash before changing it. Upstream dependency lockfiles are retained. The runtime keeps the original Element server configuration and the existing generated branding/config mounts.

The image build creates a source archive from the modified source tree at `/foldfence-source.tar.gz` and includes a link from the direct-message dialog. Check that the selected deployment serves that archive and that it corresponds to the running build before relying on a source offer. Build with Node 24 and the package manager version declared in `package.json`, then run `pnpm install --frozen-lockfile` and `VERSION=v1.12.33-ffs-policy1 pnpm --dir apps/web build`. The repository's Element changes are AGPL-3.0-only; other repository-authored files and bundled runtime components retain their separate license terms. See the [license map](../../LICENSES/README.md) and [third-party runtime notices](../../THIRD-PARTY-RUNTIME-NOTICES.md).

This feature filters this supplied web client. Other Matrix clients may display locally known users, room participants or earlier conversations from their own cache. Server-side authorization still rejects an unpermitted direct chat in those clients. It does not make already shared room membership or previously delivered history disappear.
