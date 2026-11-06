import { readFile } from "node:fs/promises";
import { isMatrixRoomId } from "./room-ids.mjs";

const MAX_POLICY_AGE_MS = 60_000;

export class AccessPolicyUnavailable extends Error {
  constructor(message = "Current export authorization data is unavailable") {
    super(message);
    this.status = 503;
  }
}

function fail(message) {
  throw new AccessPolicyUnavailable(message);
}

function assertCurrentExportEntitlement(snapshot, subject) {
  if (snapshot.matrix_access_role !== "matrix-access") {
    fail("Current Matrix application entitlement is unavailable");
  }
  const people = snapshot.users.filter((user) => user.keycloak_user_id === subject);
  if (people.length > 1) fail("Current export subject mapping is ambiguous");
  if (people.length !== 1 || people[0].matrix_access !== true || people[0].export_admin !== true) {
    throw Object.assign(new Error("Current Matrix export entitlement is required"), { status: 403 });
  }
}

function timestamp(value) {
  if (typeof value !== "string") return NaN;
  return Date.parse(value);
}

function safeRoom(room) {
  return {
    room_id: room.room_id,
    name: typeof room.name === "string" ? room.name : room.room_id,
    classification: typeof room.minimum_clearance === "string" ? room.minimum_clearance : "unknown",
    test_only: room.test_only === true,
    ...(room.kind === "direct_chat" ? { kind: "direct_chat", participants: [...room.participants] } : {}),
  };
}

function validateRoomRows(rooms, label) {
  if (!Array.isArray(rooms) || rooms.length === 0) fail(`${label} has no rooms`);
  const ids = new Set();
  for (const room of rooms) {
    if (!room || !isMatrixRoomId(room.room_id) || ids.has(room.room_id)) {
      fail(`${label} contains an invalid or duplicate Matrix room ID`);
    }
    ids.add(room.room_id);
  }
  return ids;
}

function validateCurrentSnapshot(snapshot, map, now) {
  if (!map || typeof map !== "object" || Array.isArray(map) ||
      !snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    fail("Managed room map or chat policy snapshot is missing or malformed");
  }
  const generated = timestamp(snapshot?.generated_at);
  const expires = timestamp(snapshot?.expires_at);
  const server = map.matrix_server_name;
  if (snapshot?.schema_version !== 1 || typeof server !== "string" || !server ||
      snapshot.matrix_server_name !== server ||
      !Number.isFinite(generated) || !Number.isFinite(expires) ||
      !(generated <= now && now < expires) || expires - generated <= 0 ||
      expires - generated > MAX_POLICY_AGE_MS) {
    fail("Chat policy snapshot is missing, invalid, or expired");
  }
  if (!Array.isArray(snapshot.users) || !Array.isArray(snapshot.rooms) ||
      !Array.isArray(snapshot.service_user_ids) || !Array.isArray(snapshot.admin_user_ids)) {
    fail("Chat policy snapshot has an invalid schema");
  }
  const prefix = snapshot.direct_chat?.group_prefix;
  if (typeof prefix !== "string" || !prefix || !prefix.endsWith(".")) {
    fail("Chat policy snapshot has an invalid direct-chat group namespace");
  }
  const managedIds = validateRoomRows(map.rooms, "Managed room map");
  const snapshotIds = validateRoomRows(snapshot.rooms, "Chat policy snapshot");
  if (managedIds.size !== snapshotIds.size || [...managedIds].some((id) => !snapshotIds.has(id))) {
    fail("Managed room map and chat policy snapshot do not describe the same rooms");
  }
  const mapTime = timestamp(map.last_reconciled_at);
  if (!Number.isFinite(mapTime) || mapTime > now || now - mapTime > MAX_POLICY_AGE_MS) {
    fail("Managed room map is missing, invalid, or stale");
  }
  if (map.status !== "ready" || map.schema_version !== 1) {
    fail("Managed room map is not ready");
  }
  for (const list of [snapshot.service_user_ids, snapshot.admin_user_ids]) {
    if (list.some((value) => typeof value !== "string" || !value)) fail("Chat policy service identity list is invalid");
  }

  const users = new Map();
  for (const user of snapshot.users) {
    if (!user || typeof user.user_id !== "string" || !user.user_id || users.has(user.user_id) ||
        !Array.isArray(user.direct_groups)) {
      fail("Chat policy user list is invalid");
    }
    users.set(user.user_id, user);
  }
  return {
    users,
    prefix,
    serviceUserIds: new Set(snapshot.service_user_ids),
    excludedUserIds: new Set([...snapshot.service_user_ids, ...snapshot.admin_user_ids]),
  };
}

function sharedDirectGroup(users, prefix, first, second) {
  if (first === second || !users.has(first) || !users.has(second)) return false;
  const firstGroups = directGroupIds(users.get(first), prefix);
  const secondGroups = directGroupIds(users.get(second), prefix);
  return [...firstGroups].some((groupId) => secondGroups.has(groupId));
}

function directGroupIds(user, prefix) {
  const result = new Set();
  for (const group of user?.direct_groups || []) {
    if (group && typeof group.name === "string" && group.name.startsWith(prefix) &&
        group.name.length > prefix.length && typeof group.id === "string" && group.id) {
      result.add(group.id);
    }
  }
  return result;
}

function mergeDirectRooms(managedRooms, registry, current, server) {
  if (registry === undefined) return managedRooms;
  if (registry?.schema_version !== 1 || registry.matrix_server_name !== server || !Array.isArray(registry.rooms)) {
    fail("Direct-room registry is malformed");
  }
  const rooms = new Map(managedRooms.map((room) => [room.room_id, room]));
  const seen = new Set();
  for (const entry of registry.rooms) {
    if (!entry || !isMatrixRoomId(entry.room_id) || seen.has(entry.room_id) ||
        !Array.isArray(entry.participants) || entry.participants.length !== 2 ||
        new Set(entry.participants).size !== 2 || entry.encrypted !== true ||
        !["pending_bot", "ready", "policy_unavailable"].includes(entry.status) ||
        typeof entry.bot_user_id !== "string" || !current.serviceUserIds.has(entry.bot_user_id)) {
      fail("Direct-room registry contains an invalid room entry");
    }
    seen.add(entry.room_id);
    if (entry.participants.some((userId) => typeof userId !== "string" ||
        !userId.endsWith(`:${server}`) || current.excludedUserIds.has(userId))) {
      fail("Direct-room registry contains a non-local participant");
    }
    // The registry records rooms before the bot has joined and verified the
    // encryption state. Such rooms are valid but must not be offered to export.
    if (entry.status !== "ready") continue;
    if (!sharedDirectGroup(current.users, current.prefix, entry.participants[0], entry.participants[1])) continue;
    if (rooms.has(entry.room_id)) fail("Direct-room registry collides with a managed room ID");
    rooms.set(entry.room_id, safeRoom({
      room_id: entry.room_id,
      name: typeof entry.name === "string" ? entry.name : "Policy direct chat",
      minimum_clearance: typeof entry.minimum_clearance === "string"
        ? entry.minimum_clearance
        : "unclassified_direct_chat",
      kind: "direct_chat",
      participants: entry.participants,
    }));
  }
  return [...rooms.values()];
}

export function resolveCurrentRooms({ roomMap, chatPolicy, registry, actorSubject, now = Date.now() }) {
  const current = validateCurrentSnapshot(chatPolicy, roomMap, now);
  if (actorSubject !== undefined) assertCurrentExportEntitlement(chatPolicy, actorSubject);
  const managedIds = validateRoomRows(roomMap.rooms, "Managed room map");
  const snapshotRooms = new Map(chatPolicy.rooms.map((room) => [room.room_id, room]));
  const managedRooms = roomMap.rooms.map((room) => {
    const policyRoom = snapshotRooms.get(room.room_id);
    if (!Array.isArray(policyRoom.authorized_user_ids) ||
        policyRoom.authorized_user_ids.some((userId) => typeof userId !== "string" || !userId)) {
      fail("Chat policy room authorization list is invalid");
    }
    return safeRoom(room);
  });
  if (managedIds.size !== managedRooms.length) fail("Managed room map is invalid");
  return mergeDirectRooms(managedRooms, registry, current, roomMap.matrix_server_name)
    .sort((left, right) => left.room_id.localeCompare(right.room_id));
}

async function readJson(filename, label, { optional = false } = {}) {
  try {
    return JSON.parse(await readFile(filename, "utf8"));
  } catch (error) {
    if (optional && error.code === "ENOENT") return undefined;
    fail(`${label} is unavailable or malformed`);
  }
}

export async function loadCurrentRooms({ roomMapPath, chatPolicyPath, registryPath, actorSubject, now = Date.now() }) {
  if (!roomMapPath || !chatPolicyPath) fail("Dynamic room authorization is not fully configured");
  const [roomMap, chatPolicy, registry] = await Promise.all([
    readJson(roomMapPath, "Managed room map"),
    readJson(chatPolicyPath, "Chat policy snapshot"),
    registryPath ? readJson(registryPath, "Direct-room registry", { optional: true }) : undefined,
  ]);
  return resolveCurrentRooms({ roomMap, chatPolicy, registry, actorSubject, now });
}

export function legacyRooms(roomIds) {
  if (!Array.isArray(roomIds) || roomIds.length === 0) fail("No export room allowlist is configured");
  const ids = new Set();
  const rooms = roomIds.map((roomId) => {
    if (!isMatrixRoomId(roomId) || ids.has(roomId)) fail("Legacy export room allowlist is invalid");
    ids.add(roomId);
    return { room_id: roomId, name: roomId, classification: "unknown", test_only: false };
  });
  return rooms.sort((left, right) => left.room_id.localeCompare(right.room_id));
}

export function requestedScope(body, rooms) {
  const allowedRoomIds = new Set(rooms.map((room) => room.room_id));
  if (body?.scope === "all") {
    if (allowedRoomIds.size === 0) fail("No organization room allowlist is configured");
    return { scope: "all", roomIds: [...allowedRoomIds].sort() };
  }
  if (body?.scope !== "selected" || !Array.isArray(body.room_ids) || body.room_ids.length === 0) {
    const error = new Error("Use scope=selected with one or more room_ids, or scope=all");
    error.status = 400;
    throw error;
  }
  if (body.room_ids.some((roomId) => typeof roomId !== "string" || roomId.length > 255)) {
    const error = new Error("Every room_id must be a string no longer than 255 characters");
    error.status = 400;
    throw error;
  }
  const roomIds = [...new Set(body.room_ids)].sort();
  if (roomIds.some((roomId) => !allowedRoomIds.has(roomId))) {
    const error = new Error("Requested room is outside the current export allowlist");
    error.status = 403;
    throw error;
  }
  return { scope: "selected", roomIds };
}
