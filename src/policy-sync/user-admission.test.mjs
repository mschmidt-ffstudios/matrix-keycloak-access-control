import assert from "node:assert/strict";
import test from "node:test";
import { demoProfileDisplayName, ensureMasBackedUser, isReservedMatrixIdentity } from "./user-admission.mjs";

const user = { username: "demo-new", userId: "@demo-new:test", id: "keycloak-subject",
  displayName: "Test", enabled: true };
function fixture(responses) {
  const calls = [];
  return { calls, options: { user, provisionerBase: "http://provisioner:8091", provisionerToken: "private-service-token",
    synapseBase: "http://synapse:8008", matrixToken: "private-matrix-token", attempts: 3, delayMs: 1,
    sleep: async (ms) => calls.push({ wait: ms }),
    request: async (base, path, options) => {
      calls.push({ base, path, ...options });
      if (options.method === "POST") return { status: 200, data: { provisioned: true } };
      if (options.method === "PUT") return { status: 200 };
      return responses.shift();
    },
  } };
}
test("an asynchronous MAS job must produce its shadow before any Admin profile PUT", async () => {
  const f = fixture([{ status: 404 }, { status: 404 }, { status: 200, data: { name: user.userId, admin: false } }]);
  await ensureMasBackedUser(f.options);
  assert.deepEqual(f.calls.map((call) => call.method || (call.wait ? "wait" : "GET")),
    ["POST", "GET", "wait", "GET", "wait", "GET", "PUT"]);
  assert.equal(f.calls[0].body.subject, user.id);
  assert.deepEqual(f.calls.at(-1).body, { displayname: "Test", admin: false, deactivated: false });
});
test("an already provisioned identity is updated without waiting or new sessions", async () => {
  const f = fixture([{ status: 200, data: { name: user.userId, admin: false } }]);
  await ensureMasBackedUser(f.options);
  assert.equal(f.calls.length, 3);
});
test("timeout never falls back to Admin creation or emits a profile write", async () => {
  const f = fixture([{ status: 404 }, { status: 404 }, { status: 404 }]);
  await assert.rejects(ensureMasBackedUser(f.options), /admission deadline/);
  assert.equal(f.calls.filter((call) => !call.method && !call.wait).length, 3);
  assert.equal(f.calls.some((call) => call.method === "PUT"), false);
});
test("wrong identities and administrator collisions fail closed", async () => {
  for (const data of [{ name: "@other:test", admin: false }, { name: user.userId, admin: true }]) {
    const f = fixture([{ status: 200, data }]);
    await assert.rejects(ensureMasBackedUser(f.options), /expected non-administrator/);
    assert.equal(f.calls.some((call) => call.method === "PUT"), false);
  }
});
test("disabled people are not provisioned or reactivated", async () => {
  const f = fixture([]);f.options.user = { ...user, enabled: false };
  await ensureMasBackedUser(f.options);
  assert.equal(f.calls.length, 0);
});
test("reserved service and admin identities are filtered before admission traffic", async () => {
  const candidates = [user, { ...user, userId: "@demo-policy-admin:test" }, { ...user, userId: "@orgbot:test" }];
  const serviceIds = new Set(["@orgbot:test"]);
  const admitted = candidates.filter((candidate) =>
    !isReservedMatrixIdentity(candidate.userId, "@demo-policy-admin:test", serviceIds));
  const f = fixture([{ status: 200, data: { name: user.userId, admin: false } }]);
  for (const candidate of admitted) await ensureMasBackedUser({ ...f.options, user: candidate });
  assert.equal(admitted.length, 1);
  assert.equal(f.calls.filter((call) => call.method === "POST").length, 1);
});
test("synthetic surname fallback preserves friendly names while real surnames remain", () => {
  assert.equal(demoProfileDisplayName({ firstName: "Alex", lastName: "Demo" }, "demo-alex"), "Alex");
  assert.equal(demoProfileDisplayName({ firstName: "Riley", lastName: "Demo" }, "demo-riley"), "Riley");
  assert.equal(demoProfileDisplayName({ firstName: "Jane", lastName: "Smith" }, "demo-jane"), "Jane Smith");
  assert.equal(demoProfileDisplayName({ firstName: "Jane", lastName: "Demo" }, "ordinary-jane"), "Jane Demo");
});
