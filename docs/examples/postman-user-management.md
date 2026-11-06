# Example: Postman user management

Use Postman only with an approved Keycloak Admin REST environment and synthetic accounts. Keep the collection free of credentials; store tokens in a private environment or secret store and clear them after use. Do not export a populated environment or send it to a shared workspace.

For routine demo-user operations, the repository CLI is the supported path:

~~~sh
python3 scripts/manage-demo-user.py \
  --base-url https://matrix.example.invalid/auth --realm foldfence-demo create \
  --username demo-alex --display-name 'Alex Example' \
  --clearance intern --role room.pummel.writer --matrix-access
~~~

The Matrix entitlement is explicit. Omitting the flag leaves a new user without Matrix sign-in access. Room roles and clearance are separate, and export access requires its own role. Revoke or update only the intended synthetic user and verify the policy result after synchronization.

[Public Postman collection](../../api/postman/README.md) · [Administration and revocation](../guides/ADMINISTRATION.md)
