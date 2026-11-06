# Example: Room policy

A room grant requires Matrix access, the room's minimum clearance, and one explicit client role for that room. This is the Speicher entry from the checked-in `rooms` array. [Room policy configuration](../../config/policies/room-access.json)

~~~json
{
  "id": "speicher",
  "name": "Speicher",
  "alias_localpart": "speicher",
  "minimum_clearance": "vertraulich",
  "encrypted": true,
  "join_rule": "invite",
  "history_visibility": "joined"
}
~~~

An enabled person with `matrix-access`, clearance `vertraulich` or higher, and the `matrix-synapse` client role `room.speicher.reader` is eligible for read access. Assign the role to the person in Keycloak. Policy denies access when any condition fails and evaluates each room independently.

The demo clearance labels are basis, intern, vertraulich, and streng_vertraulich. They control admission; they do not change the encryption algorithm. See [Administration](../guides/ADMINISTRATION.md) for the current room catalog and Matrix power-level mapping.
