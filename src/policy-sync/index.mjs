import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { demoProfileDisplayName, ensureMasBackedUser, isReservedMatrixIdentity } from "./user-admission.mjs";
import { matrixEntitlement, reconcileMatrixEntitlement } from "./matrix-access.mjs";
import {
  buildRoomPlan,
  directChatGroups,
  fetchAllKeycloakPages,
  isManagedDemoUser,
  makeChatPolicySnapshot,
  makeJoinPolicySnapshot,
  makeManagedRoomMapEntry,
  publishRoomMapBeforeBotReadiness,
  parseRoomRole,
} from "./policy-model.mjs";

const roomMapOutput = path.resolve(process.env.ROOM_MAP_OUTPUT || "/data/room-map.json");
const chatPolicyOutput = path.resolve(process.env.CHAT_POLICY_OUTPUT || "/data/chat-policy.json");
const joinPolicyOutput = path.resolve(process.env.JOIN_POLICY_OUTPUT || "/data/join-policy.json");
await Promise.all([clearRoomMap(), clearChatPolicy(), clearJoinPolicy()]);

const matrixServerName = required("MATRIX_SERVER_NAME");
const synapseAdminTokenFile = process.env.SYNAPSE_ADMIN_TOKEN_FILE || "/run/mas-bootstrap/synapse-admin-token";
const keycloakClientSecret = required("KEYCLOAK_POLICY_CLIENT_SECRET");
const keycloakBase = (process.env.KEYCLOAK_PUBLIC_URL || "http://keycloak:8080/auth").replace(/\/$/, "");
const realm = process.env.KEYCLOAK_REALM || "foldfence-demo";
const keycloakServiceClientId = process.env.KEYCLOAK_SERVICE_CLIENT_ID || "matrix-policy-sync";
const roomRoleClientId = process.env.KEYCLOAK_ROLE_CLIENT_ID || "matrix-synapse";
const configuredSyncInterval = Number(process.env.POLICY_SYNC_INTERVAL_SECONDS || 20);
const syncInterval = (Number.isFinite(configuredSyncInterval) ? Math.max(10, configuredSyncInterval) : 20) * 1000;
const synapse = "http://synapse:8008";
const adminLocalpart = process.env.SYNAPSE_ADMIN_USER || "demo-policy-admin";
const provisionerBase = (process.env.MAS_PROVISIONER_API_URL || "http://provisioner:8091").replace(/\/$/, "");
const provisionerToken = required("MAS_PROVISIONER_API_TOKEN");
const matrixServiceUserIds = new Set(
  (process.env.MATRIX_SERVICE_USER_IDS || "").split(",").map((userId) => userId.trim()).filter(Boolean),
);
const orgBotReadinessBase = (process.env.ORG_BOT_READINESS_URL || "http://org-bot:8090").replace(/\/$/, "");
const orgBotReadinessToken = process.env.ORG_BOT_READINESS_TOKEN || "";
const configuredReadinessTimeoutSeconds = Number(process.env.ORG_BOT_READINESS_TIMEOUT_SECONDS || 120);
const orgBotReadinessTimeoutMs = (Number.isFinite(configuredReadinessTimeoutSeconds)
  ? Math.min(600, Math.max(1, configuredReadinessTimeoutSeconds))
  : 120) * 1000;
const configuredReadinessPollSeconds = Number(process.env.ORG_BOT_READINESS_POLL_INTERVAL_SECONDS || 2);
const orgBotReadinessPollMs = (Number.isFinite(configuredReadinessPollSeconds)
  ? Math.min(30, Math.max(0.25, configuredReadinessPollSeconds))
  : 2) * 1000;

const usersSeed = JSON.parse(await fs.readFile("/app/config/demo-users.json", "utf8"));
const policy = JSON.parse(await fs.readFile("/app/config/room-access.json", "utf8"));
const clearanceLevels = policy.clearance_levels || usersSeed.clearance_levels;
const rankByClearance = new Map(clearanceLevels.map((entry) => [entry.id, entry.rank]));
const allowedRoles = new Set(policy.roles || Object.keys(policy.matrix_roles || {}));
const powerByRole = policy.role_power_levels || Object.fromEntries(
  Object.entries(policy.matrix_roles || {}).map(([role, config]) => [role, config.power_level]),
);
const configuredPowerLevels = policy.matrix_power_levels || {};
const servicePower = configuredPowerLevels.policy_service_power_level ?? configuredPowerLevels.invite ?? 100;
const knownRooms = new Set((policy.rooms || []).map((room) => room.id));
const clearanceAttribute = policy.clearance_attribute || "security_clearance";
const directChatPolicy = policy.direct_chats || {};
const directChatGroupPrefix = directChatPolicy.group_prefix || "1to1chat.";
const configuredChatPolicyTtl = directChatPolicy.snapshot_ttl_seconds ?? 60;
const chatPolicyTtlSeconds = typeof configuredChatPolicyTtl === "number" &&
  Number.isFinite(configuredChatPolicyTtl) && configuredChatPolicyTtl > 0
  ? Math.min(60, configuredChatPolicyTtl)
  : null;
let keycloakTokenCache = null;
let keycloakTokenExpiresAt = 0;

if (!policy.rooms?.length || !usersSeed.users?.length || rankByClearance.size === 0) {
  throw new Error("Policy or seed data is empty; refusing to start with an allow-all fallback.");
}

validateConfiguration();

function required(name) {
  const value = process.env[name];
  if (!value || value === "CHANGE_ME") throw new Error(`${name} is missing or still a placeholder`);
  return value;
}

function validateConfiguration() {
  if (policy.default_decision !== "deny") {
    throw new Error("room-access.json must explicitly set default_decision to deny");
  }
  if (typeof directChatPolicy !== "object" || Array.isArray(directChatPolicy) ||
      typeof directChatGroupPrefix !== "string" || !directChatGroupPrefix.endsWith(".") ||
      chatPolicyTtlSeconds === null) {
    throw new Error("direct_chats must use a dotted group_prefix and positive numeric snapshot_ttl_seconds");
  }
  if (!clearanceLevels.every((entry) => typeof entry.id === "string" && Number.isInteger(entry.rank)) ||
      new Set(clearanceLevels.map((entry) => entry.id)).size !== clearanceLevels.length) {
    throw new Error("clearance_levels must contain unique IDs with integer ranks");
  }
  if (!allowedRoles.size || [...allowedRoles].some((role) => typeof role !== "string" || !role)) {
    throw new Error("roles must list at least one non-empty role");
  }
  const rolePattern = policy.keycloak_role_pattern || "room.{room_id}.{role}";
  const roomMarker = "{room_id}";
  const roleMarker = "{role}";
  const roomIndex = rolePattern.indexOf(roomMarker);
  const roleIndex = rolePattern.indexOf(roleMarker);
  if ((rolePattern.match(/\{room_id\}/g) || []).length !== 1 ||
      (rolePattern.match(/\{role\}/g) || []).length !== 1 ||
      roleIndex <= roomIndex ||
      !rolePattern.slice(roomIndex + roomMarker.length, roleIndex)) {
    throw new Error("keycloak_role_pattern must contain {room_id} followed by {role} exactly once");
  }
  if (!Number.isInteger(servicePower) || servicePower < 1 || servicePower > 100) {
    throw new Error("matrix_power_levels must grant the policy service a valid power level");
  }
  for (const userId of matrixServiceUserIds) {
    const serverSuffix = `:${matrixServerName}`;
    const localpart = userId.startsWith("@") && userId.endsWith(serverSuffix)
      ? userId.slice(1, -serverSuffix.length)
      : "";
    if (!/^[a-z0-9][a-z0-9._=-]*$/i.test(localpart)) {
      throw new Error(`MATRIX_SERVICE_USER_IDS contains a non-local Matrix user ID: ${userId}`);
    }
  }
  if (matrixServiceUserIds.size > 0 && !orgBotReadinessToken) {
    throw new Error("ORG_BOT_READINESS_TOKEN is required when MATRIX_SERVICE_USER_IDS is configured");
  }
  for (const role of allowedRoles) {
    const power = powerByRole[role];
    if (!Number.isInteger(power) || power < 0 || power >= servicePower) {
      throw new Error(`Role ${role} must have a valid power below the policy service power`);
    }
  }
  for (const room of policy.rooms) {
    if (!/^[a-z0-9_-]+$/i.test(room.id) || !/^[a-z0-9._=-]+$/i.test(room.alias_localpart || "")) {
      throw new Error(`Room ${room.id} has an invalid ID or alias localpart`);
    }
    if (!rankByClearance.has(room.minimum_clearance)) {
      throw new Error(`Room ${room.id} references an unknown minimum clearance`);
    }
    if (room.encrypted !== true || room.join_rule !== "invite" || room.history_visibility !== "joined") {
      throw new Error(`Room ${room.id} must be encrypted, invite-only, and limited to joined history`);
    }
  }
  const controlledThresholds = [
    ["invite", configuredPowerLevels.invite],
    ["kick", configuredPowerLevels.kick],
    ["ban", configuredPowerLevels.ban],
    ["state_default", configuredPowerLevels.state_default],
    ["events_default", configuredPowerLevels.events_default],
    ["m.room.power_levels", configuredPowerLevels.events?.["m.room.power_levels"]],
    ["m.room.member", configuredPowerLevels.events?.["m.room.member"]],
    ["m.room.join_rules", configuredPowerLevels.events?.["m.room.join_rules"]],
    ["m.room.history_visibility", configuredPowerLevels.events?.["m.room.history_visibility"]],
    ["m.room.encryption", configuredPowerLevels.events?.["m.room.encryption"]],
  ];
  for (const [name, threshold] of controlledThresholds) {
    if (!Number.isInteger(threshold) || threshold < servicePower) {
      throw new Error(`matrix_power_levels.${name} must be at least the policy service power`);
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mxid(localpart) {
  if (!/^[a-z0-9][a-z0-9._=-]*$/i.test(localpart)) throw new Error(`Unsafe Matrix localpart in seed: ${localpart}`);
  return `@${localpart}:${matrixServerName}`;
}

class HttpError extends Error {
  constructor(method, url, status, detail) {
    super(`${method} ${new URL(url).pathname} returned ${status}`);
    this.status = status;
    this.method = method;
    this.url = url;
  }
}

async function request(base, path, { method = "GET", token, body, headers = {}, expected = [], timeoutMs = 15000 } = {}) {
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const relativePath = path.replace(/^\/+/, "");
  const url = new URL(relativePath, normalizedBase).toString();
  const response = await fetch(url, {
    method,
    headers: {
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const raw = await response.text();
  let parsed = null;
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = { error: raw.slice(0, 500) };
    }
  }
  if (!response.ok && !expected.includes(response.status)) {
    throw new HttpError(method, url, response.status, "request failed");
  }
  return { status: response.status, data: parsed };
}

async function getKeycloakToken() {
  if (keycloakTokenCache && Date.now() < keycloakTokenExpiresAt) return keycloakTokenCache;
  const response = await fetch(`${keycloakBase}/realms/${encodeURIComponent(realm)}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "client_credentials",
      client_id: keycloakServiceClientId,
      client_secret: keycloakClientSecret,
    }),
    signal: AbortSignal.timeout(15000),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.access_token) {
    throw new Error(`Keycloak service-account token request failed (${response.status})`);
  }
  keycloakTokenCache = result.access_token;
  keycloakTokenExpiresAt = Date.now() + Math.max(1, Number(result.expires_in || 300) - 60) * 1000;
  return keycloakTokenCache;
}

async function loginSynapseAdmin() {
  const token = (await fs.readFile(synapseAdminTokenFile, "utf8")).trim();
  if (!token) throw new Error("Matrix Authentication Service has not issued the private Synapse admin token yet");
  return token;
}

async function getKeycloakUsers(token) {
  const realmPath = `/admin/realms/${encodeURIComponent(realm)}/users`;
  return fetchAllKeycloakPages(async (first, max) => {
    const response = await request(keycloakBase, `${realmPath}?first=${first}&max=${max}&briefRepresentation=false`, { token });
    return response.data;
  });
}

async function getKeycloakGroups(token, userId) {
  const groupsPath = `/admin/realms/${encodeURIComponent(realm)}/users/${encodeURIComponent(userId)}/groups`;
  const groups = await fetchAllKeycloakPages(async (first, max) => {
    const response = await request(keycloakBase, `${groupsPath}?first=${first}&max=${max}&briefRepresentation=false`, { token });
    return response.data;
  });
  return directChatGroups(groups, directChatGroupPrefix);
}

async function getClientUuid(token) {
  const response = await request(
    keycloakBase,
    `/admin/realms/${encodeURIComponent(realm)}/clients?clientId=${encodeURIComponent(roomRoleClientId)}`,
    { token },
  );
  const client = (response.data || []).find((item) => item.clientId === roomRoleClientId);
  if (!client?.id) throw new Error(`Keycloak room-role client ${roomRoleClientId} was not found`);
  return client.id;
}

async function keycloakRoles(token, clientUuid, userId) {
  const response = await request(
    keycloakBase,
    `/admin/realms/${encodeURIComponent(realm)}/users/${encodeURIComponent(userId)}/role-mappings/clients/${encodeURIComponent(clientUuid)}`,
    { token },
  );
  return new Set((response.data || []).map((role) => role.name));
}

function normalizeUser(kcUser, roles, effectiveRealmRoles) {
  const clearanceValues = kcUser.attributes?.[clearanceAttribute];
  const clearance = Array.isArray(clearanceValues)
    ? (clearanceValues.length === 1 ? clearanceValues[0] : null)
    : clearanceValues;
  const rank = rankByClearance.get(clearance);
  const roomRoleMap = new Map();
  for (const roleName of roles) {
    const parsed = parseRoomRole(roleName, policy.keycloak_role_pattern, allowedRoles);
    if (!parsed) continue;
    const { roomId, role } = parsed;
    if (!knownRooms.has(roomId)) continue;
    if (!roomRoleMap.has(roomId)) roomRoleMap.set(roomId, []);
    roomRoleMap.get(roomId).push(role);
  }
  const localpart = kcUser.username;
  const displayName = demoProfileDisplayName(kcUser, localpart);
  return {
    id: kcUser.id,
    username: localpart,
    userId: mxid(localpart),
    displayName,
    ...matrixEntitlement(kcUser, effectiveRealmRoles),
    clearance,
    rank: Number.isInteger(rank) ? rank : null,
    roomRoleMap,
  };
}

async function ensureSynapseUser(token, user) {
  await ensureMasBackedUser({
    user, request, sleep, provisionerBase, provisionerToken,
    synapseBase: synapse, matrixToken: token,
  });
}

const basePowerLevels = () => {
  const configured = configuredPowerLevels;
  return {
    users_default: configured.users_default ?? 0,
    events_default: Math.max(servicePower, configured.events_default ?? 100),
    state_default: Math.max(servicePower, configured.state_default ?? 100),
    invite: Math.max(servicePower, configured.invite ?? 100),
    kick: Math.max(servicePower, configured.kick ?? 100),
    ban: Math.max(servicePower, configured.ban ?? 100),
    redact: configured.redact ?? 50,
    events: {
      ...(configured.events || {}),
      "m.room.message": configured.events?.["m.room.message"] ?? 10,
      "m.room.encrypted": configured.events?.["m.room.encrypted"] ?? 10,
      "m.reaction": configured.events?.["m.reaction"] ?? 10,
      "m.room.redaction": configured.events?.["m.room.redaction"] ?? configured.redact ?? 50,
      "m.room.power_levels": Math.max(servicePower, configured.events?.["m.room.power_levels"] ?? 100),
      "m.room.member": Math.max(servicePower, configured.events?.["m.room.member"] ?? 100),
      "m.room.join_rules": Math.max(servicePower, configured.events?.["m.room.join_rules"] ?? 100),
      "m.room.history_visibility": Math.max(servicePower, configured.events?.["m.room.history_visibility"] ?? 100),
      "m.room.encryption": Math.max(servicePower, configured.events?.["m.room.encryption"] ?? 100),
      "m.room.name": Math.max(servicePower, configured.events?.["m.room.name"] ?? 100),
      "m.room.topic": Math.max(servicePower, configured.events?.["m.room.topic"] ?? 100),
    },
  };
};

function initialState(room) {
  // Creator power is room-version-specific: with explicit power levels, a v11
  // creator omitted from `users` falls back to users_default; v12 derives
  // immutable infinite creator power. Keep the creator out of `users`.
  const powerLevels = basePowerLevels();
  return [
    { type: "m.room.encryption", state_key: "", content: { algorithm: "m.megolm.v1.aes-sha2" } },
    { type: "m.room.join_rules", state_key: "", content: { join_rule: "invite" } },
    { type: "m.room.history_visibility", state_key: "", content: { history_visibility: "joined" } },
    { type: "m.room.power_levels", state_key: "", content: powerLevels },
  ];
}

async function roomIdFor(token, room) {
  const alias = `#${room.alias_localpart}:${matrixServerName}`;
  const encodedAlias = encodeURIComponent(alias);
  const lookup = await request(synapse, `/_matrix/client/v3/directory/room/${encodedAlias}`, { token, expected: [404] });
  if (lookup.status === 200 && lookup.data?.room_id) return lookup.data.room_id;

  const created = await request(synapse, "/_matrix/client/v3/createRoom", {
    method: "POST",
    token,
    body: {
      name: room.name,
      room_alias_name: room.alias_localpart,
      visibility: "private",
      preset: "private_chat",
      creation_content: { "m.federate": false },
      initial_state: initialState(room),
    },
    expected: [400],
  });
  if (created.status === 200 && created.data?.room_id) {
    console.log(`Created encrypted room ${room.id}.`);
    return created.data.room_id;
  }
  // A concurrent service instance may have created the alias first. Re-read it;
  // never accept a room without the encryption state below.
  const retry = await request(synapse, `/_matrix/client/v3/directory/room/${encodedAlias}`, { token });
  return retry.data.room_id;
}

async function ensureRoomEncryption(token, roomId) {
  const path = `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state/m.room.encryption/`;
  const current = await request(synapse, path, { token, expected: [404] });
  if (current.status === 404) {
    throw new Error(`Room ${roomId} has no E2EE state; refusing to retrofit encryption onto existing history`);
  }
  if (current.data?.algorithm !== "m.megolm.v1.aes-sha2") {
    throw new Error(`Room ${roomId} exists with an unsupported encryption algorithm; refusing to weaken or overwrite it`);
  }
}

async function ensureState(token, roomId, eventType, stateKey, content) {
  const suffix = stateKey ? `${encodeURIComponent(stateKey)}` : "";
  const path = `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state/${encodeURIComponent(eventType)}/${suffix}`;
  const current = await request(synapse, path, { token, expected: [404] });
  if (current.status === 200 && JSON.stringify(sortObject(current.data)) === JSON.stringify(sortObject(content))) return;
  await request(synapse, path, { method: "PUT", token, body: content });
}

async function activeMemberships(token, roomId) {
  const response = await request(
    synapse,
    `/_synapse/admin/v1/rooms/${encodeURIComponent(roomId)}/state?type=m.room.member`,
    { token },
  );
  const memberships = new Map();
  for (const event of response.data?.state || []) {
    const membership = event.content?.membership;
    if (event.type === "m.room.member" && membership && event.state_key) {
      memberships.set(event.state_key, membership);
    }
  }
  return memberships;
}

async function kickMember(token, roomId, userId) {
  await request(synapse, `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/kick`, {
    method: "POST",
    token,
    body: { user_id: userId, reason: "Room access is no longer granted by Keycloak policy." },
    expected: [404],
  });
}

async function joinUser(token, roomId, userId) {
  await request(synapse, `/_synapse/admin/v1/join/${encodeURIComponent(roomId)}`, {
    method: "POST",
    token,
    body: { user_id: userId },
  });
}

async function verifyServiceUsersJoined(token, roomId, room) {
  const memberships = await activeMemberships(token, roomId);
  for (const userId of matrixServiceUserIds) {
    if (memberships.get(userId) !== "join") {
      throw new Error(`Configured service user ${userId} is not joined to ${room.id}; withholding demo joins and room map`);
    }
  }
}

async function waitForOrgBotRoomReadiness(roomId, room) {
  const deadline = Date.now() + orgBotReadinessTimeoutMs;
  // aiohttp preserves an escaped colon in the route parameter, which makes a
  // valid Matrix room ID fail OrgBot's strict MXID-style room-ID check.
  const readinessPath = `/v1/rooms/${roomId}/readiness`;
  let lastFailure = "no response";
  while (Date.now() < deadline) {
    const remainingMs = deadline - Date.now();
    try {
      const result = await request(orgBotReadinessBase, readinessPath, {
        headers: { authorization: `Bearer ${orgBotReadinessToken}` },
        expected: [500, 502, 503, 504],
        timeoutMs: Math.max(1, Math.min(15000, remainingMs)),
      });
      if (result.status === 200) {
        const attestation = result.data;
        if (attestation?.status === "ready" && attestation.room_id === roomId &&
            attestation.encrypted === true && attestation.algorithm === "m.megolm.v1.aes-sha2" &&
            matrixServiceUserIds.has(attestation.bot_user_id)) {
          console.log(`OrgBot readiness confirmed for ${room.id}.`);
          return;
        }
        throw new Error("OrgBot readiness response did not attest the configured joined E2EE room");
      }
      lastFailure = `HTTP ${result.status}: ${result.data?.reason || result.data?.status || "not ready"}`;
    } catch (error) {
      if (error instanceof HttpError && error.status >= 400 && error.status < 500) {
        throw new Error(`OrgBot readiness endpoint rejected ${room.id} with HTTP ${error.status}`);
      }
      lastFailure = error instanceof HttpError ? `HTTP ${error.status}` : error.message;
    }

    const sleepMs = Math.min(orgBotReadinessPollMs, deadline - Date.now());
    if (sleepMs <= 0) break;
    await sleep(sleepMs);
  }
  throw new Error(`OrgBot readiness timed out for ${room.id}; demo joins withheld (${lastFailure})`);
}

async function closeRoomDemoAccess(token, roomId, room, adminUserId) {
  const denyPower = { [adminUserId]: servicePower };
  for (const userId of matrixServiceUserIds) denyPower[userId] = 0;
  await writePowerLevels(token, roomId, denyPower);
  const memberships = await activeMemberships(token, roomId);
  for (const [userId, membership] of memberships) {
    if (userId !== adminUserId && !matrixServiceUserIds.has(userId) && ["join", "invite"].includes(membership)) {
      await kickMember(token, roomId, userId);
      console.log(`Closed demo access in ${room.id} while OrgBot readiness is unavailable.`);
    }
  }
}

async function writePowerLevels(token, roomId, desiredPower) {
  const users = { ...desiredPower };
  // Keep the creator out of content.users: in v11 an omitted creator uses
  // users_default; in v12 creator power is immutable and infinite.
  delete users[mxid(adminLocalpart)];
  const content = { ...basePowerLevels(), users };
  const path = `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state/m.room.power_levels/`;
  const current = await request(synapse, path, { token, expected: [404] });
  if (current.status === 200 && JSON.stringify(sortObject(current.data)) === JSON.stringify(sortObject(content))) return;
  await request(synapse, path, { method: "PUT", token, body: content });
}

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortObject(value[key])]));
  }
  return value;
}

async function clearRoomMap() {
  await fs.rm(roomMapOutput, { force: true });
}

async function clearChatPolicy() {
  await fs.rm(chatPolicyOutput, { force: true });
}

async function clearJoinPolicy() {
  await fs.rm(joinPolicyOutput, { force: true });
}

async function writeRoomMap(rooms, managedUserCount, status = "ready") {
  if (status !== "provisioning" && status !== "ready") {
    throw new Error("Room map status must be provisioning or ready");
  }
  const directory = path.dirname(roomMapOutput);
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const tempPath = path.join(directory, `.room-map.${process.pid}.${crypto.randomUUID()}.tmp`);
  const contents = `${JSON.stringify({
    schema_version: 1,
    status,
    last_reconciled_at: new Date().toISOString(),
    matrix_server_name: matrixServerName,
    managed_user_count: managedUserCount,
    rooms,
  }, null, 2)}\n`;
  let handle;
  try {
    handle = await fs.open(tempPath, "wx", 0o600);
    await handle.writeFile(contents, "utf8");
    await handle.sync();
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  } finally {
    await handle?.close();
  }
  try {
    await fs.chmod(tempPath, 0o600);
    await fs.rename(tempPath, roomMapOutput);
    await fs.chmod(roomMapOutput, 0o600);
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  }
}

async function roomMapHasReadyEntry(roomId) {
  try {
    const current = JSON.parse(await fs.readFile(roomMapOutput, "utf8"));
    return current?.schema_version === 1 && current.status === "ready" &&
      current.matrix_server_name === matrixServerName && Array.isArray(current.rooms) &&
      current.rooms.some((room) => room?.room_id === roomId);
  } catch {
    return false;
  }
}

async function writeChatPolicy(snapshot) {
  const directory = path.dirname(chatPolicyOutput);
  await fs.mkdir(directory, { recursive: true, mode: 0o750 });
  // Synapse reads this snapshot through its primary policy-data group.
  // Only the containing directory changes; room-map.json remains mode 0600.
  await fs.chmod(directory, 0o750);
  const tempPath = path.join(directory, `.chat-policy.${process.pid}.${crypto.randomUUID()}.tmp`);
  const contents = `${JSON.stringify(snapshot, null, 2)}\n`;
  let handle;
  try {
    handle = await fs.open(tempPath, "wx", 0o640);
    await handle.writeFile(contents, "utf8");
    await handle.sync();
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  } finally {
    await handle?.close();
  }
  try {
    await fs.chmod(tempPath, 0o640);
    await fs.rename(tempPath, chatPolicyOutput);
    await fs.chmod(chatPolicyOutput, 0o640);
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  }
}

async function writeJoinPolicy(snapshot) {
  const directory = path.dirname(joinPolicyOutput);
  await fs.mkdir(directory, { recursive: true, mode: 0o750 });
  await fs.chmod(directory, 0o750);
  const tempPath = path.join(directory, `.join-policy.${process.pid}.${crypto.randomUUID()}.tmp`);
  const contents = `${JSON.stringify(snapshot, null, 2)}\n`;
  let handle;
  try {
    handle = await fs.open(tempPath, "wx", 0o640);
    await handle.writeFile(contents, "utf8");
    await handle.sync();
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  } finally {
    await handle?.close();
  }
  try {
    await fs.chmod(tempPath, 0o640);
    await fs.rename(tempPath, joinPolicyOutput);
    await fs.chmod(joinPolicyOutput, 0o640);
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  }
}

async function reconcileRoom(token, room, users, preparedRooms, generatedAt) {
  const roomId = await roomIdFor(token, room);
  const adminUserId = mxid(adminLocalpart);
  const roomInfo = await request(synapse, `/_synapse/admin/v1/rooms/${encodeURIComponent(roomId)}`, { token });
  if (roomInfo.data?.creator !== adminUserId) {
    throw new Error(`Configured room ${room.id} was not created by the policy service; refusing to adopt it`);
  }
  const initialMemberships = await activeMemberships(token, roomId);
  if (initialMemberships.get(adminUserId) !== "join") {
    throw new Error(`Policy service is not a member of configured room ${room.id}; refusing to manage a room it does not control`);
  }

  const planOptions = {
    room,
    users,
    adminUserId,
    serviceUserIds: matrixServiceUserIds,
    servicePower,
    rankByClearance,
    allowedRoles,
    powerByRole,
  };
  const initialPlan = buildRoomPlan({ ...planOptions, currentMemberships: initialMemberships });
  // First remove any stale elevated powers so old room state cannot grant a
  // former moderator control while membership and room rules are reconciled.
  await writePowerLevels(token, roomId, initialPlan.desiredPower);
  await ensureState(token, roomId, "m.room.join_rules", "", { join_rule: "invite" });
  await ensureRoomEncryption(token, roomId);
  await ensureState(token, roomId, "m.room.name", "", { name: room.name });
  await ensureState(token, roomId, "m.room.history_visibility", "", { history_visibility: "joined" });
  const roomMapEntry = makeManagedRoomMapEntry({ room, roomId, matrixServerName, rankByClearance });
  await writeJoinPolicy(makeJoinPolicySnapshot({
    generatedAt,
    ttlSeconds: chatPolicyTtlSeconds,
    matrixServerName,
    groupPrefix: directChatGroupPrefix,
    roomMap: [...preparedRooms, roomMapEntry],
    users,
    serviceUserIds: matrixServiceUserIds,
    adminUserIds: [adminUserId],
    rankByClearance,
    allowedRoles,
    powerByRole,
  }));
  const currentMemberships = await activeMemberships(token, roomId);
  const plan = buildRoomPlan({ ...planOptions, currentMemberships });

  for (const userId of plan.revokeUserIds) {
    await kickMember(token, roomId, userId);
    console.log(`Removed a non-authorized member from ${room.id}.`);
  }
  // Service accounts join and attest E2EE health before any eligible demo
  // account is joined through the Synapse Admin API.
  if (matrixServiceUserIds.size > 0) {
    try {
      for (const action of plan.joinSequence.filter((entry) => entry.kind === "service")) {
        await joinUser(token, roomId, action.userId);
      }
      await verifyServiceUsersJoined(token, roomId, room);
      await publishRoomMapBeforeBotReadiness({
        roomMapEntry,
        preparedRooms,
        managedUserCount: users.length,
        isRoomInReadyMap: roomMapHasReadyEntry,
        writeRoomMap,
        waitForReadiness: () => waitForOrgBotRoomReadiness(roomId, room),
      });
    } catch (error) {
      try {
        await closeRoomDemoAccess(token, roomId, room, adminUserId);
      } catch (closeError) {
        throw new Error(`${error.message}; could not close demo access: ${closeError.message}`);
      }
      throw error;
    }
  }
  for (const action of plan.joinSequence.filter((entry) => entry.kind === "demo")) {
    await joinUser(token, roomId, action.userId);
  }
  await verifyServiceUsersJoined(token, roomId, room);
  // Reapply after membership changes in case a Matrix implementation or
  // concurrent room update changed power state during the reconciliation.
  await writePowerLevels(token, roomId, plan.desiredPower);
  return roomMapEntry;
}

async function reconcile() {
  const kcToken = await getKeycloakToken();
  const matrixToken = await loginSynapseAdmin();
  const generatedAt = Date.now();
  const [kcUsers, clientUuid] = await Promise.all([
    getKeycloakUsers(kcToken),
    getClientUuid(kcToken),
  ]);
  const scopedUsers = kcUsers.filter((user) =>
    isManagedDemoUser(user, clearanceAttribute, rankByClearance) &&
    !isReservedMatrixIdentity(mxid(user.username), mxid(adminLocalpart), matrixServiceUserIds));
  const users = [];
  for (const kcUser of scopedUsers) {
    const roles = await keycloakRoles(kcToken, clientUuid, kcUser.id);
    const realmRoles = await request(keycloakBase,
      `/admin/realms/${encodeURIComponent(realm)}/users/${encodeURIComponent(kcUser.id)}/role-mappings/realm/composite`,
      { token: kcToken });
    const user = normalizeUser(kcUser, roles, realmRoles.data);
    if (user.enabled && !matrixServiceUserIds.has(user.userId) && user.userId !== mxid(adminLocalpart)) {
      user.directGroups = await getKeycloakGroups(kcToken, kcUser.id);
    } else {
      user.directGroups = [];
    }
    await ensureSynapseUser(matrixToken, user);
    await reconcileMatrixEntitlement({ user, request, provisionerBase, provisionerToken });
    users.push(user);
  }

  const roomMap = [];
  for (const room of policy.rooms) {
    roomMap.push(await reconcileRoom(matrixToken, room, users, roomMap, generatedAt));
  }
  await writeRoomMap(roomMap, users.length);
  const chatPolicySnapshot = makeChatPolicySnapshot({
    generatedAt,
    ttlSeconds: chatPolicyTtlSeconds,
    matrixServerName,
    groupPrefix: directChatGroupPrefix,
    roomMap,
    users,
    serviceUserIds: matrixServiceUserIds,
    adminUserIds: [mxid(adminLocalpart)],
    rankByClearance,
    allowedRoles,
    powerByRole,
  });
  await writeChatPolicy(chatPolicySnapshot);
  await clearJoinPolicy();
  console.log(`Policy reconciliation complete: ${users.length} demo identities, ${policy.rooms.length} rooms.`);
}

console.log("Matrix room policy synchronizer started; Keycloak is the source of user clearance and room roles.");
while (true) {
  try {
    await reconcile();
  } catch (error) {
    if (error instanceof HttpError && error.status === 401) {
      if (error.url.startsWith(keycloakBase)) {
        keycloakTokenCache = null;
        keycloakTokenExpiresAt = 0;
      }
    }
    console.error(`Policy reconciliation failed; room-map readiness withheld: ${error.message}`);
    const clearResults = await Promise.allSettled([clearRoomMap(), clearChatPolicy(), clearJoinPolicy()]);
    for (const result of clearResults) {
      if (result.status === "rejected") {
        console.error(`Could not clear a stale policy readiness file: ${result.reason.message}`);
      }
    }
  }
  await sleep(syncInterval);
}
