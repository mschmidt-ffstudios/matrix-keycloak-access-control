# Example: White-label branding

The project keeps neutral public defaults in the branding configuration. Preparation derives service-specific assets for Keycloak and Element Web. This excerpt shows the keys to edit inside the existing `brand` object. Keep the remaining fields and target settings. [Branding configuration](../../config/branding/brand.json)

~~~json
{
  "brand": {
    "organization_name": "Example Organization",
    "product_name": "Example Secure Chat",
    "logo_wide": "assets/logo-wide.svg",
    "logo_mark": "assets/logo-mark.svg",
    "colors": { "primary": "#234567" }
  }
}
~~~

Use logos you are authorized to distribute. Check image dimensions, accessible contrast, alternate text, and browser-visible product names after rendering. Keep installation-specific brand assets and configuration in private deployment inputs.

Branding controls presentation. The separate Keycloak and Matrix policy inputs control authentication, room access, and export. [Installation](../guides/INSTALLATION.md)
