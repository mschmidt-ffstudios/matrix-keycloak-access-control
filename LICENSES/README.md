# License map

Foldfence Studios maintains this project. Repository-authored work uses
Apache License 2.0 where no more specific notice applies. Retain the existing
copyright and attribution notices for individual contributions and upstream
components. [Apache License 2.0](../LICENSE) · [Project attribution and warranty terms](../NOTICE)

| Scope | License identifier | Notes |
| --- | --- | --- |
| Repository-authored files without another notice | `Apache-2.0` | See the root license. |
| Neutral example assets in `config/branding/assets/` and diagrams in `docs/diagrams/` | `Apache-2.0` | These are repository-authored sample graphics. |
| Keycloak UI captures in `docs/screenshots/` | Repository documentation with upstream interface and marks | The captures show Keycloak's interface using the generated synthetic demo realm. Underlying Keycloak interface assets and marks retain their respective terms. |
| `src/element-policy/FoldfenceContactPolicy.ts` and `deployment/element/apply-contact-policy.py` | `AGPL-3.0-only` | Modified Element Web integration code. The full license text is in [`AGPL-3.0-only.txt`](AGPL-3.0-only.txt). See [`deployment/element/README.md`](../deployment/element/README.md) for the build and source-archive procedure. |
| `src/element-policy/FoldfenceInviteDialog.test.tsx` | `AGPL-3.0-only OR GPL-3.0-only OR LicenseRef-Element-Commercial` | This file retains the copyright and license notice inherited from upstream Element. The AGPL and GPL option texts are included as [`AGPL-3.0-only.txt`](AGPL-3.0-only.txt) and [`GPL-3.0-only.txt`](GPL-3.0-only.txt). |
| Direct runtime images and other upstream components | Component-specific | The repository license does not relicense them. See [`THIRD-PARTY-RUNTIME-NOTICES.md`](../THIRD-PARTY-RUNTIME-NOTICES.md). |

The AGPL and GPL texts are reproduced verbatim from the pinned Element Web
source archive (`v1.12.33`, `LICENSE-AGPL-3.0` and `LICENSE-GPL-3.0`).
`LicenseRef-Element-Commercial` identifies a separate commercial license
option available from its rights holder. The public GitHub deliverable is a
reviewed source snapshot. Redistribution of binaries and operation of modified
AGPL-covered programs over a network carry their own obligations, including
access to the corresponding source where the applicable license requires it.
Use the exact component version, build inputs and resulting image when
reviewing those obligations. [Release guide](../docs/guides/RELEASING.md) ·
[AGPL-3.0, sections 6 and 13](AGPL-3.0-only.txt)
