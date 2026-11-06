const MANAGED_NAME = /^(?:demo-[a-z0-9][a-z0-9._-]*|export-test-[0-9]{2})$/i;
const SUBJECT = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function validateMatrixAccessRequest(input, reservedUsernames) {
  if (!input || typeof input !== "object" || Array.isArray(input) ||
      !MANAGED_NAME.test(input.username || "") ||
      reservedUsernames.has(input.username) ||
      !SUBJECT.test(input.subject || "") || typeof input.allowed !== "boolean" ||
      Object.keys(input).some((key) => !["username", "subject", "allowed"].includes(key))) {
    throw new Error("Invalid managed Matrix access request");
  }
  return input;
}

export async function reconcileMatrixAccess({ input, reservedUsernames, loadState, saveState, runMas }) {
  const { username, subject, allowed } = validateMatrixAccessRequest(input, reservedUsernames);
  const state = await loadState();
  const record = Object.hasOwn(state.users, username) ? state.users[username] : null;
  if (!record) {
    if (allowed) throw new Error("Matrix identity must be provisioned before granting access");
    return { applied: true, provisioned: false, allowed: false };
  }
  if (record.subject !== subject || record.admin !== false || record.bot === true) {
    throw new Error("Matrix access identity does not match the managed human subject");
  }
  if (record.matrixAccess === allowed) return { applied: true, provisioned: true, allowed };
  if (allowed) {
    // Unlock only: never reactivate a deactivated account or restore sessions.
    await runMas(["manage", "unlock-user", username]);
  } else {
    // This changes only MAS/Matrix access. The Keycloak account remains enabled.
    await runMas(["manage", "lock-user", username]);
    await runMas(["manage", "kill-sessions", username]);
  }
  record.matrixAccess = allowed;
  await saveState(state);
  return { applied: true, provisioned: true, allowed };
}
