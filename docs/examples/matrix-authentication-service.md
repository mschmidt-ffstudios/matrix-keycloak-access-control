# Example: Matrix sign-in through MAS

MAS brokers the Matrix browser sign-in to the configured OpenID Connect identity provider. The example flow is:

~~~text
Matrix client -> MAS authorization -> Keycloak sign-in
              <- Matrix authorization response <-
~~~

For the Matrix login flow, the Keycloak user must have the `matrix-access` realm role. A missing role denies MAS browser login while the Keycloak account remains enabled. The public seed explicitly grants this role to its synthetic test users. Additional human accounts need a separate grant. MAS local password login is disabled for human browser login. Enter the user's password at the identity provider.

The Matrix user is linked to the Keycloak subject so the same person retains a stable account. Keep the issuer, subject, client IDs, public URLs, and redirect URIs consistent. Never place client secrets or generated MAS signing/encryption keys in a public client or example file.

See [Administration](../guides/ADMINISTRATION.md), [Native clients](../guides/NATIVE-CLIENTS.md), and [Installation](../guides/INSTALLATION.md).
