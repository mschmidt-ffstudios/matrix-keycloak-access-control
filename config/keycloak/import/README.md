# Keycloak realm import

The realm JSON carries users, clients, roles, protocol mappers, and test credentials. The `security_clearance` field is a Keycloak User Profile attribute, so Keycloak 26 needs a separate managed-profile configuration; it is not part of the realm-import representation. `config/keycloak/user-profile.json` defines that attribute as admin-only and restricts it to the four declared clearance values.

The MAS upstream OIDC client requests only `openid profile`. `config/keycloak/mas-client-scopes.json` defines the `preferred_username` and `name` claims MAS needs to create a Matrix localpart and display name, and assigns the `profile` scope to the `matrix-authentication-service` client. Keeping that minimal scope in both the realm import and the reconciliation step prevents Keycloak from rejecting the login request as `invalid_scope` without requesting email data. Synthetic Keycloak users receive non-routable `@foldfence-demo.invalid` email placeholders and split first/last names so Keycloak's first-login profile action does not block the demo; MAS does not request or import those email values. `scripts/reconcile-demo-keycloak-users.py` applies the scope and managed profile through Keycloak's Admin REST API, then verifies the synthetic users' clearance values before policy-sync starts.

`scripts/prepare-demo.py` builds one native Keycloak realm JSON in the private
directory selected by `DEMO_GENERATED_DIR` (default:
`internal/generated/keycloak/import/foldfence-demo-realm.json`) from the tracked
user seed, room policy, brand settings and private environment values. The
generated import is mounted at `/opt/keycloak/data/import`; Compose starts
Keycloak with `--import-realm`.

This demo defaults to one example realm (`foldfence-demo`) as the identity and
policy source. The realm contains 20 ordinary synthetic users and two isolated
export-test users, their clearance attribute,
the per-room role-container client, the MAS upstream OIDC client, the export
portal client and a least-privilege policy-sync service account. MAS creates
the Matrix identities and links them to Keycloak subject IDs. The example
implements one realm. A second realm and broker flow would be an extension.

Keycloak imports a realm only if that realm does not already exist. After
changing users or roles, reset only this demo's named volumes with the guarded
reset script, or apply changes through the Keycloak Admin API. Re-running
`--import-realm` does not overwrite an existing realm.

Keep generated realm JSON and passwords in the Git-ignored target directory
under `internal/` and the private `.env` file. The source release includes the
tracked seeds and templates used to generate the import locally.
