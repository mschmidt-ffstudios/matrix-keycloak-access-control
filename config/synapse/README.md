# Synapse configuration

`homeserver.yaml.template` is rendered by `deployment/synapse/render-homeserver.py` from the private deployment environment. The Compose `synapse-config` service also creates the signing key on first start. PostgreSQL, signing keys, media, and the rendered YAML live in private Compose volumes.

Synapse configuration is YAML; the seed and room policy are JSON inputs to the project services, not native Synapse JSON imports. MAS owns Matrix authentication and delegates user identity to Keycloak. Policy-sync manages encrypted rooms, membership, and power levels through Synapse's private admin API using a CLI-issued compatibility token provisioned and verified by the private provisioner. MAS v1.28.0 does not set an expiry on this CLI-issued token; it is reused while valid and must be revoked deliberately. Keep MAS/Synapse secrets and the admin API on the private Compose network. Nginx explicitly returns 404 for the admin path; no shared-secret account-registration endpoint is enabled.

The template uses separate registration, macaroon, and form secrets. The public base URL, Keycloak issuer, internal token endpoint, Matrix server name, and LiveKit endpoints are rendered from the selected target's `.env`.
