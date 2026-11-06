import assert from "node:assert/strict";
import test from "node:test";
import { AccessPolicyUnavailable, resolveCurrentRooms } from "../export-service/room-access.mjs";
import { publishRoomMapBeforeBotReadiness } from "./policy-model.mjs";

test("publishes the current room in a provisioning map before OrgBot readiness is polled", async () => {
  const events = [];
  let published;
  const roomMapEntry = {
    id: "abteilung_x",
    room_id: "!deptx:example.org",
    name: "Abteilung X",
    minimum_clearance: "basis",
    classification: "basis",
    authorization_basis: "keycloak_room_role_and_clearance",
  };
  const preparedRooms = [{
    id: "allgemein",
    room_id: "!general:example.org",
    name: "Allgemein",
    minimum_clearance: "basis",
    classification: "basis",
    authorization_basis: "keycloak_room_role_and_clearance",
  }];

  const result = await publishRoomMapBeforeBotReadiness({
    roomMapEntry,
    preparedRooms,
    managedUserCount: 24,
    isRoomInReadyMap: async () => false,
    writeRoomMap: async (rooms, count, status) => {
      published = { rooms, managedUserCount: count, status };
      events.push("publish-map");
    },
    waitForReadiness: async (roomId) => {
      assert.equal(roomId, roomMapEntry.room_id);
      assert.equal(published.status, "provisioning");
      assert.ok(published.rooms.some((room) => room.room_id === roomId));
      events.push("poll-orgbot-readiness");
    },
  });

  assert.deepEqual(events, ["publish-map", "poll-orgbot-readiness"]);
  assert.equal(published.managedUserCount, 24);
  assert.equal(result.publishedProvisioningMap, true);
  assert.deepEqual(result.rooms.map((room) => room.room_id), ["!general:example.org", "!deptx:example.org"]);
});

test("keeps the ready map in place during ordinary reconciliation", async () => {
  const events = [];
  const result = await publishRoomMapBeforeBotReadiness({
    roomMapEntry: { room_id: "!managed:example.org" },
    preparedRooms: [],
    managedUserCount: 24,
    isRoomInReadyMap: async () => true,
    writeRoomMap: async () => events.push("unexpected-map-write"),
    waitForReadiness: async () => events.push("readiness"),
  });

  assert.deepEqual(events, ["readiness"]);
  assert.equal(result.publishedProvisioningMap, false);
});

test("export rejects a provisioning room map even when its policy snapshot is fresh", () => {
  const now = Date.parse("2026-11-04T08:00:00.000Z");
  const roomId = "!deptx:example.org";
  const roomMap = {
    schema_version: 1,
    status: "provisioning",
    last_reconciled_at: new Date(now - 1000).toISOString(),
    matrix_server_name: "example.org",
    rooms: [{
      id: "abteilung_x",
      room_id: roomId,
      name: "Abteilung X",
      minimum_clearance: "basis",
      classification: "basis",
      authorization_basis: "keycloak_room_role_and_clearance",
    }],
  };
  const chatPolicy = {
    schema_version: 1,
    generated_at: new Date(now - 1000).toISOString(),
    expires_at: new Date(now + 59_000).toISOString(),
    matrix_server_name: "example.org",
    direct_chat: { group_prefix: "1to1chat." },
    rooms: [{ room_id: roomId, authorized_user_ids: [] }],
    users: [],
    service_user_ids: ["@orgbot:example.org"],
    admin_user_ids: ["@demo-policy-admin:example.org"],
  };

  assert.throws(
    () => resolveCurrentRooms({ roomMap, chatPolicy, now }),
    (error) => error instanceof AccessPolicyUnavailable && error.status === 503 && /not ready/.test(error.message),
  );
});
