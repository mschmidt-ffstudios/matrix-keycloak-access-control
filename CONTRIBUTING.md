# Contributing

Start with the [documentation map](docs/README.md) and
[architecture decisions](DECISIONS.md). Use a separate branch and keep changes
scoped so their authorization, configuration and recovery effects can be
reviewed together.

## Development and checks

Python tooling uses Python 3.12 or newer. Portable JavaScript tests use Node.js
24. OrgBot tests use the hash-locked Python dependencies, including its
vodozemac bindings.
Docker/Compose are needed to build or run the full example, not for every unit
test. See [installation](docs/guides/INSTALLATION.md).

```sh
python3 scripts/validate-demo-config.py
node --test src/export-service/*.test.mjs src/policy-sync/*.test.mjs src/provisioner/*.test.mjs
python3 -m unittest discover -s src/org-bot -p 'test_*.py'
```

Install the locked OrgBot requirements as described in the README before
running its tests. Private cross-service/live-target fixtures are intentionally
not required by the public suite. Do not contact a deployment as part of a test
unless its operator has authorized that target and scope.

For authorization changes, exercise both allowed and denied paths, role/group
withdrawal, expired policy, identity collisions and service-account isolation.
For exports, retain explicit scope and completeness metadata; a valid digest
must not turn partial history into a complete claim. Use synthetic messages
and files. Match any new tests to the behavior being changed.

## Documentation and source distribution

Keep English technical guides and both language READMEs consistent with the
actual configuration and APIs. Update editable PlantUML and rendered SVGs
together. Keep examples usable without private files or real addresses.
Document breaking changes, migration ordering, validation and remaining limits.

Keep credentials, generated configuration, device/crypto stores, dumps, logs,
raw exports and sensitive target evidence out of commits. Keep internal
operator code and shareable operating records under `internal/` in the
private Foldfence Studios Gitea repository. Use synthetic accounts and neutral
assets in public examples, including screenshots. GitHub publication uses a
reviewed source snapshot with fresh history after the owner's approval.

Original contributions follow the project's Apache-2.0 license. Preserve
existing notices and the declared licenses of modified third-party derivatives;
consult the [license map](LICENSES/README.md). Do not remove source-offer
materials or upstream notices when changing a build. Follow the
[release guide](docs/guides/RELEASING.md) before distributing a source snapshot
or images.
