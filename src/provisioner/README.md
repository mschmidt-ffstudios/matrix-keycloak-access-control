# Matrix identity provisioner

This private Compose service bridges Keycloak's user records into Matrix Authentication Service (MAS). It is a long-running service, not a one-shot Synapse registration job.

At startup it runs MAS database migrations and configuration sync, looks up each synthetic seed user in Keycloak, and registers the corresponding MAS account with an explicit upstream-provider/Keycloak-subject mapping. It also creates the private Synapse policy administrator and the non-admin OrgBot account without a local password. A local state file makes these operations repeatable and detects an unexpected identity change instead of silently relinking an account.

The MAS client can use a dedicated Keycloak Browser flow that preserves the
configured Browser Methods and then denies login when the effective
`matrix-access` realm role is absent. The Keycloak user can remain enabled when
that Matrix entitlement is absent. A matching entitlement check in policy
snapshots lets room and export services apply the same current grant. Configure
MAS local password authentication according to the deployment guide; the
sample deployment disables it.

The service validates and reuses the Synapse administrator compatibility token on startup and every three minutes. MAS v1.28.0's [`issue-compatibility-token` implementation](https://github.com/element-hq/matrix-authentication-service/blob/v1.28.0/crates/cli/src/commands/manage.rs#L415-L425) creates the compatibility session and access token with no expiry value; the documented [`compat_token_ttl`](https://github.com/element-hq/matrix-authentication-service/blob/v1.28.0/docs/reference/configuration.md#L1095-L1103) applies to compatibility access tokens when refresh tokens are supported, not to this CLI-issued credential. The provisioner reissues only when Synapse specifically reports `401 M_UNKNOWN_TOKEN`. It verifies exact Matrix `whoami` user/device identity and requires HTTP 200 from Synapse's protected [user Admin API](https://github.com/element-hq/synapse/blob/v1.164.2/synapse/rest/admin/users.py#L197-L234), which checks the requester's administrator authority. The response's `admin` field describes the target account's Synapse database flag and does not represent the delegated MAS administrator scope. Every successful check also updates a private verification marker; Compose health becomes unhealthy if that verification is older than five minutes. Other errors fail closed. The admin token remains in the private `mas-bootstrap-data` volume shared with policy-sync.

The provisioner separately provisions or reuses a stable, non-admin OrgBot compatibility session, verifies exact Matrix `whoami` user/device identity and a Synapse admin-endpoint `403 M_FORBIDDEN`, then writes the session atomically as owner-only JSON to a dedicated volume. OrgBot sees only that volume at `/run/mas-bootstrap`; it cannot read the Synapse administrator token. MAS CLI output is captured and suppressed because it contains issued tokens. The internal `POST /v1/users` endpoint lets policy-sync create the MAS link before applying a newly created Keycloak user's Matrix room access. This endpoint is reachable only on the Compose network and requires its own bearer token.

## Required environment

| Variable | Purpose |
| --- | --- |
| `MAS_CONFIG` | Generated MAS keys plus the private runtime configuration. |
| `MAS_UPSTREAM_PROVIDER_ID` | Fixed Keycloak OIDC provider ID used by the seed mappings. |
| `MAS_PROVISIONER_API_TOKEN` | Bearer secret for the private account-linking endpoint. |
| `KEYCLOAK_POLICY_CLIENT_SECRET` | Secret for the least-privilege Keycloak service account used to look up user subjects. |
| `MAS_ADMIN_USERNAME` | Private Matrix administrator used to issue the compatibility token. |
| `MAS_ADMIN_TOKEN_DEVICE_ID` | Stable device ID for the private administrative token. |
| `MAS_ADMIN_TOKEN_PATH` | Optional file path for the administrator token. The application defaults to `/data/synapse-admin-token`. Compose supplies a path in the private session volume. |
| `MAS_ADMIN_TOKEN_VERIFIED_PATH` | Optional freshness marker path for the last successful token identity and scope verification. The application defaults to the token path plus `.verified`. Compose supplies its path explicitly. |
| `ORG_BOT_DEVICE_ID` | Stable, non-admin Matrix device ID for OrgBot. |
| `ORG_BOT_SESSION_PATH` | Writable path for the OrgBot session JSON; defaults to `/run/orgbot-session/orgbot-session.json` in its separate private volume. |
| `MATRIX_HOMESERVER_URL` | Private Synapse Client-Server API used to validate the issued OrgBot session. |

The seed file is mounted read-only. The provisioner does not change clearance, room roles, room membership, or power levels; policy-sync owns those operations. Never publish this service or log its environment, bearer secrets, account passwords, or MAS command output.

## Recovery boundary

The bootstrap volume stores account-mapping state, the Synapse administrator token, and its last verification marker. The token is not periodically replaced merely because time passed. The separate OrgBot volume stores only the non-admin session JSON. If MAS creates an account but the process stops before saving its state, the next startup may report a duplicate registration. Review the MAS account and provider mapping before clearing only the stale provisioner state; do not delete the account or reset the MAS database as an automatic recovery action.

Run the service-session, administrator-session, and access-entitlement checks
with `node --test src/provisioner/*.test.mjs`. See the [administration guide](../../docs/guides/ADMINISTRATION.md)
and [operations guide](../../docs/guides/OPERATIONS.md) for deployment procedures.
