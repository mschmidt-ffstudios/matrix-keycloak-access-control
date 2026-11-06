# Third-party runtime notices

Foldfence Studios maintains this project. Repository-authored files use Apache
License 2.0 where no more specific notice applies. Runtime images, upstream
source, packages, interface assets and native applications retain their
respective terms. Element's commercial license identifiers refer to separate
license options from the rights holder. [Project license](LICENSE) ·
[Project attribution and warranty terms](NOTICE) · [License map](LICENSES/README.md)

The table records selected direct components and versions from the checked-in
deployment. Generate a software bill of materials (SBOM) from the exact images
and lockfiles selected for redistribution to identify the licenses and notices
of base-image packages, build tools, language dependencies and transitive
components.

| Component | Source/default version | License and notice boundary |
| --- | --- | --- |
| Matrix Authentication Service | Digest-pinned MAS 1.28.0 base with the Debian zlib package overlay | MAS uses `AGPL-3.0-only OR LicenseRef-Element-Commercial`. The same runtime is the Provisioner base. Preserve the applicable MAS notices and the separate Debian package notices, including the copyright file installed by the zlib overlay. [Pinned MAS source and licenses](https://github.com/element-hq/matrix-authentication-service/tree/v1.28.0) · [MAS build](deployment/mas/README.md) · [Debian package](https://packages.debian.org/sid/zlib1g) |
| Synapse | Synapse 1.164.2 rebuilt on an Alpine/Python runtime with upstream launch files | Synapse uses `AGPL-3.0-or-later OR LicenseRef-Element-Commercial`. The build retains both upstream license texts. Evaluate the applicable terms for the runtime, the policy module and the exact combination when distributing or offering the service. [Pinned upstream source and license](https://github.com/element-hq/synapse/tree/v1.164.2) · [Synapse build](deployment/synapse/README.md) |
| Element Web | Upstream v1.12.33 base with the contact-picker derivative | Upstream describes AGPL, GPL and separate commercial options. Apply the relevant file-level notices. The project-authored contact filter and patcher use `AGPL-3.0-only`. The inherited test retains its upstream notices and license choices. [Pinned upstream licensing](https://github.com/element-hq/element-web/tree/v1.12.33) · [Derivative build and source offer](deployment/element/README.md) |
| Keycloak | Digest-pinned upstream 26.8.1 base with the repository's SQLite and Guava overlay | Keycloak, Guava and failureaccess use Apache License 2.0. SQLite source is public domain. Preserve upstream notices and the licenses of packages in the base image. [Keycloak license](https://github.com/keycloak/keycloak/blob/26.8.1/LICENSE.txt) · [Guava license](https://github.com/google/guava/blob/v33.7.2/LICENSE) · [SQLite copyright](https://www.sqlite.org/copyright.html) · [Build notes](deployment/keycloak/README.md) |
| PostgreSQL | PostgreSQL 17 Alpine base | PostgreSQL License. [Official license text](https://www.postgresql.org/about/licence/) and the [repository's image build notes](deployment/postgres/README.md). Alpine packages remain separately licensed. |
| Nginx | Nginx 1.30.6 Alpine base | Nginx uses the 2-clause BSD license. Alpine and installed package notices apply separately. [Pinned upstream license](https://github.com/nginx/nginx/blob/release-1.30.6/LICENSE) |
| LiveKit server | Hash-pinned v1.13.11 source rebuilt with the checked-in Go module overlay | Apache License 2.0. The build includes upstream `LICENSE` and `NOTICE` under `/usr/share/doc/livekit/`. [Pinned upstream notice](https://github.com/livekit/livekit/blob/v1.13.11/NOTICE) · [LiveKit build](deployment/livekit/README.md) |
| Element LiveKit JWT service | Digest-pinned `ghcr.io/element-hq/lk-jwt-service:0.9.0` | `AGPL-3.0-only` per the pinned package manifest. [Manifest](https://github.com/element-hq/lk-jwt-service/blob/v0.9.0/Cargo.toml) · [License](https://github.com/element-hq/lk-jwt-service/blob/v0.9.0/LICENSE) |
| gosu | Release 1.20 rebuilt from a pinned commit for the PostgreSQL and Synapse images | Apache License 2.0. Both builds retain the upstream license at `/usr/share/doc/gosu/LICENSE`. Preserve it and relevant dependency notices with any binary distribution. [Pinned gosu license](https://github.com/tianon/gosu/blob/29b939aa5bd4dc588af765b8248f6e39150d2565/LICENSE) · [Build lock](deployment/security/gosu-source.lock) |
| Node.js | Digest-pinned Node.js bases and the binary copied into the Provisioner | Preserve Node.js's license and bundled third-party notices. The Provisioner copies the upstream license file into its image. [Node.js licensing source](https://github.com/nodejs/node/blob/main/LICENSE) · [Provisioner build](src/provisioner/Dockerfile) |
| `matrix-nio[e2e]` | 0.26.0, hash-locked in `src/org-bot/requirements.lock` | Internet Systems Consortium (ISC) license. The optional E2EE and other dependencies retain their own terms. [Upstream 0.26.0 license](https://github.com/matrix-nio/matrix-nio/blob/0.26.0/LICENSE.md) |
| Element X native client | Installed separately | The app publisher and store provide the applicable license for the separately distributed mobile application. |

## Source, binaries and network use

The public release target is a GitHub source snapshot. Before distributing any
built image, check the exact artifact for its upstream notices and package
licenses. When distributing object code under the AGPL, provide Corresponding
Source as permitted by section 6 for that distribution method. Tie it to the
exact image, including changes, applicable dependencies and the scripts and
inputs required by the license. Preserve each component's applicable license
and attribution in the distribution materials. [AGPL-3.0 text](LICENSES/AGPL-3.0-only.txt) ·
[Release guide](docs/guides/RELEASING.md)

For a modified AGPL-covered program offered over a network, verify that its
users can obtain the Corresponding Source for the version they interact with
where section 13 applies. The Element Web build creates an archive from the
patched source and adds a link in the contact dialog. Verify the link, archive
contents and build inputs against the actual served version. Record equivalent
source and attribution materials for each covered component in the release.
[AGPL-3.0 text](LICENSES/AGPL-3.0-only.txt) · [Element source-offer workflow](deployment/element/README.md)
