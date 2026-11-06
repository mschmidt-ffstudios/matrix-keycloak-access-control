# Releasing the public source

Foldfence Studios maintains this open-source demonstration. The project code
and documentation use Apache-2.0 where stated. Files with other license
identifiers, third-party source, images and dependencies retain their own
terms. The work is provided as is and without warranty under the applicable
license terms. Read the license and notices before redistributing source or
built images. [License](../../LICENSE) · [Notice](../../NOTICE) · [Runtime notices](../../THIRD-PARTY-RUNTIME-NOTICES.md)

Private Gitea is the working repository. Use `internal/` there for deployment
profiles, transfer scripts, test fixtures and installation-specific evidence.
Keep credentials, private keys and raw exports in separately protected storage.
Prepare the GitHub release from an independent, reviewed source snapshot.

## Choose and review the source

1. Select a source commit in private Gitea and review its file list. Include `config/`,
   `deployment/`, `src/`, `scripts/`, `web/`, `api/`, `docs/`, both language READMEs,
   architecture decisions, release notes, license texts and notices. Keep executable script
   permissions. Include `.env.example`, never a populated environment.
2. Exclude the entire `internal/` tree, `.git` history, private profiles,
   generated configuration, credentials, keys, crypto/device stores, raw
   exports, backups, logs and target-specific screenshots/evidence. Review
   file contents as well as names. Ignoring a file now does not erase it from
   earlier Git history.
3. Export the candidate file list to an empty directory. Build and review
   the exported bytes before initializing the public Git history. The public tree
   must work independently of private transfer helpers and acceptance fixtures,
   and its documents must read correctly from that source snapshot.
4. Check all local links and heading anchors; render the PlantUML sources and
   compare the SVGs. Keep source and rendered diagrams together. Example URLs
   use reserved domains and do not claim that a deployment is running.
5. Scan the complete snapshot for credentials, private keys, target addresses,
   account data and accidental generated state. An exact known-credential
   comparison and pattern scan complement human review. Check the visible
   contents and metadata of screenshots as well. The README's
   Keycloak captures use an isolated realm generated from the public demo seed.

## Validate the public copy itself

Run from the source snapshot root:

```sh
python3 scripts/validate-demo-config.py
node --test src/export-service/*.test.mjs src/policy-sync/*.test.mjs src/provisioner/*.test.mjs
RELEASE_TEST_VENV="$(mktemp -d)"
python3 -m venv "$RELEASE_TEST_VENV"
"$RELEASE_TEST_VENV/bin/python" -m pip install --require-hashes -r src/org-bot/requirements.lock
"$RELEASE_TEST_VENV/bin/python" -m unittest discover -s src/org-bot -p 'test_*.py'
plantuml -tsvg docs/diagrams/*.puml
```

Use Node.js 24 and Python 3.12 or newer with `venv` support. The virtual
environment keeps the locked OrgBot test dependencies outside the snapshot.
Rendering needs a local PlantUML/Java installation and
Graphviz for diagrams that use it. Private fixture tests are not part of these
public commands. A passing unit/configuration suite is not live acceptance.

To substantiate a tested demo installation, build and start from the public
snapshot on an isolated Linux `amd64` target. Use a separate Compose project, fresh volumes,
synthetic accounts, and local test DNS/TLS settings. Start through
`scripts/start-demo.sh` using the public default Compose model so the test
exercises the patched Keycloak build. Record the resolved image references
without writing expanded environment values to logs. Verify discovery, fresh Keycloak/MAS login,
application and room revocation, direct-chat isolation, E2EE files, export
scope/coverage and the intended client/call network path. Check that the actual
Keycloak container uses the built image, starts in optimized mode and exposes
OIDC discovery under `/auth`. Run the Keycloak component checks and record the
result. Record separately any native client and media tests that were actually
performed. [Installation](INSTALLATION.md) · [Keycloak build](../../deployment/keycloak/README.md)

## Images, licenses and security evidence

Retain upstream notices and any required corresponding source for the exact
versions distributed. The Element Web derivative remains AGPL-3.0-only.
Follow its build and source-offer instructions when distributing the image
or operating the modified web client for users. [Element Web build](../../deployment/element/README.md)

Record source commit, final image IDs/registry digests, runtime versions,
lockfiles, SBOMs, scanner/database versions and scan timestamps. Builds that
install packages from signed repositories can change as those repositories
update. Scan the exact final images and retain the raw reports, inventory and
disposition of findings. Summarize the scope, results and known limits in the
[release notes](../RELEASE-NOTES.md). [Security](SECURITY.md)

Publication to GitHub requires the repository owner's explicit approval of the
reviewed snapshot and release notes. Initialize fresh Git history from the
approved source. Image redistribution and deployment remain separate
decisions. [Vulnerability reporting](../../SECURITY.md) ·
[Operational scope](OPERATIONS.md#demo-and-production-boundaries)

## Activate private vulnerability reporting

After the owner's publication approval, enable **Private vulnerability
reporting** under **Settings → Advanced Security** on the approved public
GitHub repository. If creating a new repository, enable reporting on the
empty public repository before pushing the approved source. The repository
owner can also activate and verify the setting with an authenticated GitHub
CLI session with repository administration rights.

```sh
# Substitute the exact repository approved for publication.
RELEASE_REPOSITORY='OWNER/REPOSITORY'
gh api --method PUT "repos/$RELEASE_REPOSITORY/private-vulnerability-reporting"
gh api "repos/$RELEASE_REPOSITORY/private-vulnerability-reporting" --jq '.enabled'
```

Require `true`. Once the approved source is present, confirm that
**Report a vulnerability** opens the private form for a signed-in
non-administrator. The public snapshot includes the form and the reporting
instructions. Confirm that at least one Foldfence Studios maintainer can
access and monitor private advisories before announcing the release. Triage
reports in the private advisory and coordinate disclosure with the reporter.
[Configure GitHub reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository) ·
[Reporting API](https://docs.github.com/en/rest/repos/repos#enable-private-vulnerability-reporting-for-a-repository)
