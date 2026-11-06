// MAS register-user queues Synapse provisioning; it does not wait for that job.
// The Synapse Admin API must only update an existing MAS-backed shadow user.
export async function ensureMasBackedUser({ user, request, sleep, provisionerBase,
  provisionerToken, synapseBase, matrixToken, attempts = 15, delayMs = 2000 }) {
  if (!user.enabled) return;
  await request(provisionerBase, "/v1/users", {
    method: "POST", token: provisionerToken,
    body: { username: user.username, subject: user.id, displayName: user.displayName },
  });
  const userPath = `/_synapse/admin/v2/users/${encodeURIComponent(user.userId)}`;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const result = await request(synapseBase, userPath, { token: matrixToken, expected: [404] });
    if (result.status === 200) {
      if (result.data?.name !== user.userId || result.data?.admin !== false) {
        throw new Error("MAS-provisioned Matrix identity is not the expected non-administrator");
      }
      await request(synapseBase, userPath, {
        method: "PUT", token: matrixToken,
        body: { displayname: user.displayName, admin: false, deactivated: false },
      });
      return;
    }
    if (result.status !== 404) throw new Error("Unexpected MAS identity readiness response");
    if (attempt + 1 < attempts) await sleep(delayMs);
  }
  throw new Error("MAS did not provision its Synapse identity before the admission deadline");
}

export function demoProfileDisplayName(kcUser, localpart) {
  const first = typeof kcUser.firstName === "string" ? kcUser.firstName.trim() : "";
  // `Demo` is the required-profile fallback used by the synthetic-user tools.
  // Keep that placeholder out of the Matrix name shown to testers.
  if (/^demo-/i.test(localpart) && kcUser.lastName === "Demo" && first) return first;
  return [kcUser.firstName, kcUser.lastName].filter(Boolean).join(" ").trim() || localpart;
}

export function isReservedMatrixIdentity(userId, adminUserId, serviceUserIds) {
  return userId === adminUserId || serviceUserIds.has(userId);
}
