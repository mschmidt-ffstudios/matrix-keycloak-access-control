# Synapse chat policy guard

This module targets the pinned Synapse **v1.164.2**, in the single-process demo
deployment. It consumes Keycloak policy data from policy-sync. It does not call
Keycloak from an event callback or require a separate authorization engine.

## Group rule

Two enabled, provisioned synthetic users with current Matrix entitlement and
a recognized clearance may start a monitored direct chat when
they share the **same Keycloak group ID** whose group name starts with the
configured `1to1chat.` namespace and has a non-empty suffix. For example,
`1to1chat.orga-a.abteilung-x` authorizes its members mutually. `orga.demo`,
parent membership, a matching name on another group ID, and a room role do not
grant that permission. Current managed-room roles remain independent.

The `/policy/chat-policy.json` snapshot expires at most 60 seconds after the
Keycloak read begins. Missing, malformed, future or expired policy denies new
events and joins. A successful read takes effect when policy-sync publishes
the replacement. Revocation cannot remove content or keys already received.

During managed-room initialization a separate `/policy/join-policy.json`
lists exact admin-created room IDs and current role-authorized people. It
permits only the required membership joins before the final snapshot exists;
it grants neither messages nor exports. It has the same maximum lifetime and
is removed after successful publication or any reconciliation failure. This
also covers Synapse's Admin API, which performs joins as the target user and
therefore invokes the join spam callback.

## Homeserver enforcement

Normal user-created rooms must invite exactly one permitted local person. The
creation hook supplies a private, non-federated room, Megolm encryption,
joined-only history, no guests, fixed power levels, and an OrgBot invitation.
Client input for initial state, room version, alias, room type, federation and
power overrides is replaced or rejected. The Matrix `is_direct` flag and
per-user `m.direct` account data never grant access.

The immutable `m.room.create` event contains a signed pair marker. The stable
private `SYNAPSE_CHAT_POLICY_SECRET` must be preserved across restarts; rotating
it invalidates existing pair markers and requires an explicit room migration.
Room version 11 is selected so a later version's implicit creator privileges
cannot silently change the authorization assumptions.

Creation, invite and join callbacks reject other rooms and third people. The
event callback additionally covers initial state, state changes, membership
kicks/bans, and joins initiated through administrative APIs. Neither human may
remove OrgBot, change the pair, replace encryption, or grant room authority to
another account. New encrypted timeline events and call membership require the
bot to be joined. This proves participation, **not possession of every E2EE
key**; exports still report `partial` until history/key coverage is proven.

Direct-chat exports use `unclassified_direct_chat` as their classification.
Group permission does not assign one of the four managed-room clearance
labels. The registry's rank zero is a metadata ordering placeholder, not a
claim that the conversation is low sensitivity.

The bot is an explicit **third participant** with access to the content its
device can decrypt. A client may therefore display this monitored conversation
as a group room. Do not describe it as private to only the two people.

The module publishes approved room IDs to the dedicated
`/chat-guard/direct-rooms.json` registry. OrgBot and Export Service mount it
read-only and also check the current policy snapshot; registry presence alone
does not grant a revoked pair permission. An initial `pending_bot` record
allows only the bot's authorized invitation handling. The module promotes it
to `ready` after observing real Megolm room state and joined bot membership;
only ready rooms are available for export. Bot departure removes that ready
status. Readiness still does not attest to decryption keys.

A five-second cleanup removes both human memberships and the bot when a
fresh policy confirms the pair is no longer allowed. An unavailable policy
preserves memberships, marks the registry entry `policy_unavailable` and
denies protected actions. The record remains for audit context. Reauthorization
can create a new chat. Departed rooms are not automatically rejoined.

Existing unmanaged rooms have no signed pair marker and cannot be used for new
messages, invitations or joins. Existing history can remain on clients and in
homeserver storage. This module does not promise retrospective deletion.

## Service and upgrade boundaries

Only the exact private policy-admin account is trusted to create the configured
group rooms and perform provisioning operations. OrgBot cannot create rooms or
send chat messages. Public Nginx routes must continue to block Synapse's Admin
API; privileged credentials are an administrative trust boundary.

Upstream labels `check_event_allowed` **experimental**. The simpler spam
callbacks alone omit important paths such as kicking the bot and admin joins.
For this demo the module combines them and pins the server version. Passing
offline tests is not a runtime integration or production security acceptance.
Before deployment/upgrade, run real API tests for creation, invitations, joins,
extra members, bot removal, stale policy, revocation, messages and exports on
the isolated internal instance. Multi-worker deployment is not supported by
the local registry writer; it needs a shared transactional registry first.

Upstream references: [spam callbacks](https://element-hq.github.io/synapse/latest/modules/spam_checker_callbacks.html),
[third-party event callbacks](https://element-hq.github.io/synapse/latest/modules/third_party_rules_callbacks.html),
and the [pinned ModuleApi](https://github.com/element-hq/synapse/blob/v1.164.2/synapse/module_api/__init__.py).

The callback harness and its Synapse integration fixtures are maintained in
the private development test area and are not included in this public source
tree. Follow the [security guide](../../docs/guides/SECURITY.md) for the public
verification boundary and deployment checks. The public OrgBot, policy-sync,
provisioner, and export-service tests can be run from their respective
directories without a homeserver.
