# Security model and release checks

This document describes the demo's trust boundaries and operating requirements. Assess each deployment against its live configuration, running image versions, and current advisories. [Operational scope](OPERATIONS.md#demo-and-production-boundaries)

## Identity and authorization boundaries

- Keycloak authenticates users and stores the login entitlement, clearances, room client roles, direct-chat groups, and export role.
- MAS brokers the Matrix sign-in. A missing `matrix-access` role denies the MAS browser login while leaving the Keycloak account enabled.
- Policy Sync reads Keycloak and applies explicit room membership and power levels in Synapse. Synapse enforces those permissions server-side.
- The Synapse policy module checks guarded direct-chat and call actions against expiring policy data and current room state.
- OrgBot is an E2EE endpoint with a persistent device store. It can decrypt messages and attachments delivered to its device; its export endpoint therefore returns sensitive plaintext.
- Export Service checks the current actor and room scope before it calls OrgBot and again before returning the archive.
- LiveKit carries call signaling/media. The separate JWT service issues participant tokens after the guarded authorization path; LiveKit is not the source of room-role policy.

Compromise of a Keycloak administrator, Synapse administrator, Policy Sync credentials, OrgBot device store, or export credential can cross these boundaries. Separate credentials and restrict each mount, network path, and operator account to the minimum needed.

## E2EE and revocation limits

End-to-end encryption protects message content from ordinary homeserver storage access, but every joined device that receives a room key can decrypt content. OrgBot is an explicitly trusted participant used for export. A user who already received plaintext or encryption keys may retain them after removal. Room removal cannot erase copies already downloaded. Revocation blocks future authorized actions after policy refresh; it is not retroactive erasure or guaranteed termination of an active call.

Clearance labels are policy classifications, not stronger cryptographic algorithms. All configured rooms use the configured Matrix E2EE algorithm.

## Network and secrets

The Compose file defines separate edge, application, control and per-database bridges. Application, control and data networks are internal; only Nginx and LiveKit have an external-facing bridge. Databases and internal APIs have no host publication. This is logical isolation on one Docker host, not separate machines or VLANs. The resolved model, host firewall, required self-hostname TLS paths and actual allowed/denied connections must be checked for each deployment. See [Network zones](../architecture/network-zones.md).

Use trusted TLS certificates outside isolated local development. Restrict administrative CIDRs, service credentials, database ports, container-socket access, and filesystem permissions. Never commit `.env`, generated configuration, signing keys, database passwords, client secrets, bearer tokens, private TLS keys, bot sessions, or crypto stores. Do not log request bodies, access tokens, E2EE content, or decrypted attachments.

## Release and vulnerability review

Before an external release:

1. Record exact image references and immutable digests for the images that will run.
2. Generate and retain an SBOM for each final image.
3. Scan those exact images with a current vulnerability database and record scanner version, database timestamp, coverage, and results. Check the inventory against expected operating-system, application and native components to identify blind spots.
4. Review findings against authoritative vendor advisories and the actual package/runtime path. Do not suppress findings merely to obtain a clean count.
5. Block release when scanning is unavailable, stale, or cannot inspect relevant package types; document any accepted residual risk and its owner.
6. Re-scan rebuilt images. A scan of a base image or a previous image tag is not evidence for a changed image.

Pinned versions are not automatically safe. Check upstream security notices and operating-system package updates on every release. Keep vulnerability evidence separate from credentials and production data.

## Example local image scan

After building an image, use Docker and Trivy to scan that exact local artifact. Replace `your-local-image:tag` with the built image reference and repeat for every release image. Each run keeps its reports in a private, local directory.

```sh
(
  umask 077
  mkdir -p internal/security-results
  SCAN_IMAGE_ID="$(docker image inspect --format '{{.Id}}' your-local-image:tag)" || exit 1
  SCAN_OUTPUT_DIR="$(mktemp -d "internal/security-results/${SCAN_IMAGE_ID#sha256:}.XXXXXX")" || exit 1
  trivy image --image-src docker --format cyclonedx --output "$SCAN_OUTPUT_DIR/image.sbom.json" "$SCAN_IMAGE_ID" || exit 1
  trivy image --image-src docker --scanners vuln --format json --output "$SCAN_OUTPUT_DIR/image.vulnerabilities.json" "$SCAN_IMAGE_ID"
)
```

Selecting only the Docker image source avoids falling back to a registry image with the same tag. SBOM generation and vulnerability scanning are separate outputs; a package inventory is not a passing scan. Record Trivy/database versions and timestamps with the image ID, keep findings of every severity, and use separate evidence filenames for each image. See the [official container-image guide](https://trivy.dev/docs/latest/guide/target/container_image/) for image sources and scan modes, and [SBOM generation](https://trivy.dev/docs/latest/guide/supply-chain/sbom/) for format details. The JSON scan retains findings rather than enforcing a release policy; a normal process exit is not release approval. These commands do not inspect application authorization or certify runtime exposure.

## Operational controls

Back up databases and persistent E2EE device state with a coordinated, tested procedure. Restrict access to recovery keys and backups. Do not use the destructive reset script as a troubleshooting shortcut. See [Operations](OPERATIONS.md) for the current recovery boundary.
