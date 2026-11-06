import assert from "node:assert/strict";
import test from "node:test";
import {
  buildRoomPlan,
  directChatGroups,
  fetchAllKeycloakPages,
  isChatPolicySnapshotFresh,
  isManagedDemoUser,
  makeJoinPolicySnapshot,
  makeChatPolicySnapshot,
  makeManagedRoomMapEntry,
  parseRoomRole,
} from "./policy-model.mjs";

const rolePattern = "room.{room_id}.{role}";
const allowedRoles = new Set(["reader", "writer", "moderator"]);
const powerByRole = { reader: 0, writer: 10, moderator: 50 };
const rankByClearance = new Map([
  ["basis", 0],
  ["intern", 1],
  ["vertraulich", 2],
  ["streng_vertraulich", 3],
]);
const room = { id: "pummel", minimum_clearance: "intern" };
const adminUserId = "@demo-policy-admin:example.org";
const orgBotId = "@orgbot:example.org";

test("managed export metadata follows the enforced clearance rather than stale labels", () => {
  const policyRoom = {
    id: "speicher", name: "Speicher", alias_localpart: "speicher",
    minimum_clearance: "vertraulich", classification: "basis",
    authorization_basis: "untrusted_supplied_label", test_only: false,
  };
  const entry = makeManagedRoomMapEntry({
    room: policyRoom, roomId: "!speicher:example.org",
    matrixServerName: "example.org", rankByClearance,
  });
  assert.equal(entry.classification, "vertraulich");
  assert.equal(entry.minimum_clearance, entry.classification);
  assert.equal(entry.minimum_clearance_rank, 2);
  assert.equal(entry.authorization_basis, "keycloak_room_role_and_clearance");
  assert.equal(entry.alias, "#speicher:example.org");
  assert.equal(entry.test_only, false);
  assert.throws(() => makeManagedRoomMapEntry({
    room: { ...policyRoom, minimum_clearance: "unrecognized" },
    roomId: entry.room_id, matrixServerName: "example.org", rankByClearance,
  }), /unknown clearance/);
});

function demoUser(username, clearance, roleNames = [], enabled = true) {
  const roomRoleMap = new Map();
  for (const roleName of roleNames) {
    const parsed = parseRoomRole(roleName, rolePattern, allowedRoles);
    if (parsed) {
      if (!roomRoleMap.has(parsed.roomId)) roomRoleMap.set(parsed.roomId, []);
      roomRoleMap.get(parsed.roomId).push(parsed.role);
    }
  }
  return {
    userId: `@${username}:example.org`,
    enabled,
    matrixAccess: true,
    rank: rankByClearance.get(clearance) ?? null,
    roomRoleMap,
  };
}

function plan({ users, memberships = new Map(), targetRoom = room }) {
  return buildRoomPlan({
    room: targetRoom,
    users,
    currentMemberships: memberships,
    adminUserId,
    serviceUserIds: new Set([orgBotId]),
    servicePower: 100,
    rankByClearance,
    allowedRoles,
    powerByRole,
    rolePattern,
  });
}

test("only an explicit, recognized room role grants access", () => {
  const reader = demoUser("reader", "intern", ["room.pummel.reader"]);
  const noRole = demoUser("no-role", "intern");
  const wrongRoom = demoUser("wrong-room", "intern", ["room.speicher.writer"]);
  const unknownRole = demoUser("unknown", "intern", ["room.pummel.owner"]);
  const result = plan({ users: [reader, noRole, wrongRoom, unknownRole] });

  assert.deepEqual(result.eligibleUsers.map((user) => user.userId), [reader.userId]);
  assert.equal(result.desiredPower[reader.userId], 0);
  assert.equal(result.desiredPower[noRole.userId], undefined);
});

test("insufficient clearance denies an otherwise valid role and revokes an existing join", () => {
  const user = demoUser("low-clearance", "basis", ["room.pummel.writer"]);
  const result = plan({ users: [user], memberships: new Map([[user.userId, "join"]]) });

  assert.deepEqual(result.eligibleUsers, []);
  assert.deepEqual(result.revokeUserIds, [user.userId]);
  assert.equal(result.desiredPower[user.userId], undefined);
});

test("removing a role removes both the member and its Matrix power assignment", () => {
  const user = demoUser("revoked", "intern");
  const result = plan({ users: [user], memberships: new Map([[user.userId, "join"]]) });

  assert.deepEqual(result.revokeUserIds, [user.userId]);
  assert.equal(result.desiredPower[user.userId], undefined);
});

test("conflicting room roles deny access", () => {
  const user = demoUser("conflicted", "intern", ["room.pummel.reader", "room.pummel.writer"]);
  const result = plan({ users: [user] });

  assert.deepEqual(result.eligibleUsers, []);
  assert.equal(result.desiredPower[user.userId], undefined);
});

test("service users are joined before demo users and stay at power 0", () => {
  const serviceUserWithRole = demoUser("orgbot", "intern", ["room.pummel.moderator"]);
  const writer = demoUser("demo-23", "intern", ["room.pummel.writer"]);
  const result = plan({
    users: [serviceUserWithRole, writer],
    memberships: new Map([[orgBotId, "invite"]]),
  });

  assert.deepEqual(result.joinSequence, [
    { kind: "service", userId: orgBotId },
    { kind: "demo", userId: writer.userId },
  ]);
  assert.deepEqual(result.eligibleUsers.map((user) => user.userId), [writer.userId]);
  assert.equal(result.desiredPower[orgBotId], 0);
  assert.equal(result.desiredPower[writer.userId], 10);
  assert.ok(result.desiredPower[orgBotId] < 10, "PL 0 is below the m.room.message threshold of 10");
  assert.deepEqual(result.revokeUserIds, []);

  const alreadyJoined = plan({ users: [writer], memberships: new Map([[orgBotId, "join"]]) });
  assert.deepEqual(alreadyJoined.joinSequence, [{ kind: "demo", userId: writer.userId }]);
});

test("room roles stay independent, and each room still applies its clearance minimum", () => {
  const rooms = {
    allgemein: { id: "allgemein", minimum_clearance: "basis" },
    pummel: { id: "pummel", minimum_clearance: "intern" },
    speicher: { id: "speicher", minimum_clearance: "vertraulich" },
    leitung: { id: "leitung", minimum_clearance: "streng_vertraulich" },
  };
  const multiRoomUser = demoUser("demo-21", "vertraulich", [
    "room.pummel.reader",
    "room.speicher.moderator",
  ]);
  const lowClearanceUser = demoUser("demo-22", "intern", [
    "room.pummel.reader",
    "room.speicher.moderator",
  ]);

  const pummel = plan({ users: [multiRoomUser], targetRoom: rooms.pummel });
  const speicher = plan({ users: [multiRoomUser], targetRoom: rooms.speicher });
  const allgemein = plan({ users: [multiRoomUser], targetRoom: rooms.allgemein });
  const leitung = plan({ users: [multiRoomUser], targetRoom: rooms.leitung });
  const lowClearanceSpeicher = plan({ users: [lowClearanceUser], targetRoom: rooms.speicher });

  assert.deepEqual(pummel.eligibleUsers.map((user) => user.userId), [multiRoomUser.userId]);
  assert.equal(pummel.desiredPower[multiRoomUser.userId], 0);
  assert.deepEqual(speicher.eligibleUsers.map((user) => user.userId), [multiRoomUser.userId]);
  assert.equal(speicher.desiredPower[multiRoomUser.userId], 50);
  assert.deepEqual([...allgemein.eligibleUsers, ...leitung.eligibleUsers, ...lowClearanceSpeicher.eligibleUsers], []);
});

test("only synthetic demo accounts with one known clearance enter user sync scope", () => {
  const eligible = {
    id: "user-id",
    username: "demo-21",
    attributes: { security_clearance: ["intern"] },
  };
  assert.equal(isManagedDemoUser(eligible, "security_clearance", rankByClearance), true);
  assert.equal(isManagedDemoUser({ ...eligible, username: "service-account-matrix-policy-sync" }, "security_clearance", rankByClearance), false);
  assert.equal(isManagedDemoUser({ ...eligible, attributes: { security_clearance: ["unknown"] } }, "security_clearance", rankByClearance), false);
  assert.equal(isManagedDemoUser({ ...eligible, attributes: { security_clearance: ["intern", "basis"] } }, "security_clearance", rankByClearance), false);
  assert.equal(isManagedDemoUser({ ...eligible, username: "export-test-01" }, "security_clearance", rankByClearance), true);
  assert.equal(isManagedDemoUser({ ...eligible, username: "export-test-real" }, "security_clearance", rankByClearance), false);
  assert.equal(isManagedDemoUser({ ...eligible, username: "demo-operator-23" }, "security_clearance", rankByClearance), true);
});

test("only explicitly returned groups in the direct-chat namespace are retained", () => {
  const groups = directChatGroups([
    { id: "ordinary-org", name: "orga-x", path: "/orga-x" },
    { id: "ordinary-dept", name: "abteilung-y", path: "/orga-x/abteilung-y" },
    { id: "chat-group", name: "1to1chat.orga-x.abteilung-y", path: "/1to1chat.orga-x.abteilung-y" },
    { id: "path-only", name: "abteilung-z", path: "/1to1chat.orga-x.abteilung-z" },
    { id: "bare-prefix", name: "1to1chat.", path: "/1to1chat." },
  ]);

  assert.deepEqual(groups, [
    { id: "chat-group", name: "1to1chat.orga-x.abteilung-y", path: "/1to1chat.orga-x.abteilung-y" },
  ]);
});

test("Keycloak pagination reads every page and fails on malformed or non-progressing results", async () => {
  const pages = new Map([
    [0, [{ id: "u1" }, { id: "u2" }]],
    [2, [{ id: "u3" }, { id: "u4" }]],
    [4, [{ id: "u5" }]],
  ]);
  const all = await fetchAllKeycloakPages(async (first, max) => {
    assert.equal(max, 2);
    return pages.get(first) || [];
  }, { pageSize: 2 });
  assert.deepEqual(all.map(({ id }) => id), ["u1", "u2", "u3", "u4", "u5"]);

  await assert.rejects(fetchAllKeycloakPages(async () => ({ id: "not-an-array" }), { pageSize: 2 }));
  await assert.rejects(fetchAllKeycloakPages(async () => [{ id: "same" }, { id: "same" }], { pageSize: 2 }), /without progress/);
});

test("chat policy snapshot scopes room access independently and snapshots only direct explicit groups", () => {
  const multiRoomUser = demoUser("demo-21", "vertraulich", [
    "room.pummel.reader",
    "room.speicher.moderator",
  ]);
  multiRoomUser.id = "kc-demo-21";
  multiRoomUser.clearance = "vertraulich";
  multiRoomUser.directGroups = [
    { id: "org", name: "orga-x", path: "/orga-x" },
    { id: "chat", name: "1to1chat.orga-x.abteilung-y", path: "/1to1chat.orga-x.abteilung-y" },
  ];
  const disabled = demoUser("demo-22", "vertraulich", ["room.pummel.writer"], false);
  disabled.id = "kc-demo-22";
  disabled.clearance = "vertraulich";
  disabled.directGroups = [{ id: "chat-disabled", name: "1to1chat.orga-x", path: "/1to1chat.orga-x" }];
  const orgBot = demoUser("orgbot", "vertraulich", ["room.pummel.moderator"]);
  orgBot.id = "kc-orgbot";
  orgBot.clearance = "vertraulich";

  const snapshot = makeChatPolicySnapshot({
    generatedAt: 10_000,
    ttlSeconds: 600,
    matrixServerName: "example.org",
    roomMap: [
      { ...room, room_id: "!pummel:example.org", alias: "#pummel:example.org", name: "Pummel" },
      { id: "speicher", minimum_clearance: "vertraulich", room_id: "!speicher:example.org", alias: "#speicher:example.org", name: "Speicher" },
    ],
    users: [multiRoomUser, disabled, orgBot],
    serviceUserIds: new Set([orgBotId]),
    adminUserIds: new Set([adminUserId]),
    rankByClearance,
    allowedRoles,
    powerByRole,
    now: 10_001,
  });

  assert.equal(Date.parse(snapshot.expires_at) - Date.parse(snapshot.generated_at), 60_000);
  assert.deepEqual(snapshot.rooms.map(({ authorized_user_ids }) => authorized_user_ids), [
    [multiRoomUser.userId],
    [multiRoomUser.userId],
  ]);
  assert.deepEqual(snapshot.users, [{
    user_id: multiRoomUser.userId,
    keycloak_user_id: "kc-demo-21",
    matrix_access: true,
    export_admin: false,
    clearance: "vertraulich",
    direct_groups: [{ id: "chat", name: "1to1chat.orga-x.abteilung-y", path: "/1to1chat.orga-x.abteilung-y" }],
  }]);
  assert.deepEqual(snapshot.service_user_ids, [orgBotId]);
  assert.deepEqual(snapshot.admin_user_ids, [adminUserId]);
});

test("missing, expired, future-dated, or overlong snapshots are denied", () => {
  const current = {
    schema_version: 1,
    generated_at: "2026-01-01T00:00:00.000Z",
    expires_at: "2026-01-01T00:00:30.000Z",
  };
  const now = Date.parse("2026-01-01T00:00:10.000Z");
  assert.equal(isChatPolicySnapshotFresh(current, now), true);
  assert.equal(isChatPolicySnapshotFresh(null, now), false);
  assert.equal(isChatPolicySnapshotFresh({ ...current, expires_at: "2026-01-01T00:00:00.000Z" }, now), false);
  assert.equal(isChatPolicySnapshotFresh({ ...current, generated_at: "2026-01-01T00:00:20.000Z" }, now), false);
  assert.equal(isChatPolicySnapshotFresh({ ...current, expires_at: "2026-01-01T00:02:00.000Z" }, now), false);
  assert.throws(() => makeChatPolicySnapshot({
    generatedAt: now - 61_000,
    matrixServerName: "example.org",
    roomMap: [],
    users: [],
    serviceUserIds: [],
    adminUserIds: [],
    rankByClearance,
    allowedRoles,
    powerByRole,
    now,
  }), /already stale/);
});

test("removing a direct Keycloak group removes it from the next snapshot", () => {
  const user = demoUser("demo-24", "intern");
  user.id = "kc-demo-24";
  user.clearance = "intern";
  user.directGroups = [{ id: "direct-chat-1", name: "1to1chat.orga-x.abteilung-y", path: "/1to1chat.orga-x.abteilung-y" }];
  const options = {
    generatedAt: 10_000,
    ttlSeconds: 30,
    matrixServerName: "example.org",
    roomMap: [],
    users: [user],
    serviceUserIds: new Set([orgBotId]),
    adminUserIds: new Set([adminUserId]),
    rankByClearance,
    allowedRoles,
    powerByRole,
    now: 10_001,
  };
  const before = makeChatPolicySnapshot(options);
  user.directGroups = [];
  const after = makeChatPolicySnapshot(options);

  assert.deepEqual(before.users[0].direct_groups.map(({ id }) => id), ["direct-chat-1"]);
  assert.deepEqual(after.users[0].direct_groups, []);
});

test("the policy administrator is never treated as a room member grant", () => {
  const administrator = demoUser("demo-policy-admin", "streng_vertraulich", ["room.pummel.moderator"]);
  const result = plan({ users: [administrator] });
  assert.deepEqual(result.eligibleUsers, []);
  assert.equal(result.desiredPower[adminUserId], 100);
});

test("bootstrap join snapshot is short-lived and grants only current explicit room roles", () => {
  const authorized = demoUser("demo-21", "intern", ["room.pummel.reader"]);
  authorized.id = "kc-demo-21";
  authorized.clearance = "intern";
  const noRole = demoUser("demo-22", "intern");
  noRole.id = "kc-demo-22";
  noRole.clearance = "intern";
  const lowClearance = demoUser("demo-23", "basis", ["room.pummel.writer"]);
  lowClearance.id = "kc-demo-23";
  lowClearance.clearance = "basis";

  const snapshot = makeJoinPolicySnapshot({
    generatedAt: 10_000,
    ttlSeconds: 90,
    matrixServerName: "example.org",
    roomMap: [{
      ...room,
      room_id: "!actual-pummel:example.org",
      alias: "#pummel:example.org",
      name: "Pummel",
    }],
    users: [authorized, noRole, lowClearance],
    serviceUserIds: new Set([orgBotId]),
    adminUserIds: new Set([adminUserId]),
    rankByClearance,
    allowedRoles,
    powerByRole,
    now: 10_001,
  });

  assert.equal(snapshot.purpose, "managed_room_join");
  assert.equal(Date.parse(snapshot.expires_at) - Date.parse(snapshot.generated_at), 60_000);
  assert.deepEqual(snapshot.rooms.map(({ room_id, authorized_user_ids }) => ({ room_id, authorized_user_ids })), [{
    room_id: "!actual-pummel:example.org",
    authorized_user_ids: [authorized.userId],
  }]);
});
