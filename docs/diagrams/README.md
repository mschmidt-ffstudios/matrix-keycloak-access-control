# Architecture diagrams

These PlantUML diagrams describe the logical architecture and selected
configuration paths. They use generic service names and contain no deployment
hostnames, credentials, or private addresses. They describe source behavior;
they do not establish that a particular deployment is running or healthy.

| Diagram | Source | Rendered SVG |
| --- | --- | --- |
| Repository and runtime overview | [repository-overview.puml](repository-overview.puml) | [repository-overview.svg](repository-overview.svg) |
| From Keycloak grants to Matrix actions | [keycloak-policy-flow.puml](keycloak-policy-flow.puml) | [keycloak-policy-flow.svg](keycloak-policy-flow.svg) |
| Matrix login entitlement | [authentication-and-entitlement.puml](authentication-and-entitlement.puml) | [authentication-and-entitlement.svg](authentication-and-entitlement.svg) |
| Room and direct-chat policy | [room-and-direct-policies.puml](room-and-direct-policies.puml) | [room-and-direct-policies.svg](room-and-direct-policies.svg) |
| OrgBot and protected export | [orgbot-and-export.puml](orgbot-and-export.puml) | [orgbot-and-export.svg](orgbot-and-export.svg) |
| MatrixRTC call authorization | [matrixrtc-call.puml](matrixrtc-call.puml) | [matrixrtc-call.svg](matrixrtc-call.svg) |

## Render locally

With PlantUML, Java, and Graphviz installed, render SVGs alongside the sources
from the repository root with:

```sh
plantuml -tsvg docs/diagrams/*.puml
```

This runs the local PlantUML renderer and does not upload diagrams to a public
rendering service. If PlantUML is not installed, install it from a trusted
package source or use a verified local tool.

## How to read the arrows and boundaries

- Solid arrows show configured request, policy, or data paths in the source.
  They do not prove that a remote deployment is healthy.
- Dashed arrows mark preparation/configuration links or the optional MatrixRTC
  path, as labeled. Token authorization is
  checked by Synapse against a fresh policy snapshot and current room state;
  only fixed internal destinations receive forwarded requests.
- The Keycloak `matrix-access` diagram describes a conditional entitlement
  check for the MAS client. Its missing-role branch denies Matrix login while
  allowing the Keycloak account to remain enabled. Verify the flow binding and
  effective-role behavior for each deployment.
- Managed-room access requires Matrix entitlement, sufficient clearance and one
  explicit room role. Eligible direct-chat users share an exact Keycloak group ID
  whose group name has the configured `1to1chat.` prefix; ordinary groups do
  not grant access.
- OrgBot is an E2EE room participant with power level 0. Its export describes
  content its own device observed and decrypted. An archive can be produced
  while still reporting `partial`; it does not prove complete history.
- The call diagram covers token authorization and signaling/media routes. It
  does not establish client compatibility or successful audio/video transport.
  Native mobile clients, Matrix ringing, NAT, and TURN behavior require
  separate testing.

Check the PUML source and these boundaries when updating diagrams. Follow
[installation](../guides/INSTALLATION.md), [security](../guides/SECURITY.md),
and the [network-zone model](../architecture/network-zones.md) for deployment
guidance.
