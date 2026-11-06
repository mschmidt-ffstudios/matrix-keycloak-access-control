# Example: Group-based direct-chat eligibility

Both people must be enabled, hold `matrix-access`, and have a valid clearance to enter the policy snapshot. A direct chat also requires both people to have a direct Keycloak membership in the same exact group ID, with a group name in the configured namespace such as `1to1chat.team-a`. This synthetic example assumes all three accounts meet the snapshot requirements.

~~~text
demo-alex  -> group ID 8f...2a, name 1to1chat.team-a
demo-blair -> group ID 8f...2a, name 1to1chat.team-a
demo-casey -> group ID 3c...91, name department.team-a
~~~

Alex and Blair share the exact prefixed group, so policy may authorize a direct room for them. Casey's ordinary department group grants nothing. Matching display names or different group IDs with similar names do not count. The policy uses direct memberships returned by Keycloak; it does not infer nested membership.

The Matrix server enforces pair authorization, invite/join rules, current membership, and room encryption. OrgBot is an additional encrypted participant for readiness and export, so a direct room is not a two-person-only cryptographic room. A policy outage denies new protected actions; a fresh confirmed revocation removes the current grant. Already delivered content or keys cannot be recalled.

See [Administration](../guides/ADMINISTRATION.md) for group management and [room policy](room-policy.md) for per-room grants.
