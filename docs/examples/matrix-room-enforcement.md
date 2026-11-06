# Example: Server-side room enforcement

The client interface can hide a room or disable a button, but those controls are not the authorization boundary. The server-side policy flow is:

~~~text
Keycloak account + Matrix access + clearance + explicit room role
                  |
                  v
             Policy Sync
                  |
        membership + power levels
                  v
                Synapse
~~~

For each configured room, admission requires an enabled user, `matrix-access`, sufficient clearance, and exactly one supported role for that room. Synapse stores membership and power levels and checks them when a client sends an event. These checks apply equally to normal client traffic and direct API requests.

Room roles map to power levels: reader 0, writer 10, moderator 50. Invite, kick, ban, and state changes require power 100. See [Administration](../guides/ADMINISTRATION.md) for the configured rooms and role mapping.
