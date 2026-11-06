import assert from "node:assert/strict";
import test from "node:test";
import { reconcileMatrixAccess } from "./matrix-access.mjs";

const subject = "61ac5c0e-17e8-57b9-884a-e4f08fdec320";
const reserved = new Set(["orgbot", "demo-policy-admin", "demo-admin-01", "demo-admin-02"]);
function harness({ matrixAccess, admin = false, bot = false, failKill = false } = {}) {
  let saved = null;
  const state = { users: { "demo-person": { subject, admin, bot, ...(matrixAccess === undefined ? {} : { matrixAccess }) } } };
  const calls = [];
  const run = (allowed) => reconcileMatrixAccess({
    input: { username: "demo-person", subject, allowed }, reservedUsernames: reserved,
    loadState: async () => structuredClone(state),
    saveState: async (next) => { saved = next; Object.assign(state, next); },
    runMas: async (args) => { calls.push(args); if (failKill && args[1] === "kill-sessions") throw new Error("fixture"); },
  });
  return { state, calls, run, saved: () => saved };
}

test("withdrawal locks only MAS and finishes sessions, without deactivating the account", async () => {
  const h = harness({ matrixAccess: true });
  await h.run(false);
  assert.deepEqual(h.calls, [["manage", "lock-user", "demo-person"], ["manage", "kill-sessions", "demo-person"]]);
  assert.equal(h.state.users["demo-person"].matrixAccess, false);
  await h.run(false);
  assert.equal(h.calls.length, 2);
  await h.run(true);
  assert.deepEqual(h.calls[2], ["manage", "unlock-user", "demo-person"]);
});

test("failed session termination is never recorded as successful revocation", async () => {
  const h = harness({ matrixAccess: true, failKill: true });
  await assert.rejects(h.run(false));
  assert.equal(h.saved(), null);
  assert.equal(h.state.users["demo-person"].matrixAccess, true);
});

test("reserved services, admin identities and subject collisions fail before any CLI action", async () => {
  for (const entry of [{ admin: true }, { bot: true }]) {
    const h = harness(entry);
    await assert.rejects(h.run(false), /identity/);
    assert.deepEqual(h.calls, []);
  }
  const calls = [];
  for (const username of reserved) {
    await assert.rejects(reconcileMatrixAccess({ input: { username, subject, allowed: false },
      reservedUsernames: reserved, loadState: async () => { calls.push("read"); },
    }), /Invalid/);
  }
  assert.deepEqual(calls, []);
  await assert.rejects(reconcileMatrixAccess({ input: { username: "demo-person", subject, allowed: true },
    reservedUsernames: reserved, loadState: async () => ({ users: { "demo-person": { subject: "other", admin: false, bot: false } } }),
  }), /identity/);
});

test("an unprovisioned unentitled user is never created or unlocked", async () => {
  let calls = 0;
  const options = { reservedUsernames: reserved, loadState: async () => ({ users: {} }),
    runMas: async () => { calls++; }, saveState: async () => { calls++; } };
  assert.deepEqual(await reconcileMatrixAccess({ ...options, input: { username: "demo-new", subject, allowed: false } }),
    { applied: true, provisioned: false, allowed: false });
  await assert.rejects(reconcileMatrixAccess({ ...options, input: { username: "demo-new", subject, allowed: true } }), /provisioned/);
  assert.equal(calls, 0);
});
