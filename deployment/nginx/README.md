# Nginx reverse proxy

Nginx is the public TLS entry point. `scripts/prepare-demo.py` renders `default.conf.template` into the private generated configuration.

MAS routes use the `mas_backend` upstream with a shared zone and Docker DNS
resolution refreshed every ten seconds. URI rewriting stays unchanged. This
prevents a restarted MAS container's old address from remaining cached in the
proxy. Other backend changes still follow the documented startup/reload flow.
Each generated server block explicitly selects the `demo_safe` access-log
format, which records the path without query parameters. Defining that format
only at HTTP level would also inherit the image's default combined log.

| Public path | Compose service | Purpose |
| --- | --- | --- |
| `/` | `element-web:80` | Element Web client |
| `/auth/` | `keycloak:8080/auth/` | Keycloak sign-in and realm endpoints |
| `/mas/` | `mas:8080/` | Matrix OAuth/OIDC service and upstream Keycloak sign-in |
| `/export/` | Nginx static files | SSO-protected room selection and ZIP download page |
| `/_matrix/client/*/{login,logout,refresh}` | `mas:8080` | Synapse authentication endpoints delegated to MAS |
| `/_matrix/`, `/_synapse/client`, and `/_synapse/mas` | `synapse:8008` | Matrix APIs and Synapse's MAS delegation endpoints |
| `/_synapse/admin/` | none; returns 404 | Admin API stays private |
| `/_export/` | `export-service:8090/` | Authenticated room list and scoped export API |
| `/livekit/jwt/sfu/get` | Synapse policy gate, then private `lk-jwt-service:8080/sfu/get` | Checks the Matrix OpenID subject, fresh policy, current membership, E2EE state, bot readiness for direct chats, and room call power level before returning a token |
| `/_matrix/client/unstable/io.element.msc4195/rtc/livekit/get_token` | Synapse policy gate, then the private Synapse-to-application-service route | Checks the authenticated Matrix user, fresh policy, current membership, E2EE state, bot readiness for direct chats, and room call power level before the application service can mint a token |
| `/_matrix/federation/unstable/io.element.msc4195/rtc/livekit/get_token` | none; return 404 | Server-to-server token minting is outside the single-homeserver demo |
| `/livekit/jwt/healthz` | `lk-jwt-service:8080/healthz` | Read-only service health |
| Other `/livekit/jwt/*` paths | none; return 404 | Alternate public token-mint endpoints are closed |
| `/livekit/sfu/` | `livekit:7880/` | WebSocket/media signaling proxy |
| `/.well-known/matrix/server` | Nginx | Routes Matrix server discovery through the existing TLS port 443 |
| `/_matrix/federation/v1/openid/userinfo` | `synapse:8008` | Exact GET endpoint used by `lk-jwt-service` to validate OpenID tokens; Synapse exposes only its standalone `openid` resource, not the broader federation API |

LiveKit also publishes its configured TCP and UDP media ports directly in Compose. The call token gate limits HTTP requests, while LiveKit's media authorization and Matrix room membership remain separate checks. A valid reverse-proxy config does not prove that a call works; browser/mobile TLS trust, client support, firewall, NAT, and TURN requirements must be tested separately. Never expose OrgBot or Synapse admin endpoints through Nginx.

Nginx is distributed under the 2-clause BSD license; see [third-party runtime
notices](../../THIRD-PARTY-RUNTIME-NOTICES.md). Its container base and installed
packages have separate notices.

The Nginx master starts as root to read the owner-only TLS private key.
Workers use the upstream image's unprivileged `nginx` account. An operator
switching the master to an unprivileged account must review key access, socket
binding and runtime directories together. [Nginx worker user directive](https://nginx.org/en/docs/ngx_core_module.html#user)

## Host-proxy peers and client addresses

Docker's userland proxy can replace a connection's peer address with the host
gateway of the Nginx bridge. In that profile, the outer proxy's container address
is not the immediate peer seen by Nginx. Trust only the exact reviewed gateway
addresses, never the entire private subnet, and only after both host-socket INPUT
and DNAT/FORWARD paths restrict who can reach the backend publication. Direct
application-container requests must not become trusted forwarded-header sources.

The edge and application bridge IPAM gateways are explicit configuration values:
`COMPOSE_EDGE_GATEWAY` and `COMPOSE_APPLICATION_GATEWAY`. If a paired subnet is
changed, change its gateway to the reviewed first host address as well. Set
`TRUSTED_PROXY_CIDRS` only from that reviewed deployment topology. Management
`ADMIN_ALLOWED_CIDRS` remains a separate allowlist. Verify the real source through
the actual outer proxy and reject forged forwarded addresses from an untrusted
application peer before treating a client-address configuration as operational.
