# Architecture decisions

These decisions describe the reusable implementation. Private deployment choices and historical acceptance records are kept separately and do not define a customer installation.

## One realm and one homeserver

The example uses one Keycloak realm and one Matrix homeserver. A single Matrix server name supplies stable IDs such as `@demo-01:matrix.example.invalid`. Choose that name before initializing data; changing DNS does not rename existing Matrix identities. Multiple realms and identity brokering are extensions, not implemented alternative deployment profiles.

## Separate identity, admission and room permissions

Keycloak authenticates and owns assignments. MAS provides Matrix sessions; Synapse delegates authentication to MAS. The effective `matrix-access` realm role controls application admission independently of clearance, room roles and direct-chat groups. A client-specific Keycloak flow runs copied Browser methods before a direct conditional missing-role/deny subflow, including SSO-cookie authentication. Other Keycloak clients retain their own flows. Local MAS password login is disabled.

Policy Sync reconciles current Matrix grants. Confirmed application withdrawal locks the linked MAS user and terminates its browser/OAuth/compatibility sessions through the private Provisioner. The Keycloak account stays enabled; unlocking does not resurrect terminated sessions. Administrator-issued personal access tokens are a separate privileged boundary and need explicit lifecycle controls.

## Synapse as the selected homeserver

Matrix is a protocol, not a specific server. This implementation selects Synapse for its MAS integration, administrative API and module callbacks. Its policy module is specific to Synapse, and its event callback is experimental upstream. Another homeserver would require an equivalent server enforcement implementation and new acceptance tests.

## Default-deny managed rooms and pair chats

A managed room requires application admission, sufficient clearance and exactly one recognized per-room role. Policy Sync applies membership and powers; Synapse enforces room actions. Direct chats require two eligible people sharing an exact Keycloak group UUID in the configured `1to1chat.*` namespace. Ordinary organization groups grant nothing. User-created group rooms are denied by this example policy. A signed pair marker and private registry bind the authorization to the actual room ID.

Snapshots expire. New policy-controlled actions fail closed when policy is missing or invalid. A transient outage preserves existing pair memberships; a fresh confirmed revocation can remove them. Clients cannot recall already delivered plaintext or keys. [Administration](docs/guides/ADMINISTRATION.md).

## Separate policy authority from E2EE content access

The policy service uses an administrative Matrix identity to manage membership and protected state. OrgBot is a separate non-admin E2EE device with power level 0 in approved rooms. It can decrypt only content whose keys it receives. OrgBot receives only its dedicated read-only session volume, never the administrator-token volume. Device/crypto state needs persistent protected storage; the event cache remains bounded and in memory. [Export](docs/guides/EXPORT.md).

## Scoped export with explicit coverage

Export Service verifies Keycloak signatures and allowed client/issuer claims, current application/export entitlement and current room scope. It rechecks before delivery. OrgBot supplies observed events, original attachments and a `matrix-portable-v1` manifest. Missing history, keys or media remain explicit gaps; `partial` and `unknown` must never be relabeled complete. Digests support integrity verification but do not establish legal evidence retention.

## Calls and network boundaries

Synapse checks current room grants, membership, encryption and writer-level call permission before token issuance. LiveKit handles signaling/media separately. The Compose project has no TURN relay or implemented VLAN/DMZ segmentation. Target transport, device behavior and active-call revocation require their own verification. [Calls](docs/guides/CALLS.md), [network design](docs/architecture/network-zones.md).

## Reusable configuration and private operations

Public inputs, startup scripts, build definitions and synthetic documentation screenshots stay reusable. Organization branding, transfer scripts, target profiles, live screenshots and historical deployment decisions stay under `internal/` in the private Foldfence Studios Gitea working repository. Credentials, generated private configuration and raw evidence stay in protected local or operator storage outside Git history. GitHub publication uses a reviewed source snapshot and fresh history after the owner's approval.

Original project code uses Apache-2.0. Explicit source derivatives and runtime components retain their declared licenses and notices. [Runtime notices](THIRD-PARTY-RUNTIME-NOTICES.md) · [Release guidance](docs/guides/RELEASING.md)
