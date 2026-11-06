# Synapse security derivative

This image keeps Synapse **v1.164.2**, its exact upstream `/start.py` launcher and
configuration templates, using a hash-locked CPython 3.13 Alpine runtime. The
upstream source archive, Python image and Go toolchain are immutable pins.

Build with the repository root as context:

```sh
docker build -f deployment/synapse/Dockerfile -t foldfence-demo/synapse:v1.164.2-security1 .
```

`requirements.in` selects PostgreSQL, OIDC, URL previews, Redis, Sentry and cache
memory support; `requirements.lock` pins their complete dependency graph and
artifact hashes. Psycopg2 and zope-interface build in a disposable stage. MAS
authentication is part of this Synapse release and remains available. SAML,
LDAP and optional tracing extras are not configured by this deployment and are
not included in this image; enabling them requires a new dependency/security
review rather than assuming vendor-all-extras compatibility.

Runtime libpq/jemalloc are installed separately; build compilers, wheel caches
and pip are absent from the final image. A signed, hash-pinned SQLite 3.54.1
leaf package fixes the reviewed native SQLite issues. The gosu source/module
locks live in `deployment/security`. See the detailed
[runtime build rules](../security/README.md), including the tested Linux amd64
architecture and SQLite stable-repository exception.

The existing Compose command `run --config-path=/data/homeserver.yaml` remains
supported. The server image defaults to UID 991 and GID 1000. The networkless
Compose configuration job runs as root to set ownership on its shared volumes.
Preserve the data directory, signing key, database and configured
user/group behavior during an update; verify restored-database startup and MAS
login before switching traffic. Scan every final image and retain its SBOM.

Synapse is available under AGPL-3.0-or-later or a separate commercial license.
The rebuilt gosu binary includes its Apache-2.0 license under
`/usr/share/doc/gosu/`. See the pinned upstream license and the distribution
requirements in [third-party runtime notices](../../THIRD-PARTY-RUNTIME-NOTICES.md).
