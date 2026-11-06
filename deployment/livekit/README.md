# LiveKit security derivative

This is the same LiveKit **1.13.11** release, built from its hash-pinned source
archive with Go 1.27.3. The checked-in `go.mod`/`go.sum` update the affected
golang.org/x/net and x/crypto dependency families without changing the server
release. `go mod download`, `go mod verify` and a read-only-module build enforce
the entire resolved graph. Runtime build information and both lock files remain
inside `/usr/share/doc/livekit/` for inspection.

```sh
docker build -f deployment/livekit/Dockerfile -t foldfence-demo/livekit:v1.13.11-security1 .
```

The existing `--config`, `--node-ip`, WebSocket, TCP/UDP and health interfaces
remain in use. LiveKit runs as UID/GID 65532. Compose passes the generated
configuration's group as a supplemental group so LiveKit can read the private
file. Validate authentication, two logical participants, room-policy
denials and reconnects against the final image before activation. A zero-media
test proves signaling and participant state only; it does not claim physical
camera, microphone or audible media acceptance.

The runtime includes LiveKit's Apache-2.0 license and upstream `NOTICE` at
`/usr/share/doc/livekit/`. The immutable source archive,
compiler image and runtime image are pinned in the Dockerfile. See
[runtime security builds](../security/README.md) for scan and release rules.
