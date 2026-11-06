# Postgres security derivative

The three demo databases use one image derived from the digest-pinned upstream Postgres 17 Alpine image. It updates installed Alpine packages and replaces the bundled `gosu` with the same source-built, statically linked binary used by the Synapse derivative.

The gosu 1.20 source commit, Go toolchain and module overlay are pinned in
`deployment/security`. The overlay updates x/sys to 0.44.0; `go mod verify` and
`go build -mod=readonly` check the complete graph. The original PostgreSQL
entrypoint, major version, database format and data-directory path are retained.
The entrypoint starts as root to set ownership on the persistent data directory,
then uses `gosu postgres` so the database server runs as the PostgreSQL user.

Build with the repository root as context:

```sh
docker build -f deployment/postgres/Dockerfile -t foldfence-demo/postgres:17-alpine-security1 .
```

Scan the built image before using it. Package updates can add or change findings; this derivative is not a blanket waiver for upstream advisories.

Before activation, perform a logical backup and a full isolated restore with
the final immutable image. Preserve the old image ID and pre-upgrade checkpoint
for rollback. A successful `pg_isready` alone does not prove recovery of data,
roles, ownership or application authentication. See
[runtime security builds](../security/README.md).

The image is derived from the PostgreSQL distribution, which uses the
PostgreSQL License. The rebuilt gosu binary includes its Apache-2.0 license
under `/usr/share/doc/gosu/`. Alpine packages and the other runtime images
keep their own license and notice requirements. [Third-party runtime notices](../../THIRD-PARTY-RUNTIME-NOTICES.md)
