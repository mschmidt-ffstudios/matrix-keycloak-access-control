# Release notes

## First public source release

The Foldfence Studios source release packages a Docker Compose example of
Matrix with Keycloak sign-in, policy-controlled rooms and direct chats, encrypted messages and files,
LiveKit calls, and scoped OrgBot export. The installation generates its own
configuration and synthetic accounts from the public source tree.

### Docker startup

The default build uses the repository's Keycloak 26.8.1 derivative on Linux
`amd64`. It builds SQLite 3.54.1 as an RPM and verifies the Guava 33.7.2-jre
and failureaccess 1.0.3 JARs against pinned SHA-256 values. It preserves
Keycloak's PostgreSQL integration and `/auth` path. The build recipe documents
its pinned inputs and the difference between an input digest and the identity
of a built image. Compose selects the upstream MAS base and LiveKit JWT image
by digest. The local MAS and Provisioner builds share the same patched MAS
runtime. [Installation](guides/INSTALLATION.md) ·
[Keycloak build](../deployment/keycloak/README.md)

The source package includes the browser export portal. Generated proxy
configuration keeps the canonical HTTPS port reachable inside the Compose
network, and MAS template preparation works when the Docker CLI runs through
`sudo`. Preparation builds MAS before generating keys and copying login
templates. Startup builds the local image graph before creating services. The
generator sets the permissions required by non-root containers. The
provisioner initializes owner-only session volumes and can secure the known
older volume mode while preserving a valid OrgBot session.

### Verification

On 6 November 2026, a fresh Linux `amd64` installation with Compose 2.40.3
and empty volumes passed all eight startup endpoint checks. The public
source passed 66 Node tests, 60 OrgBot tests and configuration validation.
Gitleaks and Trivy found no secrets in the tested source snapshot.

End-to-end checks covered Keycloak/MAS sign-in, scoped administration,
room and direct-chat denial, and removal and restoration of access when a
room role changed. The group and 1:1 export run recovered two encrypted
attachments byte-for-byte and verified the export authorization responses.
Two authorized participants joined the LiveKit SFU over WebSocket without
publishing media tracks. This exercised authorization and signaling.

Trivy 0.76.1 scanned the 12 distinct images used by the 15 Compose roles
and produced a CycloneDX SBOM for each. It used the vulnerability database
from 6 November 2026 at 09:50 UTC and the Java database from 01:10 UTC.
The unfiltered reports contain 0 critical, 5 high, 105 medium and 42 low
rows across the images. The five high rows cover the three CVEs below.
The scanner still reports them against the Debian 13
or Red Hat RPM feed. The separate, image-specific findings review records
the fixed zlib payload and the Keycloak trust-store remediation. Its evidence
accounts for all five High rows on these exact image IDs. The separate
Dockerfile source scan originally reported 23 configuration findings.

MAS and the Provisioner now use Debian's fixed `zlib1g` package
`1:1.3.dfsg+really1.3.2-4` for `CVE-2026-76844` and `CVE-2026-85091`. The
build verifies the package checksum, removes the prior library and checks
the installed files and metadata. Runtime readback from both images matched
the signed Debian package and confirmed that the active library link points
to the corrected file. Debian tracks the fixed package in sid while its
trixie advisory still lists a vulnerable version.
[MAS build](../deployment/mas/README.md) ·
[Debian zlib tracker](https://security-tracker.debian.org/tracker/source-package/zlib)

The Keycloak build passed the SQLite ABI, RPM, FTS5 and NSS checks. It now
also requires Red Hat's fixed CA package and verifies the exact vendor store
against the JVM's default JSSE accepted issuers. The freshly built image
and running test stack both rejected the affected certificate paths. The
raw Java RPM match for `CVE-2026-91403` remains in the scan record.
[Red Hat CVE record](https://access.redhat.com/security/cve/cve-2026-91403) ·
[Red Hat CA package erratum](https://access.redhat.com/errata/RHSA-2026:88459)

The later attribution audit added LiveKit's upstream `NOTICE` and the gosu
license to the relevant build recipes. Isolated rebuilds verified all three
notice files against the pinned sources and matched the checked binaries and
launchers byte-for-byte with the earlier builds. The scan counts above refer
to the earlier image IDs. Record fresh exact-image evidence before distributing
rebuilt images. [Runtime notices](../THIRD-PARTY-RUNTIME-NOTICES.md)

### Dockerfile source-scan follow-up

The follow-up reduced the unfiltered Dockerfile scan from 23 to 14 findings,
with no new findings. LiveKit and Synapse now default to unprivileged users.
Synapse's networkless renderer still runs as root to set ownership on persistent
files. The signed SQLite APK installs offline without a cache, wheel and
package checksum verification stays in place, and Provisioner no longer
duplicates token-file paths in its image environment. Compose now checks Export
Service liveness and LiveKit SFU readiness before reporting startup success.

The remaining findings cover two intentional image startup privileges, one
Compose-provided build context, and eleven checks for Dockerfile-level health
probes. The Nginx master reads the owner-only TLS key and starts unprivileged
workers. PostgreSQL initializes volume ownership and drops to the database user
before starting the server. The Provisioner builds against the local patched
MAS service image through a named Compose context. The supported Compose start
uses service health, bootstrap checks and TLS endpoint checks. No scanner rule
was suppressed. [Installation](guides/INSTALLATION.md) ·
[Nginx](../deployment/nginx/README.md) · [PostgreSQL](../deployment/postgres/README.md)

### Source and deployment scope

- New installations generate fresh credentials and their own TLS configuration.
  Existing installations should back up their databases and persistent device
  state and review the upgrade and rollback steps. [Operations](guides/OPERATIONS.md)
- Review the actual image inventory and security findings for the selected
  build. A package scan is limited to the scanned artifacts and its advisory
  database. [Release checks](guides/RELEASING.md)
- OrgBot export coverage depends on the events and decryption keys available
  to its device. Native client and camera/microphone media acceptance are
  specific to the deployment and devices. [Export](guides/EXPORT.md) ·
  [Native clients](guides/NATIVE-CLIENTS.md) · [Calls](guides/CALLS.md)
- The GitHub candidate contains the reviewed public source. The private
  Gitea history, `internal/` files and live deployment materials are separate.
  Approval covers the exact source snapshot. Image redistribution requires
  its own license and verification checks. [Releasing](guides/RELEASING.md)

The reference deployment is provided as is, without warranties under the
applicable licenses. Each operator is responsible for qualifying its own
production environment. Project-authored source uses Apache License 2.0.
Upstream components and explicitly marked derivatives retain their own terms.
[License](../LICENSE) · [Runtime notices](../THIRD-PARTY-RUNTIME-NOTICES.md)
