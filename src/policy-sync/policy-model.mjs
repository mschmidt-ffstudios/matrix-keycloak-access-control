export function makeManagedRoomMapEntry({ room, roomId, matrixServerName, rankByClearance }) {
  const rank = rankByClearance.get(room.minimum_clearance);
  if (!Number.isInteger(rank) || rank < 0) {
    throw new Error("Cannot publish room metadata for an unknown clearance");
  }
  return {
    id: room.id,
    room_id: roomId,
    alias: `#${room.alias_localpart}:${matrixServerName}`,
    alias_localpart: room.alias_localpart,
    name: room.name,
    test_only: room.test_only === true,
    minimum_clearance: room.minimum_clearance,
    minimum_clearance_rank: rank,
    classification: room.minimum_clearance,
    authorization_basis: "keycloak_room_role_and_clearance",
  };
}

export async function publishRoomMapBeforeBotReadiness({
  roomMapEntry,
  preparedRooms,
  managedUserCount,
  isRoomInReadyMap,
  writeRoomMap,
  waitForReadiness,
}) {
  if (!roomMapEntry || !Array.isArray(preparedRooms) ||
      typeof isRoomInReadyMap !== "function" || typeof writeRoomMap !== "function" ||
      typeof waitForReadiness !== "function") {
    throw new TypeError("A validated room entry and map/readiness callbacks are required");
  }
  const rooms = [...preparedRooms, roomMapEntry];
  const mapAlreadyRecognizesRoom = await isRoomInReadyMap(roomMapEntry.room_id);
  if (!mapAlreadyRecognizesRoom) {
    await writeRoomMap(rooms, managedUserCount, "provisioning");
  }
  await waitForReadiness(roomMapEntry.room_id);
  return { rooms, publishedProvisioningMap: !mapAlreadyRecognizesRoom };
}

export function parseRoomRole(roleName, rolePattern, allowedRoles) {
  const pattern = rolePattern || "room.{room_id}.{role}";
  const roomMarker = "{room_id}";
  const roleMarker = "{role}";
  const roomIndex = pattern.indexOf(roomMarker);
  const roleIndex = pattern.indexOf(roleMarker);
  if (roomIndex < 0 || roleIndex < roomIndex + roomMarker.length) return null;
  const prefix = pattern.slice(0, roomIndex);
  const separator = pattern.slice(roomIndex + roomMarker.length, roleIndex);
  const suffix = pattern.slice(roleIndex + roleMarker.length);
  if (!separator || !roleName.startsWith(prefix) || (suffix && !roleName.endsWith(suffix))) return null;
  const end = suffix ? roleName.length - suffix.length : roleName.length;
  const value = roleName.slice(prefix.length, end);
  const separatorIndex = value.lastIndexOf(separator);
  if (separatorIndex <= 0 || separatorIndex + separator.length >= value.length) return null;
  const roomId = value.slice(0, separatorIndex);
  const role = value.slice(separatorIndex + separator.length);
  if (!/^[a-z0-9_-]+$/i.test(roomId) || !allowedRoles.has(role)) return null;
  return { roomId, role };
}

export function isManagedDemoUser(user, clearanceAttribute, rankByClearance) {
  if (!user?.id || typeof user.username !== "string" ||
      !/^(?:demo-[a-z0-9][a-z0-9._-]*|export-test-[0-9]{2})$/i.test(user.username)) return false;
  if (user.serviceAccountClientId || user.username.startsWith("service-account-")) return false;
  const value = user.attributes?.[clearanceAttribute];
  const clearance = Array.isArray(value) ? (value.length === 1 ? value[0] : null) : value;
  return typeof clearance === "string" && rankByClearance.has(clearance);
}

export function roleForRoom(user, room, { rankByClearance, allowedRoles, powerByRole }) {
  const roles = user.roomRoleMap.get(room.id) || [];
  const roomRank = rankByClearance.get(room.minimum_clearance);
  if (!user.enabled || user.matrixAccess !== true || user.rank === null || user.rank < roomRank) return null;
  if (roles.length !== 1) return null;
  const role = roles[0];
  if (!allowedRoles.has(role) || !Number.isInteger(powerByRole[role])) return null;
  return role;
}

export function buildRoomPlan({
  room,
  users,
  currentMemberships,
  adminUserId,
  serviceUserIds,
  servicePower,
  rankByClearance,
  allowedRoles,
  powerByRole,
}) {
  const authorizedServiceIds = new Set(serviceUserIds);
  const roleOptions = { rankByClearance, allowedRoles, powerByRole };
  const eligibleUsers = users.filter((user) =>
    user.userId !== adminUserId && !authorizedServiceIds.has(user.userId) &&
      roleForRoom(user, room, roleOptions) !== null,
  );
  const eligibleIds = new Set(eligibleUsers.map((user) => user.userId));
  const desiredPower = { [adminUserId]: servicePower };
  for (const userId of authorizedServiceIds) desiredPower[userId] = 0;
  for (const user of eligibleUsers) {
    desiredPower[user.userId] = powerByRole[roleForRoom(user, room, roleOptions)];
  }

  const revokeUserIds = [];
  for (const [userId, membership] of currentMemberships) {
    if (["join", "invite"].includes(membership) && userId !== adminUserId &&
        !eligibleIds.has(userId) && !authorizedServiceIds.has(userId)) {
      revokeUserIds.push(userId);
    }
  }
  const joinUserIds = eligibleUsers
    .filter((user) => currentMemberships.get(user.userId) !== "join")
    .map((user) => user.userId);
  const joinServiceUserIds = [...authorizedServiceIds].filter((userId) => currentMemberships.get(userId) !== "join");
  const joinSequence = [
    ...joinServiceUserIds.map((userId) => ({ kind: "service", userId })),
    ...joinUserIds.map((userId) => ({ kind: "demo", userId })),
  ];

  return { eligibleUsers, desiredPower, revokeUserIds, joinServiceUserIds, joinUserIds, joinSequence };
}

export async function fetchAllKeycloakPages(fetchPage, { pageSize = 1000, maxItems = 1_000_000 } = {}) {
  if (typeof fetchPage !== "function" || !Number.isInteger(pageSize) || pageSize < 1 ||
      !Number.isInteger(maxItems) || maxItems < pageSize) {
    throw new TypeError("Invalid Keycloak pagination options");
  }
  const items = [];
  const seenIds = new Set();
  for (let first = 0; ; first += pageSize) {
    if (first >= maxItems) throw new Error("Keycloak pagination exceeded the safety limit");
    const page = await fetchPage(first, pageSize);
    if (!Array.isArray(page)) throw new Error("Keycloak returned a non-array page");
    if (items.length + page.length > maxItems) throw new Error("Keycloak result exceeded the safety limit");
    let newItems = 0;
    for (const item of page) {
      const id = item?.id;
      if (typeof id !== "string" || !id) throw new Error("Keycloak returned an item without an ID");
      if (seenIds.has(id)) continue;
      seenIds.add(id);
      items.push(item);
      newItems += 1;
    }
    if (page.length < pageSize) return items;
    if (newItems === 0) throw new Error("Keycloak pagination repeated a full page without progress");
  }
}

export function directChatGroups(groups, groupPrefix = "1to1chat.") {
  if (typeof groupPrefix !== "string" || groupPrefix.length === 0) {
    throw new TypeError("The direct-chat group prefix must be a non-empty string");
  }
  if (!Array.isArray(groups)) throw new TypeError("Keycloak groups must be an array");
  return groups
    .filter((group) => {
      return typeof group?.id === "string" && group.id &&
        typeof group.name === "string" && group.name.startsWith(groupPrefix) &&
        group.name.length > groupPrefix.length;
    })
    .map(({ id, name, path }) => ({ id, name: name ?? null, path: path ?? null }));
}

export function makeChatPolicySnapshot({
  generatedAt,
  ttlSeconds = 60,
  matrixServerName,
  groupPrefix = "1to1chat.",
  roomMap,
  users,
  serviceUserIds,
  adminUserIds,
  rankByClearance,
  allowedRoles,
  powerByRole,
  now = Date.now(),
}) {
  const generatedMs = typeof generatedAt === "number" ? generatedAt : Date.parse(generatedAt);
  const ttlMs = Math.min(60, Number(ttlSeconds)) * 1000;
  if (!Number.isFinite(generatedMs) || !Number.isFinite(ttlMs) || ttlMs <= 0 ||
      !Number.isFinite(now) || typeof matrixServerName !== "string" || !matrixServerName ||
      !Array.isArray(roomMap) || !Array.isArray(users)) {
    throw new Error("Cannot create an invalid chat-policy snapshot");
  }
  const expiresMs = generatedMs + ttlMs;
  if (generatedMs > now || expiresMs <= now) {
    throw new Error("Keycloak policy data is already stale; refusing to publish a chat-policy snapshot");
  }
  const serviceIds = new Set(serviceUserIds);
  const adminIds = new Set(adminUserIds);
  const eligibleUsers = users.filter((user) => user.enabled === true && user.matrixAccess === true && user.rank !== null &&
    !serviceIds.has(user.userId) && !adminIds.has(user.userId));
  const roleOptions = { rankByClearance, allowedRoles, powerByRole };

  return {
    schema_version: 1,
    generated_at: new Date(generatedMs).toISOString(),
    expires_at: new Date(expiresMs).toISOString(),
    matrix_server_name: matrixServerName,
    matrix_access_role: "matrix-access",
    direct_chat: { group_prefix: groupPrefix },
    rooms: roomMap.map((room) => ({
      room_id: room.room_id,
      logical_id: room.id,
      alias: room.alias,
      name: room.name,
      minimum_clearance: room.minimum_clearance,
      authorized_user_ids: eligibleUsers
        .filter((user) => roleForRoom(user, room, roleOptions) !== null)
        .map((user) => user.userId)
        .sort(),
    })),
    users: eligibleUsers.map((user) => ({
      user_id: user.userId,
      keycloak_user_id: user.id,
      matrix_access: true,
      export_admin: user.exportAdmin === true,
      clearance: user.clearance,
      direct_groups: directChatGroups(user.directGroups || [], groupPrefix),
    })).sort((left, right) => left.user_id.localeCompare(right.user_id)),
    service_user_ids: [...serviceIds].sort(),
    admin_user_ids: [...adminIds].sort(),
  };
}

export function isChatPolicySnapshotFresh(snapshot, now = Date.now()) {
  if (!snapshot || snapshot.schema_version !== 1 || !Number.isFinite(now)) return false;
  const generatedMs = Date.parse(snapshot.generated_at);
  const expiresMs = Date.parse(snapshot.expires_at);
  return Number.isFinite(generatedMs) && Number.isFinite(expiresMs) &&
    generatedMs <= now && expiresMs > now && expiresMs > generatedMs &&
    expiresMs - generatedMs <= 60_000;
}

export function makeJoinPolicySnapshot(options) {
  return {
    ...makeChatPolicySnapshot(options),
    purpose: "managed_room_join",
  };
}
