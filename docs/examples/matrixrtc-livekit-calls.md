# Example: MatrixRTC calls with LiveKit

The call flow checks Matrix room policy before minting and returning a LiveKit participant token.

~~~text
Matrix client         -> Synapse policy guard       authenticated token request
Synapse policy guard  -> Private LiveKit JWT service after room authorization
Private JWT service   -> Synapse policy guard       participant token
Synapse policy guard  -> Matrix client              after authorization recheck
Matrix client         -> LiveKit SFU                 authorized connection
~~~

The guard derives the caller from Matrix authentication, checks the current policy and room state, forwards to a fixed private token endpoint, then rechecks authorization before returning a token. The call-member power threshold is 10, matching the writer role. A reader is not authorized to request a call token. The native Matrix Client-Server path and the standalone OpenID path are both guarded; alternate public token-mint routes are denied.

HTTPS/WSS signaling and real-time media are separate paths. LiveKit requires its configured TCP/UDP media ports to be reachable. This Compose project does not include TURN, and successful token authorization does not prove audio/video transport. Native mobile behavior, ringing, actual media, and active-call termination after role revocation require separate acceptance. See [Calls](../guides/CALLS.md).
