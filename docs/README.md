# Documentation map

The Foldfence Studios public documentation describes reusable configuration and source behavior. The public screenshots show synthetic users and a neutral example deployment. Host-specific runbooks, acceptance reports, live screenshots and historical requirements belong under `internal/` in private Gitea. Protect credentials and raw evidence outside Git history. [Project scope and license](../README.en.md)

## Start and operate

| Guide | Covers |
| --- | --- |
| [Installation](guides/INSTALLATION.md) | Prerequisites, generated files, hostnames, TLS, fresh startup and service checks |
| [Administration and roles](guides/ADMINISTRATION.md) | Application entitlement, all room roles, clearance, direct-chat groups, CLI/Postman and revocation |
| [Operations](guides/OPERATIONS.md) | Persistent imports, upgrades, secrets, device state, backup/recovery and destructive reset |
| [Native clients](guides/NATIVE-CLIENTS.md) | Homeserver discovery, Keycloak/MAS login, certificate trust and target-specific acceptance |
| [Export](guides/EXPORT.md) | Authorization, public API, ZIP/NDJSON metadata, files, hashes and completeness limits |
| [Calls](guides/CALLS.md) | Synapse policy gate, JWT service, LiveKit signaling/media, ports and TURN limits |
| [Security](guides/SECURITY.md) | Trust boundaries, management exposure, failure behavior and scanning |
| [Releasing](guides/RELEASING.md) | Apache/third-party boundaries, clean source distribution and release checks |
| [Release notes](RELEASE-NOTES.md) | Public Docker build, release scope and deployment limits |
| [Network zones](architecture/network-zones.md) | Compose networking and a reference Wi-Fi/DMZ topology to implement and verify separately |

## Understand and customize

- [Architecture decisions](../DECISIONS.md)
- [Contributing](../CONTRIBUTING.md) and [private vulnerability reporting](../SECURITY.md)
- [PlantUML diagram catalog](diagrams/README.md), with editable sources and rendered SVGs
- [Configuration and API examples](examples/README.md)
- [Postman collection](../api/postman/README.md)
- [White-label inputs](../config/branding/README.md)
- [Runtime and third-party license notices](../THIRD-PARTY-RUNTIME-NOTICES.md)

Service-level READMEs document environment variables, endpoint contracts and recovery boundaries beside each implementation under `src/` and `deployment/`. Example domains and addresses are placeholders. Validate the actual deployed versions, policies, certificates, devices and network before handing a target to users.
