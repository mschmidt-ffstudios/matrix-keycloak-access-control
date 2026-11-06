import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { reconcileMatrixAccess, validateMatrixAccessRequest } from "./matrix-access.mjs";
import { ensureSynapseAdminToken } from "./admin-session.mjs";
import { ensureOrgBotSession } from "./service-session.mjs";

const execFileAsync = promisify(execFile);
const masCli = process.env.MAS_CLI || "/usr/local/bin/mas-cli";
const statePath = process.env.MAS_BOOTSTRAP_STATE_PATH || "/data/state.json";
const tokenPath = process.env.MAS_ADMIN_TOKEN_PATH || "/data/synapse-admin-token";
const tokenVerificationPath = process.env.MAS_ADMIN_TOKEN_VERIFIED_PATH || `${tokenPath}.verified`;
const synapseAdmin = process.env.MAS_ADMIN_USERNAME || "demo-policy-admin";
const adminDevice = process.env.MAS_ADMIN_TOKEN_DEVICE_ID || "DEMOPOLICYSVC";
const botUsername = process.env.ORG_BOT_USERNAME || "orgbot";
const botDevice = process.env.ORG_BOT_DEVICE_ID || "FOLDFENCEORGBOT";
const botSessionPath = process.env.ORG_BOT_SESSION_PATH || "/run/orgbot-session/orgbot-session.json";
const matrixServerName = required("MATRIX_SERVER_NAME");
const matrixHomeserverUrl = (process.env.MATRIX_HOMESERVER_URL || "http://synapse:8008").replace(/\/$/, "");
const botDisplayName = process.env.ORG_BOT_DISPLAY_NAME || "Demo Export Bot";
const providerId = required("MAS_UPSTREAM_PROVIDER_ID");
const apiToken = required("MAS_PROVISIONER_API_TOKEN");
const usersPath = process.env.DEMO_USERS_PATH || "/app/config/demo-users.json";
const keycloakBase = (process.env.KEYCLOAK_PUBLIC_URL || "http://keycloak:8080/auth").replace(/\/$/, "");
const keycloakRealm = process.env.KEYCLOAK_REALM || "foldfence-demo";
const keycloakClientId = process.env.KEYCLOAK_CLIENT_ID || "matrix-policy-sync";
const keycloakClientSecret = required("KEYCLOAK_POLICY_CLIENT_SECRET");
const httpPort = Number(process.env.MAS_BOOTSTRAP_HTTP_PORT || 8091);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const reservedMatrixUsers = new Set([synapseAdmin, botUsername, "demo-admin-01", "demo-admin-02"]);
let stateMutation = Promise.resolve();
function withStateLock(action) {
  const pending = stateMutation.then(action);
  stateMutation = pending.catch(() => {});
  return pending;
}

if (!/^[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(providerId)) {
  throw new Error("MAS_UPSTREAM_PROVIDER_ID must be a valid ULID");
}
if (!/^[a-z0-9][a-z0-9._=-]*$/i.test(botUsername) || !/^[a-z0-9][a-z0-9._=-]*$/i.test(synapseAdmin)) {
  throw new Error("Configured Matrix service usernames are invalid");
}
if (!/^[A-Za-z0-9._=-]{1,255}$/.test(botDevice) || !matrixServerName || /[\s/]/.test(matrixServerName)) {
  throw new Error("Configured OrgBot Matrix device or server name is invalid");
}

function required(name) {
  const value = process.env[name];
  if (!value || value === "CHANGE_ME") throw new Error(`${name} is missing or still a placeholder`);
  return value;
}

function safeLog(message) {
  // Never forward child output: MAS CLI includes compatibility tokens in its info log.
  console.log(message);
}

async function runMas(args) {
  try {
    const { stdout, stderr } = await execFileAsync(masCli, args, {
      env: { ...process.env, RUST_LOG: "info" },
      maxBuffer: 2 * 1024 * 1024,
      timeout: 60000,
    });
    return `${stdout}\n${stderr}`;
  } catch {
    throw new Error(`Matrix Authentication Service command failed: ${args[0]} ${args[1] || ""}`.trim());
  }
}

async function loadState() {
  try {
    const parsed = JSON.parse(await fs.readFile(statePath, "utf8"));
    if (parsed && typeof parsed.users === "object" && parsed.users !== null) return parsed;
  } catch (error) {
    if (error.code !== "ENOENT") throw new Error("Provisioner state file is unreadable or invalid");
  }
  return { users: {} };
}

async function saveState(state) {
  const temp = `${statePath}.tmp`;
  await fs.writeFile(temp, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 });
  await fs.rename(temp, statePath);
  await fs.chmod(statePath, 0o600);
}

async function registerAccount({ username, subject, displayName, admin = false, serviceBot = false }) {
  const state = await loadState();
  const record = state.users[username];
  if (record) {
    if (record.subject !== (subject || null) || record.admin !== admin || record.bot !== serviceBot) {
      throw new Error(`Provisioned identity ${username} changed unexpectedly; manual review is required`);
    }
    return false;
  }

  const args = ["manage", "register-user", "--yes", "--display-name", displayName];
  if (subject) args.push("--upstream-provider-mapping", `${providerId}:${subject}`);
  args.push(admin ? "--admin" : "--no-admin", username);
  await runMas(args);
  state.users[username] = { subject: subject || null, admin, bot: serviceBot };
  await saveState(state);
  return true;
}

async function keycloakToken() {
  const url = `${keycloakBase}/realms/${encodeURIComponent(keycloakRealm)}/protocol/openid-connect/token`;
  const body = new URLSearchParams({
    grant_type: "client_credentials",
    client_id: keycloakClientId,
    client_secret: keycloakClientSecret,
  });
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body,
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      await sleep(2000);
      continue;
    }
    if (response.ok) {
      const result = await response.json();
      if (!result.access_token) throw new Error("Keycloak did not issue a service-account token");
      return result.access_token;
    }
    if (response.status === 404 || response.status === 429 || response.status >= 500) {
      await sleep(2000);
      continue;
    }
    throw new Error("Keycloak service-account authentication failed");
  }
  throw new Error("Keycloak did not become ready for service-account authentication");
}

async function keycloakUserByUsername(token, username) {
  const url = `${keycloakBase}/admin/realms/${encodeURIComponent(keycloakRealm)}/users?username=${encodeURIComponent(username)}&exact=true`;
  const response = await fetch(url, {
    headers: { authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("Keycloak user lookup failed");
  const matches = await response.json();
  const user = matches.find((item) => item.username === username);
  if (!user?.id || !user.enabled) throw new Error(`Active Keycloak user ${username} was not found`);
  return user;
}

async function ensureSeedAccounts() {
  const seed = JSON.parse(await fs.readFile(usersPath, "utf8"));
  if (!Array.isArray(seed.users) || seed.users.length < 1) throw new Error("Synthetic demo user seed is empty");
  const token = await keycloakToken();
  for (const seeded of seed.users) {
    if (!/^[a-z0-9][a-z0-9._=-]*$/i.test(seeded.username)) throw new Error("Seed contains an invalid Matrix username");
    const keycloakUser = await keycloakUserByUsername(token, seeded.username);
    await registerAccount({
      username: seeded.username,
      subject: keycloakUser.id,
      displayName: seeded.display_name || seeded.username,
    });
  }
  await registerAccount({ username: synapseAdmin, displayName: "Demo Policy Service", admin: true });
  await registerAccount({ username: botUsername, displayName: botDisplayName, serviceBot: true });
  safeLog(`Matrix Authentication Service identities provisioned: ${seed.users.length} seeded users and two service accounts.`);
}

async function ensureOrgBotSessionFile() {
  const result = await ensureOrgBotSession({
    username: botUsername,
    userId: `@${botUsername}:${matrixServerName}`,
    deviceId: botDevice,
    sessionPath: botSessionPath,
    homeserverUrl: matrixHomeserverUrl,
    runMas,
  });
  safeLog(`OrgBot non-admin Matrix session ${result.status}; stable device verified.`);
}

async function ensureAdminToken() {
  const result = await ensureSynapseAdminToken({
    username: synapseAdmin,
    userId: `@${synapseAdmin}:${matrixServerName}`,
    deviceId: adminDevice,
    tokenPath,
    verificationPath: tokenVerificationPath,
    homeserverUrl: matrixHomeserverUrl,
    runMas,
  });
  safeLog(`Synapse administrator session ${result.status}; identity, device, and admin scope verified.`);
}

function constantTimeEqual(left, right) {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function respond(res, status, body) {
  const encoded = Buffer.from(`${JSON.stringify(body)}\n`);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": encoded.length,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(encoded);
}

async function handleProvision(req, res) {
  if (req.method !== "POST" || !["/v1/users", "/v1/users/access"].includes(req.url)) {
    respond(res, 404, { error: "not_found" });
    return;
  }
  const authorization = req.headers.authorization || "";
  if (!authorization.startsWith("Bearer ") || !constantTimeEqual(authorization.slice(7), apiToken)) {
    respond(res, 401, { error: "unauthorized" });
    return;
  }

  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 16384) {
      respond(res, 413, { error: "request_too_large" });
      return;
    }
  }
  let user;
  try {
    user = JSON.parse(raw);
  } catch {
    respond(res, 400, { error: "invalid_json" });
    return;
  }
  if (req.url === "/v1/users/access") {
    try {
      validateMatrixAccessRequest(user, reservedMatrixUsers);
    } catch {
      respond(res, 400, { error: "invalid_access_request" });
      return;
    }
    try {
      const result = await withStateLock(() => reconcileMatrixAccess({
        input: user, reservedUsernames: reservedMatrixUsers, loadState, saveState, runMas,
      }));
      respond(res, 200, result);
    } catch {
      console.error("Managed Matrix access reconciliation failed");
      respond(res, 409, { error: "access_reconciliation_failed" });
    }
    return;
  }
  if (!user || !/^[a-z0-9][a-z0-9._=-]*$/i.test(user.username || "") ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(user.subject || "") ||
      typeof user.displayName !== "string" || user.displayName.length > 128) {
    respond(res, 400, { error: "invalid_user" });
    return;
  }

  try {
    const created = await withStateLock(() => registerAccount({ username: user.username, subject: user.subject, displayName: user.displayName }));
    respond(res, 200, { provisioned: true, created });
  } catch (error) {
    console.error(`Matrix identity provision failed for ${user.username}: ${error.message}`);
    respond(res, 409, { error: "identity_conflict" });
  }
}

await runMas(["database", "migrate"]);
await runMas(["config", "sync"]);
await ensureSeedAccounts();
await ensureOrgBotSessionFile();

const server = http.createServer((req, res) => {
  handleProvision(req, res).catch(() => respond(res, 500, { error: "provisioning_failed" }));
});
server.maxHeadersCount = 32;
server.requestTimeout = 10000;
server.headersTimeout = 5000;
server.listen(httpPort, "0.0.0.0", () => safeLog("Private Matrix identity provisioning endpoint is ready."));

while (true) {
  try {
    await ensureAdminToken();
  } catch (error) {
    console.error(`Synapse administrator session validation failed: ${error.message}`);
  }
  await sleep(180000);
}
