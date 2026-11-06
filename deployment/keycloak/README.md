# Patched Keycloak image

The Compose service builds this Keycloak 26.8.1 derivative for `linux/amd64`.
The build keeps the upstream Keycloak distribution and its runtime RPM
database. It replaces Guava with 33.7.2-jre, includes failureaccess 1.0.3, and
installs SQLite 3.54.1 as a shared-library RPM. The image has the upstream
Keycloak entry point and runs as user 1000.

The Dockerfile pins the frontend, Keycloak base, and UBI builder by digest. It
checks the Guava and failureaccess downloads with SHA-256 and checks both the
SQLite source archive and the amalgamation with SHA3-256. UBI supplies the
compiler and RPM tools during the build. Their repository can advance, so a
rebuild must be tested and scanned by its own image ID before deployment.

The build compares SQLite exports and ELF hardening against the pinned vendor
library, exercises the FTS5 negative-row-ID regression and JSON functions, and
verifies the installed RPM and its source identity. The three permitted removed
exports are undocumented debugging globals. It also checks actual NSS
initialization and database creation with the replacement library. The build
tools, NSS probe, and SQLite CLI are absent from the resulting runtime. Java
uses the RHEL system CA store supplied by the pinned base. The build requires
the fixed `ca-certificates` RPM from RHSA-2026:88459 and verifies the exact CA
store and default JSSE anchors offline. The two affected roots and the known
cross-signed intermediate must be absent. Trust settings or mounts added by an
operator require separate runtime review. [Red Hat CA erratum](https://access.redhat.com/errata/RHSA-2026:88459) · [Red Hat Java advisory](https://access.redhat.com/security/cve/cve-2026-91403)

## Build and run

From the repository root, build the Keycloak service with the same Compose
environment used by the [installation guide](../../docs/guides/INSTALLATION.md).
To build just the image independently:

```sh
docker build --platform linux/amd64 --target runtime \
  --file deployment/keycloak/Dockerfile \
  --tag matrix-keycloak-demo/keycloak:26.8.1-security2 .
```

Compose starts it using `start --optimized --import-realm`. The optimized build
uses PostgreSQL, the `/auth` HTTP path, health enabled, and metrics disabled.
Keep build-time options consistent when supplying a deployment override.
Record the actual image ID, test results, SBOM, and scan using the
[release guide](../../docs/guides/RELEASING.md).

The Keycloak distribution and Guava artifacts retain their Apache 2.0 terms.
SQLite is public domain. See the
[runtime inventory](../../THIRD-PARTY-RUNTIME-NOTICES.md).
