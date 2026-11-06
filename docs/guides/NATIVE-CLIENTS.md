# Native clients

The deployment exposes standard Matrix client APIs through the configured public Matrix URL and uses Matrix Authentication Service (MAS) with Keycloak for human sign-in. Native clients must support the deployed Matrix login/SSO flow and the room's end-to-end encryption behavior.

## Client setup

1. Install a Matrix client compatible with the deployment's homeserver and authentication service.
2. Enter the public Matrix server name or URL, for example `matrix.example.org`, rather than a private container address.
3. Choose the configured single sign-on option and authenticate at the identity provider. The Keycloak password belongs at the identity provider; MAS local password login is disabled for human browser login.
4. Complete the client's device-key setup and recovery prompts. Keep the recovery key or secret in an approved secure location; it is needed to recover encrypted history on a new device.
5. Confirm that the signed-in account is entitled to Matrix (`matrix-access`) and has an explicit room role plus sufficient clearance for the rooms it should see.

The server name is part of each Matrix user ID. Changing it later is not a client preference change and can require account migration.

## Encryption and device changes

Each client device has its own cryptographic identity. Adding, replacing, or resetting a device changes which keys it can use. Verify device verification and recovery behavior before relying on encrypted history across devices. Room membership does not grant a device access to old Megolm keys that were never shared with it.

If a user loses access, distinguish the cause before resetting anything: Keycloak login entitlement, MAS identity/session state, Matrix membership, room role, and device-key recovery are separate controls. See [Administration](ADMINISTRATION.md) and [Operations](OPERATIONS.md).

## Calls and platform support

MatrixRTC support depends on the client version and platform. The repository's browser flow uses LiveKit, but native phone installation, mobile background behavior, ringing, audio/video media, and carrier/Wi-Fi NAT traversal have not been verified. Check [Calls](CALLS.md) before promising those capabilities.

## Client configuration

The public Element Web defaults are prepared from `config/element/config.json`. Native apps use their own client configuration and discovery behavior; they do not consume that web configuration file. Do not embed Keycloak client secrets, service tokens, private homeserver addresses, or OrgBot credentials in a client.
