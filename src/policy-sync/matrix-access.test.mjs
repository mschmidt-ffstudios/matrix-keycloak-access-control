import assert from "node:assert/strict";
import test from "node:test";
import { matrixEntitlement, reconcileMatrixEntitlement } from "./matrix-access.mjs";
import { buildRoomPlan, makeChatPolicySnapshot } from "./policy-model.mjs";

test("an enabled Keycloak user needs an effective Matrix role without changing the account", () => {
  const kc = { enabled: true };
  assert.deepEqual(matrixEntitlement(kc, []), {
    keycloakEnabled: true, matrixAccess: false, enabled: false, exportAdmin: false,
  });
  assert.equal(kc.enabled, true);
  assert.equal(matrixEntitlement(kc, [{ name: "room.allgemein.writer" }]).enabled, false);
  assert.equal(matrixEntitlement(kc, [{ name: "matrix-access" }]).enabled, true);
  assert.equal(matrixEntitlement({ enabled: false }, [{ name: "matrix-access" }]).enabled, false);
  assert.throws(() => matrixEntitlement(kc, null), /malformed/);
  assert.throws(() => matrixEntitlement(kc, [{ wrong: true }]), /malformed/);
});

test("withdrawing application access overrides room roles and shared direct groups", () => {
  const user = {
    id: "kc-user", userId: "@demo-person:example.org", clearance: "basis", rank: 0,
    roomRoleMap: new Map([["allgemein", ["writer"]]]),
    directGroups: [{ id: "cohort", name: "1to1chat.demo" }],
    ...matrixEntitlement({ enabled: true }, []),
  };
  const options = {
    room: { id: "allgemein", minimum_clearance: "basis" }, users: [user],
    currentMemberships: new Map([[user.userId, "join"]]),
    adminUserId: "@manager:example.org", serviceUserIds: new Set(), servicePower: 100,
    rankByClearance: new Map([["basis", 0]]), allowedRoles: new Set(["writer"]), powerByRole: { writer: 10 },
  };
  const plan = buildRoomPlan(options);
  assert.deepEqual(plan.eligibleUsers, []);
  assert.deepEqual(plan.revokeUserIds, [user.userId]);
  const snapshot = makeChatPolicySnapshot({ ...options, generatedAt: 1000, ttlSeconds: 60,
    now: 1001, matrixServerName: "example.org", roomMap: [], adminUserIds: new Set(), groupPrefix: "1to1chat." });
  assert.equal(snapshot.matrix_access_role, "matrix-access");
  assert.deepEqual(snapshot.users, []);
});

test("the private provisioner receives only the exact managed identity and effective grant", async () => {
  const calls = [];
  await reconcileMatrixEntitlement({
    user: { username: "demo-person", id: "kc-subject", enabled: false },
    request: async (...args) => calls.push(args), provisionerBase: "http://provisioner:8091", provisionerToken: "fixture",
  });
  assert.deepEqual(calls[0][2].body, { username: "demo-person", subject: "kc-subject", allowed: false });
  assert.equal(calls[0][1], "/v1/users/access");
});
