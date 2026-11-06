import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { AccessPolicyUnavailable, loadCurrentRooms, requestedScope, resolveCurrentRooms } from "./room-access.mjs";

const now = Date.parse("2026-11-03T12:00:30.000Z");
const botId = "@orgbot:example.org";
const adminId = "@demo-policy-admin:example.org";
const aliceId = "@demo-21:example.org";
const bobId = "@demo-22:example.org";

function fixture({ roomIds = ["!managed:example.org"], aliceGroups, bobGroups, registryRooms = [] } = {}) {
  const rooms = roomIds.map((room_id, index) => ({
    id: `room-${index + 1}`,
    room_id,
    alias: `#room-${index + 1}:example.org`,
    name: `Room ${index + 1}`,
    minimum_clearance: "intern",
    test_only: false,
  }));
  const roomMap = {
    schema_version: 1,
    status: "ready",
    last_reconciled_at: new Date(now - 1000).toISOString(),
    matrix_server_name: "example.org",
    rooms,
  };
  const chatPolicy = {
    schema_version: 1,
    generated_at: new Date(now - 30_000).toISOString(),
    expires_at: new Date(now + 30_000).toISOString(),
    matrix_server_name: "example.org",
    direct_chat: { group_prefix: "1to1chat." },
    rooms: rooms.map((room) => ({ ...room, authorized_user_ids: [aliceId] })),
    users: [
      { user_id: aliceId, direct_groups: aliceGroups ?? [] },
      { user_id: bobId, direct_groups: bobGroups ?? [] },
    ],
    service_user_ids: [botId],
    admin_user_ids: [adminId],
  };
  const registry = {
    schema_version: 1,
    matrix_server_name: "example.org",
    rooms: registryRooms,
  };
  return { roomMap, chatPolicy, registry };
}

function dm(roomId = "!dm:example.org", participants = [aliceId, bobId]) {
  return {
    room_id: roomId,
    participants,
    bot_user_id: botId,
    encrypted: true,
    status: "ready",
    name: "Policy direct chat",
    minimum_clearance: "unclassified_direct_chat",
  };
}

test("a direct room is exportable only for two current users sharing the same prefixed group ID", () => {
  const { roomMap, chatPolicy, registry } = fixture({
    aliceGroups: [
      { id: "shared-id", name: "1to1chat.orga-x.abteilung-y" },
      { id: "ordinary-id", name: "orga-x.abteilung-y" },
    ],
    bobGroups: [
      { id: "shared-id", name: "1to1chat.orga-x.abteilung-y" },
      { id: "ordinary-id", name: "orga-x.abteilung-y" },
    ],
    registryRooms: [dm()],
  });
  const rooms = resolveCurrentRooms({ roomMap, chatPolicy, registry, now });

  assert.deepEqual(rooms.map(({ room_id }) => room_id), ["!dm:example.org", "!managed:example.org"]);
  assert.equal(rooms[0].kind, "direct_chat");
  assert.deepEqual(requestedScope({ scope: "all" }, rooms).roomIds, ["!dm:example.org", "!managed:example.org"]);
});

test("a previously issued export JWT cannot bypass current application or export-role withdrawal", () => {
  const input = fixture();
  input.chatPolicy.matrix_access_role = "matrix-access";
  input.chatPolicy.users[0] = { ...input.chatPolicy.users[0], keycloak_user_id: "kc-alice", matrix_access: true, export_admin: true };
  assert.equal(resolveCurrentRooms({ ...input, actorSubject: "kc-alice", now }).length, 1);
  for (const flags of [{ matrix_access: false }, { export_admin: false }]) {
    const changed = structuredClone(input);
    Object.assign(changed.chatPolicy.users[0], flags);
    assert.throws(() => resolveCurrentRooms({ ...changed, actorSubject: "kc-alice", now }), (e) => e.status === 403);
  }
  input.chatPolicy.users = [];
  assert.throws(() => resolveCurrentRooms({ ...input, actorSubject: "kc-alice", now }), (e) => e.status === 403);
  delete input.chatPolicy.matrix_access_role;
  assert.throws(() => resolveCurrentRooms({ ...input, actorSubject: "kc-alice", now }), AccessPolicyUnavailable);
});

test("ordinary groups, mismatched group IDs, or a revoked membership close a direct room", () => {
  const registry = { schema_version: 1, matrix_server_name: "example.org", rooms: [dm()] };
  const ordinary = fixture({
    aliceGroups: [{ id: "org", name: "orga-x" }],
    bobGroups: [{ id: "org", name: "orga-x" }],
    registryRooms: registry.rooms,
  });
  assert.deepEqual(resolveCurrentRooms({ ...ordinary, now }).map(({ room_id }) => room_id), ["!managed:example.org"]);

  const mismatched = fixture({
    aliceGroups: [{ id: "direct-a", name: "1to1chat.orga-x.abteilung-y" }],
    bobGroups: [{ id: "direct-b", name: "1to1chat.orga-x.abteilung-y" }],
    registryRooms: registry.rooms,
  });
  assert.deepEqual(resolveCurrentRooms({ ...mismatched, now }).map(({ room_id }) => room_id), ["!managed:example.org"]);

  const revoked = fixture({
    aliceGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
    bobGroups: [],
    registryRooms: registry.rooms,
  });
  assert.deepEqual(resolveCurrentRooms({ ...revoked, now }).map(({ room_id }) => room_id), ["!managed:example.org"]);
});

test("a pending bot room is withheld until the registry attests ready; unknown status fails closed", () => {
  const groups = {
    aliceGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
    bobGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
  };
  const pending = fixture({ ...groups, registryRooms: [{ ...dm(), status: "pending_bot" }] });
  assert.deepEqual(
    resolveCurrentRooms({ ...pending, now }).map(({ room_id }) => room_id),
    ["!managed:example.org"],
  );

  const unknown = fixture({ ...groups, registryRooms: [{ ...dm(), status: "warming_up" }] });
  assert.throws(
    () => resolveCurrentRooms({ ...unknown, now }),
    (error) => error instanceof AccessPolicyUnavailable && error.status === 503,
  );
});

test("a policy-unavailable direct room is retained in the registry but withheld from export", () => {
  const groups = {
    aliceGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
    bobGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
  };
  const unavailable = fixture({ ...groups, registryRooms: [{ ...dm(), status: "policy_unavailable" }] });
  const rooms = resolveCurrentRooms({ ...unavailable, now });
  assert.deepEqual(rooms.map(({ room_id }) => room_id), ["!managed:example.org"]);
  assert.throws(
    () => requestedScope({ scope: "selected", room_ids: ["!dm:example.org"] }, rooms),
    /outside the current export allowlist/,
  );
});

test("an absent direct-room registry leaves managed rooms available; a malformed registry fails closed", () => {
  const { roomMap, chatPolicy } = fixture();
  assert.deepEqual(resolveCurrentRooms({ roomMap, chatPolicy, now }).map(({ room_id }) => room_id), ["!managed:example.org"]);
  assert.throws(
    () => resolveCurrentRooms({ roomMap, chatPolicy, registry: { rooms: "invalid" }, now }),
    (error) => error instanceof AccessPolicyUnavailable && error.status === 503,
  );
});

test("missing, expired, mismatched, or malformed current data returns 503", () => {
  const { roomMap, chatPolicy, registry } = fixture();
  const expired = { ...chatPolicy, expires_at: new Date(now).toISOString() };
  const staleMap = { ...roomMap, last_reconciled_at: new Date(now - 60_001).toISOString() };
  const wrongMapRooms = { ...roomMap, rooms: [...roomMap.rooms, { ...roomMap.rooms[0], room_id: "!new:example.org" }] };

  for (const input of [
    { roomMap: null, chatPolicy, registry, now },
    { roomMap, chatPolicy: expired, registry, now },
    { roomMap: staleMap, chatPolicy, registry, now },
    { roomMap: wrongMapRooms, chatPolicy, registry, now },
    { roomMap, chatPolicy, registry: { schema_version: 1, matrix_server_name: "wrong", rooms: [] }, now },
  ]) {
    assert.throws(() => resolveCurrentRooms(input), (error) => error.status === 503);
  }
});

test("every request can see newly reconciled managed rooms without a restart", () => {
  const first = fixture();
  assert.deepEqual(resolveCurrentRooms({ ...first, now }).map(({ room_id }) => room_id), ["!managed:example.org"]);

  const second = fixture({ roomIds: ["!managed:example.org", "!new:example.org"] });
  assert.deepEqual(
    resolveCurrentRooms({ ...second, now }).map(({ room_id }) => room_id),
    ["!managed:example.org", "!new:example.org"],
  );
});

test("the file loader re-reads current policy on each request and fails closed when the snapshot disappears", async () => {
  const directory = await mkdtemp(join(tmpdir(), "export-policy-test-"));
  const roomMapPath = join(directory, "room-map.json");
  const policyPath = join(directory, "chat-policy.json");
  const registryPath = join(directory, "direct-rooms.json");
  try {
    const first = fixture();
    await Promise.all([
      writeFile(roomMapPath, JSON.stringify(first.roomMap)),
      writeFile(policyPath, JSON.stringify(first.chatPolicy)),
      writeFile(registryPath, JSON.stringify(first.registry)),
    ]);
    assert.deepEqual(
      (await loadCurrentRooms({ roomMapPath, chatPolicyPath: policyPath, registryPath, now })).map(({ room_id }) => room_id),
      ["!managed:example.org"],
    );

    const updated = fixture({ roomIds: ["!managed:example.org", "!new:example.org"] });
    await Promise.all([
      writeFile(roomMapPath, JSON.stringify(updated.roomMap)),
      writeFile(policyPath, JSON.stringify(updated.chatPolicy)),
    ]);
    assert.deepEqual(
      (await loadCurrentRooms({ roomMapPath, chatPolicyPath: policyPath, registryPath, now })).map(({ room_id }) => room_id),
      ["!managed:example.org", "!new:example.org"],
    );

    await rm(policyPath);
    await assert.rejects(
      loadCurrentRooms({ roomMapPath, chatPolicyPath: policyPath, registryPath, now }),
      (error) => error instanceof AccessPolicyUnavailable && error.status === 503,
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("selected export scopes are restricted to the rooms loaded for that request", () => {
  const { roomMap, chatPolicy, registry } = fixture({
    aliceGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
    bobGroups: [{ id: "shared", name: "1to1chat.orga-x.abteilung-y" }],
    registryRooms: [dm()],
  });
  const rooms = resolveCurrentRooms({ roomMap, chatPolicy, registry, now });
  assert.deepEqual(requestedScope({ scope: "selected", room_ids: ["!dm:example.org"] }, rooms), {
    scope: "selected",
    roomIds: ["!dm:example.org"],
  });
  assert.throws(() => requestedScope({ scope: "selected", room_ids: ["!unknown:example.org"] }, rooms), (error) => error.status === 403);
});
