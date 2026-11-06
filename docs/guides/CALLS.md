# MatrixRTC calls

The call path combines Matrix room authorization with a LiveKit SFU. MatrixRTC support uses experimental Matrix specifications and depends on compatible client versions; evaluate those dependencies before production use.

## Authorization path

1. A signed-in client requests a call token for a Matrix room.
2. The Synapse policy guard derives the user from authenticated Matrix credentials and checks the current policy snapshot, room membership, room encryption, and call-member power threshold.
3. The guard forwards only an authorized request to the private LiveKit JWT service, then rechecks current room authorization before returning the token.
4. The client uses the token to connect to the LiveKit SFU. Nginx carries the public HTTPS/WSS signaling path; configured TCP/UDP media traffic reaches LiveKit directly.

The call-member event threshold is 10, matching the writer power level. A reader at power 0 cannot request a call token for that room. Direct-chat calls also require the room's current pair authorization and ready OrgBot state. Client-supplied user or device fields do not replace the Matrix-authenticated identity.

The private token service has a separate Matrix membership check, but it is not the Keycloak policy authority. The Synapse guard is the layer that applies current clearance/room-role policy to call-token issuance. Alternate public token-mint routes are denied.

## Network requirements and limits

Use HTTPS/WSS for the public web and signaling endpoints. Open only the ports explicitly configured in the deployment. LiveKit media uses configured TCP/UDP ports; a reverse proxy for HTTPS does not carry all real-time media. This Compose project does not include a TURN relay, so restrictive NATs, firewalls, and mobile networks may prevent media connectivity.

`LIVEKIT_BIND_HOST` selects the host interface for the published TCP and UDP
media ports. `LIVEKIT_NODE_IP` is the address advertised to clients and must
be reachable from their network. Use loopback binding for host-only tests.
[Installation](INSTALLATION.md)

The JWT service verifies HTTPS when it reaches the canonical LiveKit URL
through the proxy. Its mounted CA bundle combines the host trust store with
the generated development root in local TLS mode.

Withdrawing room access blocks subsequent guarded token requests after the policy snapshot updates. Active calls and previously issued tokens follow their own lifecycle.

For each target, test token authorization, participant presence and departure separately from ringing and media transport. A test with zero published tracks establishes only signaling behavior. Verify native mobile clients, ringing, actual audio/video and the intended network path before claiming those capabilities; a token response or lobby join alone does not prove working media.

## See also

- [Room administration](ADMINISTRATION.md)
- [Native clients](NATIVE-CLIENTS.md)
- [Security](SECURITY.md)
