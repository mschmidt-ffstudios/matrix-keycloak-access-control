# Security information

The [security model](docs/guides/SECURITY.md) describes identity, room-policy,
E2EE, export and call boundaries. The [operations guide](docs/guides/OPERATIONS.md)
covers persistent state and recovery limits. Review both before exposing a
new installation.

## Reporting a vulnerability

On the public GitHub repository, open **Security and quality**, then
**Advisories**, and select **Report a vulnerability**. This opens a private
report for the Foldfence Studios maintainers. If the button is unavailable,
ask a maintainer for a private contact and keep the details private.
[GitHub reporting instructions](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)

Include the affected source/image version, configuration assumptions, impact
and a minimal synthetic reproduction. Remove credentials, tokens, keys,
private room and user data, decrypted content, live exports and private
deployment profiles. Coordinate disclosure with the maintainers and test only
systems you are authorized to examine.

## Deployment review

Use exact image digests and current upstream advisories. Record scans and SBOMs of
the final artifacts. The example's unit tests and configuration validator do
not certify a deployment, historical export completeness, network isolation,
phone behavior or media transport. Define the supported versions and update
policy for the actual installation rather than assuming that pinned versions
remain safe indefinitely. Production qualification belongs to each operator and
installation. See the [project warranty and license terms](LICENSE).
