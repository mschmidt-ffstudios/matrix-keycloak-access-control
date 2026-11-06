# White-label branding

`brand.json` is the organization-neutral default. Set `DEMO_BRAND_CONFIG` in a target's private `.env` to select the brand file for that deployment. The prepare script uses the selected organization's names, colors, theme name, and logo paths to generate Keycloak theme assets and Element Web configuration.

The default logo assets live beside this file under `assets/`. Private target branding belongs outside the public source tree and is selected through that target's private environment; it must not be copied into a public build by accident.

Element's native configuration changes the product name, homeserver, and selected logo. Full replacement of all Element product identity would require a maintained custom build; the config alone does not provide complete rebranding. `scripts/validate-demo-config.py` checks the selected tracked defaults and `prepare-demo.py` checks the chosen runtime brand path.
