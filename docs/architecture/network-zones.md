# Network zones

The Compose model separates traffic through logical bridges on one Docker
host. Verify the resolved model and real connections. A deployment that
requires a WLAN, DMZ and external zone on separate networks must implement
and test those boundaries independently. [Operational scope](../guides/OPERATIONS.md#demo-and-production-boundaries)

## Compose topology

| Network | Attached services | External forwarding |
| --- | --- | --- |
| `edge` | Nginx and LiveKit | Configured proxy/media ports |
| `application` | Nginx, Element Web, Keycloak, MAS, Synapse, Export Service, LiveKit JWT service | Internal bridge |
| `control` | Keycloak, MAS, Synapse, Provisioner, Policy Sync, OrgBot, Export Service | Internal bridge |
| `keycloak-data-net` | Keycloak and its database | Internal bridge |
| `synapse-data-net` | Synapse and its database | Internal bridge |
| `mas-data-net` | MAS, its database, and Provisioner | Internal bridge |

Provisioner intentionally needs the MAS database because `mas-cli`
compatibility-token issuance reads it. It cannot reach the other databases.
The Synapse configuration renderer uses `network_mode: none`.

Only Nginx and LiveKit publish host ports. OrgBot's plaintext export API,
Provisioner's API, Keycloak management health and all databases remain
unpublished. Nginx does not share the control network and cannot directly reach
OrgBot or Provisioner. Application authorization still protects APIs that
intentionally share a network.

```text
Clients / public edge
       |
       v
    Nginx ---- edge ---- LiveKit <---- client media TCP/UDP
       |
   application
       |-- Element Web
       |-- Keycloak ---- keycloak-data-net ---- Keycloak DB
       |-- Synapse ----- synapse-data-net ----- Synapse DB
       |-- MAS --------- mas-data-net --------- MAS DB
       |-- LiveKit JWT                    |
       +-- Export Service                 +-- Provisioner
                 |                            |
              control -----------------------+
                 |-- Policy Sync
                 |-- OrgBot
                 +-- Keycloak / Synapse / MAS
```

## DNS, TLS and required calls

Nginx has the configured Matrix/auth hostname aliases on `application`. MAS
and Synapse can use the canonical public HTTPS URL for OIDC discovery, JWKS,
backchannel and Matrix identity requests without general Internet egress.
TLS hostname and chain verification stay enabled. Worker requests use explicit
`http://synapse:8008`, `http://keycloak:8080/auth`,
`http://provisioner:8091` and `http://org-bot:8090` control endpoints.

The proxy reaches the JWT service through `application` and LiveKit through
`edge`. The JWT service validates Matrix identity against Synapse. LiveKit has
configured public media ports and external connectivity. New SMTP, identity
brokers, federation, push gateways or other outbound integrations require
explicitly reviewed flows. Do not silently attach every service to `edge`.

The six `COMPOSE_*_SUBNET` settings in `.env.example` select separate private
IPv4 ranges. Check them against every existing Docker network and host route.
The edge and application networks also pin `COMPOSE_EDGE_GATEWAY` and
`COMPOSE_APPLICATION_GATEWAY`; change each gateway with its subnet and keep
the first host address reserved for the gateway.
Service DNS names stay stable; container IPs can change. Bootstrap and private
acceptance tools select a specific application/control interface from a
multi-homed container rather than concatenating its addresses.

## Host and management boundaries

Docker port publication installs forwarding/NAT rules. Ordinary host firewall
rules do not automatically protect published container ports. With the iptables
backend, forwarded-flow restrictions belong ahead of Docker's accepts in
`DOCKER-USER`; matching original published destinations after DNAT requires
conntrack. Preserve unrelated applications and SSH. See
[Docker's firewall guidance](https://docs.docker.com/engine/network/firewall-iptables/).

Docker's userland proxy can also expose a host listening socket. A container
connecting through that socket takes the host INPUT path instead of the DNAT
forwarding path. Protect the same exact backend addresses and ports on both
paths when that proxy is used; forwarding rules alone are insufficient. Verify
the permitted proxy, an unrelated container and independent live-listener
controls before accepting a deny result.

An outer proxy using a published host socket may appear to Nginx as the Docker
host gateway. Configure `TRUSTED_PROXY_CIDRS` for the observed, verified peer
addresses only, with bypass access blocked first. Do not trust whole container
subnets. Test a legitimate forwarded client address and forged headers from an
untrusted application peer. [Operations](../guides/OPERATIONS.md#proxy-client-addresses)
describes the corresponding admin-boundary checks.

Restrict root/master administration to operator CIDRs at the proxy. The demo
exposes delegated realm administration through an authenticated route when
published. Review whether that route belongs on the public ingress for each
deployment. Keep management ports and Docker sockets unpublished. The
host-local operator remains a privileged management path.

The supplied bridges disable IPv6 and LiveKit publishes on IPv4 by default.
Inspect actual IPv4/IPv6 listeners and bindings. Enabling IPv6 later requires
separate routing, firewall and acceptance. An empty IPv6 scan is not proof of a
tested dual-stack media deployment.

## Acceptance and recovery

Check required positive connections before accepting negative tests: owning
applications to their databases, Provisioner to MAS DB, workers to Synapse and
private APIs, and all proxy routes. Probe direct destination IPs from forbidden
source namespaces; failed DNS alone is not isolation. Include an unrelated
container network and an external vantage point. Verify canonical self-hostname
DNS/trusted HTTPS from MAS/Synapse, admin restrictions and external media
transport separately from camera/audio.

Retain previous Compose/network and host-filter state for rollback. Preserve
volumes, keys, users and room IDs. Repeat login, role withdrawal, encrypted
messages/files, export and zero-media call logic after migration. See
[Installation](../guides/INSTALLATION.md), [Security](../guides/SECURITY.md) and
[Operations](../guides/OPERATIONS.md).
