import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  ensureOrgBotSession,
  readPrivateSessionFile,
  verifyNonAdminSession,
} from "./service-session.mjs";

const USERNAME = "orgbot";
const USER_ID = "@orgbot:example.test";
const DEVICE_ID = "FOLDFENCEORGBOT";
const TOKEN = "mas_a_synthetic_valid_access_token_0123456789";
const NEW_TOKEN = "mas_a_synthetic_new_access_token_0123456789";

function validDocument(accessToken = TOKEN) {
  return {
    schema_version: 1,
    user_id: USER_ID,
    device_id: DEVICE_ID,
    access_token: accessToken,
    admin: false,
  };
}

function reply(status, body) {
  return { status, async json() { return body; } };
}

async function tempSessionPath() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "orgbot-session-test-"));
  return { directory, filePath: path.join(directory, "orgbot-session.json") };
}

async function writeSession(filePath, document, mode = 0o600) {
  await fs.writeFile(filePath, `${JSON.stringify(document)}\n`, { mode });
  await fs.chmod(filePath, mode);
}

function goodFetches(accessToken = TOKEN) {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("/_matrix/client/v3/account/whoami")) {
      return reply(200, { user_id: USER_ID, device_id: DEVICE_ID, is_guest: false });
    }
    return reply(403, { errcode: "M_FORBIDDEN", error: "Forbidden" });
  };
  return { requests, fetchImpl };
}

test("validates whoami identity then confirms non-admin access with Synapse 403", async () => {
  const { requests, fetchImpl } = goodFetches();
  assert.deepEqual(await verifyNonAdminSession({
    userId: USER_ID,
    deviceId: DEVICE_ID,
    accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008/",
    fetchImpl,
  }), { status: "valid" });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, "http://synapse:8008/_matrix/client/v3/account/whoami");
  assert.equal(requests[1].url, "http://synapse:8008/_synapse/admin/v1/users/%40orgbot%3Aexample.test/joined_rooms");
  for (const request of requests) {
    assert.equal(request.options.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(request.options.redirect, "manual");
  }
});

test("requires the exact Matrix user and stable device", async () => {
  for (const whoami of [
    { user_id: "@other:example.test", device_id: DEVICE_ID },
    { user_id: USER_ID, device_id: "RANDOMDEVICE" },
  ]) {
    await assert.rejects(verifyNonAdminSession({
      userId: USER_ID,
      deviceId: DEVICE_ID,
      accessToken: TOKEN,
      homeserverUrl: "http://synapse:8008",
      fetchImpl: async () => reply(200, whoami),
    }), /user\/device identity/);
  }
});

test("a valid token whose whoami reports unknown-token is eligible for renewal", async () => {
  assert.deepEqual(await verifyNonAdminSession({
    userId: USER_ID,
    deviceId: DEVICE_ID,
    accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008",
    fetchImpl: async () => reply(401, { errcode: "M_UNKNOWN_TOKEN" }),
  }), { status: "expired" });
});

test("fails closed for non-expiry whoami failures and an admin-capable session", async () => {
  await assert.rejects(verifyNonAdminSession({
    userId: USER_ID,
    deviceId: DEVICE_ID,
    accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008",
    fetchImpl: async () => reply(403, { errcode: "M_USER_LOCKED" }),
  }), /user\/device identity/);

  let requestCount = 0;
  await assert.rejects(verifyNonAdminSession({
    userId: USER_ID,
    deviceId: DEVICE_ID,
    accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008",
    fetchImpl: async (...args) => {
      requestCount++;
      if (requestCount === 1) return reply(200, { user_id: USER_ID, device_id: DEVICE_ID });
      return reply(200, { joined_rooms: [] });
    },
  }), /non-admin/);
});

test("issues and stores only a verified non-admin session atomically at mode 0600", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    const { requests, fetchImpl } = goodFetches();
    const calls = [];
    const result = await ensureOrgBotSession({
      username: USERNAME,
      userId: USER_ID,
      deviceId: DEVICE_ID,
      sessionPath: filePath,
      homeserverUrl: "http://synapse:8008",
      fetchImpl,
      runMas: async (args) => {
        calls.push(args);
        return `Compatibility token issued: ${TOKEN}`;
      },
    });
    assert.deepEqual(result, { status: "issued" });
    assert.deepEqual(calls, [["manage", "issue-compatibility-token", USERNAME, DEVICE_ID]]);
    assert.equal(requests.length, 2);
    const stat = await fs.stat(filePath);
    assert.equal(stat.mode & 0o777, 0o600);
    assert.equal(stat.uid, process.getuid());
    assert.deepEqual(JSON.parse(await fs.readFile(filePath, "utf8")), validDocument());
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("does not write a newly issued token if the server reports that it is admin", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    let requestCount = 0;
    await assert.rejects(ensureOrgBotSession({
      username: USERNAME,
      userId: USER_ID,
      deviceId: DEVICE_ID,
      sessionPath: filePath,
      homeserverUrl: "http://synapse:8008",
      fetchImpl: async () => {
        requestCount++;
        return requestCount === 1
          ? reply(200, { user_id: USER_ID, device_id: DEVICE_ID })
          : reply(200, { joined_rooms: [] });
      },
      runMas: async () => `Compatibility token issued: ${TOKEN}`,
    }), /non-admin/);
    await assert.rejects(fs.lstat(filePath), { code: "ENOENT" });
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("secures an older owned volume and reuses its valid session without minting a token", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    await writeSession(filePath, validDocument());
    await fs.chmod(directory, 0o755);
    const { fetchImpl } = goodFetches();
    let issueCount = 0;
    const result = await ensureOrgBotSession({
      username: USERNAME,
      userId: USER_ID,
      deviceId: DEVICE_ID,
      sessionPath: filePath,
      homeserverUrl: "http://synapse:8008",
      fetchImpl,
      runMas: async () => { issueCount++; return ""; },
    });
    assert.deepEqual(result, { status: "reused" });
    assert.equal((await fs.stat(directory)).mode & 0o777, 0o700);
    assert.equal(issueCount, 0);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("renews an expired token for the same exact user and device", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    await writeSession(filePath, validDocument());
    let queryCount = 0;
    const fetchImpl = async (url) => {
      queryCount++;
      if (queryCount === 1) return reply(401, { errcode: "M_UNKNOWN_TOKEN" });
      if (url.endsWith("/_matrix/client/v3/account/whoami")) {
        return reply(200, { user_id: USER_ID, device_id: DEVICE_ID });
      }
      return reply(403, { errcode: "M_FORBIDDEN" });
    };
    const result = await ensureOrgBotSession({
      username: USERNAME,
      userId: USER_ID,
      deviceId: DEVICE_ID,
      sessionPath: filePath,
      homeserverUrl: "http://synapse:8008",
      fetchImpl,
      runMas: async (args) => {
        assert.deepEqual(args, ["manage", "issue-compatibility-token", USERNAME, DEVICE_ID]);
        return `Compatibility token issued: ${NEW_TOKEN}`;
      },
    });
    assert.deepEqual(result, { status: "renewed" });
    assert.deepEqual(JSON.parse(await fs.readFile(filePath, "utf8")), validDocument(NEW_TOKEN));
    const stat = await fs.stat(filePath);
    assert.equal(stat.mode & 0o777, 0o600);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("never overwrites a symlink, malformed, broadly readable, or identity-colliding file", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    const target = path.join(directory, "target.json");
    await writeSession(target, validDocument());
    await fs.symlink(target, filePath);
    await assert.rejects(readPrivateSessionFile(filePath, { userId: USER_ID, deviceId: DEVICE_ID }), /unsafe/);
    await fs.unlink(filePath);

    await writeSession(filePath, { ...validDocument(), admin: true });
    await assert.rejects(readPrivateSessionFile(filePath, { userId: USER_ID, deviceId: DEVICE_ID }), /invalid identity/);
    await writeSession(filePath, validDocument(), 0o644);
    await assert.rejects(readPrivateSessionFile(filePath, { userId: USER_ID, deviceId: DEVICE_ID }), /unsafe/);

    await fs.unlink(filePath);
    let injected = false;
    const fetchImpl = async (url) => {
      if (url.endsWith("/_matrix/client/v3/account/whoami")) return reply(200, { user_id: USER_ID, device_id: DEVICE_ID });
      if (!injected) {
        await fs.symlink(target, filePath);
        injected = true;
      }
      return reply(403, { errcode: "M_FORBIDDEN" });
    };
    await assert.rejects(ensureOrgBotSession({
      username: USERNAME,
      userId: USER_ID,
      deviceId: DEVICE_ID,
      sessionPath: filePath,
      homeserverUrl: "http://synapse:8008",
      fetchImpl,
      runMas: async () => `Compatibility token issued: ${TOKEN}`,
    }), /could not be written safely/);
    assert.equal((await fs.lstat(filePath)).isSymbolicLink(), true);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("rejects a session directory that is group or world accessible", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    await fs.chmod(directory, 0o755);
    await assert.rejects(readPrivateSessionFile(filePath, { userId: USER_ID, deviceId: DEVICE_ID }), /directory.*unsafe/);
  } finally {
    await fs.chmod(directory, 0o700);
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("requires the session volume and file to be owned by the provisioner uid", async () => {
  const { directory, filePath } = await tempSessionPath();
  try {
    await writeSession(filePath, validDocument());
    await assert.rejects(readPrivateSessionFile(filePath, {
      userId: USER_ID,
      deviceId: DEVICE_ID,
      uid: process.getuid() + 1,
    }), /directory.*unsafe/);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("Compose gives OrgBot only the dedicated read-only session volume", async () => {
  const compose = await fs.readFile(new URL("../../deployment/compose/compose.yaml", import.meta.url), "utf8");
  const service = (name) => {
    const match = compose.match(new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [A-Za-z0-9_-]+:|^volumes:)`, "m"));
    assert.ok(match, `Compose service ${name} exists`);
    return match[1];
  };
  const provisioner = service("provisioner");
  const orgbot = service("org-bot");
  assert.match(provisioner, /mas-bootstrap-data:\/run\/mas-bootstrap\n/);
  assert.match(provisioner, /orgbot-session-data:\/run\/orgbot-session\n/);
  assert.match(orgbot, /orgbot-session-data:\/run\/mas-bootstrap:ro/);
  assert.doesNotMatch(orgbot, /mas-bootstrap-data/);
  assert.match(orgbot, /ORG_BOT_SESSION_FILE: \/run\/mas-bootstrap\/orgbot-session\.json/);
  assert.doesNotMatch(`${provisioner}\n${orgbot}`, /ORG_BOT_PASSWORD|ORG_BOT_AUTH_URL/);
  assert.match(compose, /^  orgbot-session-data:\s*$/m);
});
