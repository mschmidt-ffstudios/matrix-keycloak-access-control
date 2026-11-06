# Runtime security builds

Build from the repository root. The reviewed target is **Linux amd64**. The
SQLite leaf APK is x86_64; other architectures require their own signed package,
hash lock and compatibility review. Do not substitute an unverified APK.

| Service | Build recipe | Preserved interface |
| --- | --- | --- |
| Keycloak | [Patched Keycloak image](../keycloak/README.md) | Keycloak 26.8.1, optimized PostgreSQL startup and `/auth` |
| MAS | `deployment/mas/Dockerfile` | MAS 1.28.0 with Debian's fixed zlib package and the existing CLI and templates |
| Synapse | `deployment/synapse/Dockerfile` | Synapse 1.164.2, `/start.py`, `/conf`, PostgreSQL and MAS authentication |
| OrgBot | `src/org-bot/Dockerfile` | Python 3.12, unchanged hash-locked E2EE SDK, UID 1000, persistent crypto store |
| Provisioner | `src/provisioner/Dockerfile` | Node 24 on the same patched MAS runtime, UID 1000, existing service-session files |
| PostgreSQL | `deployment/postgres/Dockerfile` | PostgreSQL 17 upstream entrypoint and data directory |
| LiveKit | `deployment/livekit/Dockerfile` | LiveKit 1.13.11 server/configuration, updated locked Go dependencies |

The Python images build wheels in an isolated stage with
`build-requirements.lock`, verify artifact hashes and install without network or
dependency resolution into the runtime. The runtime removes the unused pip
installer, its vendored code and metadata together. Removing only an SBOM or
package metadata would not fix embedded vulnerable code and is not done here.
Builder compilers and package caches are not copied into the runtime.

The Node services do not use the bundled `node:sqlite` module. Compose and the
source images disable that module with `NODE_OPTIONS=--no-experimental-sqlite`;
retain the setting for prebuilt images and inherited Node helper processes.
Native checks must reject CommonJS, dynamic import and getBuiltinModule access
and verify inherited children keep the restriction. Web Storage uses SQLite
independently of that module flag: keep `--experimental-webstorage` and internal
module exposure disabled, reject foreign preload/import/config hooks, and check
that `Storage`, `localStorage` and `sessionStorage` are absent in the main and
inherited processes. See the pinned [Node initialization source](https://github.com/nodejs/node/blob/v24.22.2/lib/internal/process/pre_execution.js).
This is a feature
mitigation: compiled SQLite code remains in the executable and its version is
not declared fixed. Package-only scans may omit such embedded components.
The reviewed Node release also backports security fixes without always changing
bundled dependency version strings; use official release/source evidence and
the actual binary hash when assessing those components.

Python's SQLite library is an explicitly reviewed exception to the stable
Alpine package set: stable 3.24 currently supplies 3.53.4, while the selected
signed `sqlite-libs-3.54.1-r0.apk` fixes the reviewed FTS5 issues. The exact
official Alpine leaf APK is SHA-256 pinned and installed offline with normal
Alpine signature validation. No edge repository is enabled and no transitive
edge packages are installed. Retire the exception when the supported stable
repository provides the fixed version. SQLite runtime tests must inspect the
actual loaded library, not just package metadata.

Both gosu builds use the official 1.20 source commit and checked-in module
overlay (`gosu.go.mod`, `gosu.go.sum`) with x/sys 0.44.0. `go mod verify` and
`go build -mod=readonly` enforce the graph. LiveKit has a separate complete
module lock. All image sources and toolchains are digest/hash pinned; installed
OS security packages still follow the configured signed release repository.
Build receipts therefore need the final immutable image ID and installed
package inventory. A digest-pinned base alone is not a security-update policy.

Before release, scan each **final immutable image ID**, retain the unfiltered
report and SBOM, inspect UNKNOWN findings, and run the relevant native/runtime
checks. An all-zero package report describes that scanner/database snapshot;
it does not prove absence of application bugs or host/network vulnerabilities.
In particular, a copied Node executable also needs its official-source digest,
runtime version and binary hash recorded, since package-file scanning may not
inventory Node core itself. Conditional vendor findings require artifact- and
configuration-specific review rather than global ignore rules.

These recipes do not change the licenses of their dependencies. Synapse remains
AGPL-3.0-or-later or separately commercially licensed; the project Apache 2.0
license does not replace that boundary. Preserve upstream notices and provide
corresponding source where required. See
[third-party runtime notices](../../THIRD-PARTY-RUNTIME-NOTICES.md).

Primary references: [Alpine SQLite package](https://pkgs.alpinelinux.org/package/edge/main/x86_64/sqlite-libs),
[Synapse source](https://github.com/element-hq/synapse/tree/v1.164.2),
[gosu source](https://github.com/tianon/gosu/tree/1.20),
[Go OpenPGP advisory](https://pkg.go.dev/vuln/GO-2026-5932).
