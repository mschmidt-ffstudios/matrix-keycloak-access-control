export const MATRIX_ACCESS_ROLE = "matrix-access";

export function matrixEntitlement(keycloakUser, effectiveRealmRoles) {
  if (!Array.isArray(effectiveRealmRoles) || effectiveRealmRoles.some((role) =>
    !role || typeof role.name !== "string" || !role.name)) {
    throw new Error("Keycloak effective realm roles are malformed");
  }
  const roles = new Set(effectiveRealmRoles.map((role) => role.name));
  const keycloakEnabled = keycloakUser.enabled === true;
  const matrixAccess = roles.has(MATRIX_ACCESS_ROLE);
  return {
    keycloakEnabled,
    matrixAccess,
    enabled: keycloakEnabled && matrixAccess,
    exportAdmin: roles.has("org-export-admin"),
  };
}

export async function reconcileMatrixEntitlement({ user, request, provisionerBase, provisionerToken }) {
  await request(provisionerBase, "/v1/users/access", {
    method: "POST",
    token: provisionerToken,
    body: { username: user.username, subject: user.id, allowed: user.enabled === true },
  });
}
