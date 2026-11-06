# Direct-chat policy sequence

This sequence describes the source policy for synthetic users. It is not a
deployment or acceptance report. The clients shown below are already
authenticated; MAS/Keycloak login is a separate flow. `SynapseGuard` runs
inside Synapse, not as another network service.

Keycloak administrators assign explicit groups such as
`1to1chat.orga-a.abteilung-x`. Two enabled, provisioned users with current
`matrix-access` and a recognized clearance qualify when their returned direct
memberships share the exact same qualifying group UUID. The
prefix selects eligible group names; an ordinary department group does not
grant a direct chat. Room roles and clearance remain separate permissions.

## Authorized creation and messaging

```mermaid
sequenceDiagram
    title Group-authorized monitored direct chat
    participant Keycloak
    participant PolicySync
    participant PolicyFile
    participant AliceApp
    participant SynapseGuard
    participant OrgBot
    participant BenApp
    PolicySync->>Keycloak: Read users, effective entitlement, clearance and direct memberships
    Keycloak-->>PolicySync: Alice and Ben share a qualifying group UUID
    PolicySync->>PolicyFile: Atomically write chat-policy.json, maximum TTL 60s
    AliceApp->>SynapseGuard: GET authenticated allowed contacts
    SynapseGuard->>PolicyFile: Read fresh grants for token's own user
    SynapseGuard-->>AliceApp: Allowed IDs and expiry, no-store
    AliceApp->>AliceApp: Filter local recents, suggestions and typed IDs
    AliceApp->>SynapseGuard: createRoom with Ben as the sole human invitee
    SynapseGuard->>PolicyFile: Check freshness and shared policy group UUID
    PolicyFile-->>SynapseGuard: Pair authorized
    SynapseGuard->>SynapseGuard: Create private E2EE room and immutable signed pair
    SynapseGuard-->>OrgBot: Invite with registered pending_bot room
    SynapseGuard-->>BenApp: Invite to the same room
    OrgBot->>PolicyFile: Validate current pair authorization
    OrgBot->>SynapseGuard: Join the authorized room
    SynapseGuard->>SynapseGuard: Confirm Megolm and joined bot, registry ready
    BenApp->>SynapseGuard: Accept invitation, policy checked again
    AliceApp->>SynapseGuard: Send encrypted message
    SynapseGuard->>PolicyFile: Recheck current pair permission
    SynapseGuard-->>BenApp: Deliver only if authorized and bot joined
    SynapseGuard-->>OrgBot: Sync encrypted event
```

The two invitations are persisted before either client's asynchronous join is
guaranteed. The diagram shows one permitted ordering; Ben may join before the
bot. User messages stay blocked until bot membership and encryption are in
place. The client may need to retry an early send. Bot membership and registry
readiness do not prove possession of every E2EE key or a complete export.

Other user-created rooms, additional human invitees, public aliases and
third-party invitations are denied. Client-supplied room state is replaced
with the fixed private, non-federated policy. `is_direct` and `m.direct` never
grant permission. A missing or stale policy denies protected actions.

## Revocation in an existing chat

```mermaid
sequenceDiagram
    title Removing the last shared direct-chat grant
    participant Admin
    participant Keycloak
    participant PolicySync
    participant PolicyFile
    participant ClientApp
    participant SynapseGuard
    participant OrgBot
    Admin->>Keycloak: Remove one person's qualifying membership
    PolicySync->>Keycloak: Read current group memberships
    Keycloak-->>PolicySync: No remaining shared qualifying group UUID
    PolicySync->>PolicyFile: Atomically replace the snapshot
    ClientApp->>SynapseGuard: Send, invite or join in the existing room
    SynapseGuard->>PolicyFile: Evaluate current pair permission
    PolicyFile-->>SynapseGuard: Deny
    SynapseGuard-->>ClientApp: 403 forbidden
    SynapseGuard->>SynapseGuard: Fresh known revocation: sweep leaves both people and OrgBot
    SynapseGuard-->>ClientApp: Membership leave events
    SynapseGuard-->>OrgBot: Membership leave event, no longer export-ready
```

The worker waits 20 seconds between reconciliation runs by default. A snapshot
expires no later than 60 seconds after its Keycloak read began, so a failed or
slow sync cannot authorize new actions indefinitely. With a fresh snapshot
confirming loss of the last shared group, new actions are denied and the
five-second cleanup sweep ends memberships. Another
remaining shared qualifying group keeps the pair authorized. Already received
messages and device keys cannot be recalled. Reauthorization does not
automatically restore old memberships.

## Policy unavailable is not confirmed revocation

```mermaid
sequenceDiagram
    title Missing or expired policy preserves membership while denying actions
    participant ClientApp
    participant SynapseGuard
    participant PolicyFile
    participant OrgBot
    ClientApp->>SynapseGuard: Create, join, invite or send
    SynapseGuard->>PolicyFile: Require a fresh valid snapshot
    PolicyFile-->>SynapseGuard: Missing, malformed, future-dated or expired
    SynapseGuard-->>ClientApp: Deny protected action
    SynapseGuard->>SynapseGuard: Mark registry policy_unavailable, preserve memberships
    OrgBot->>PolicyFile: Require fresh pair authorization
    PolicyFile-->>OrgBot: Authorization unavailable
    OrgBot->>OrgBot: Pause direct capture/export, preserve cache and membership
    Note over SynapseGuard,OrgBot: No leave follows from uncertainty alone
```

When fresh policy returns, retained authorized pairs resume. A fresh known
revocation instead follows the leave sequence above. Readiness or export may
remain unavailable during reconciliation; preserving members does not grant
new room actions. This is distinct from the whole-application login entitlement.
The example's MAS client requires the effective `matrix-access` realm role
while leaving the Keycloak account enabled. Current policy snapshots carry
that entitlement for room and export checks.

The separate `join-policy.json` exists only for initializing exact managed
group rooms. It grants narrowly scoped joins and cannot authorize direct-chat
messages or exports. See the [guard contract](../../src/synapse-policy/README.md),
the [administration guide](../guides/ADMINISTRATION.md), and the
[security guide](../guides/SECURITY.md).
