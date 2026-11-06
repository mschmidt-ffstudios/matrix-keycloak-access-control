# Example: Keycloak user and room role

A user's clearance and each room role are separate assignments. For example:

~~~text
User: demo-alex
security_clearance: vertraulich
matrix-access: granted
matrix-synapse client roles:
  room.allgemein.writer
  room.speicher.reader
~~~

This grants the Matrix login entitlement, writer access in Allgemein, and reader access in Speicher, provided each room's minimum clearance is met. It grants no access to rooms without an explicit room role. It grants no export permission unless the separate org-export-admin role is assigned.

Room roles are client roles on matrix-synapse. The login and export entitlements are realm roles. See [Administration](../guides/ADMINISTRATION.md) for all clearance levels, rooms, powers, and CLI operations.
