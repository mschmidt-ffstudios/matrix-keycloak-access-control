import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  ensureSynapseAdminToken,
  readAdminTokenFile,
  verifyAdminSession,
} from "./admin-session.mjs";

const USERNAME = "demo-policy-admin";
const USER_ID = "@demo-policy-admin:example.test";
const DEVICE_ID = "DEMOPOLICYSVC";
const TOKEN = "mas_synthetic_admin_compatibility_token_0123456789";
const NEW_TOKEN = "mas_synthetic_renewed_admin_token_0123456789";

function reply(status, body) {
  return { status, async json() { return body; } };
}

async function tempTokenPath() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "synapse-admin-session-test-"));
  return { directory, filePath: path.join(directory, "synapse-admin-token") };
}

async function writeToken(filePath, token = TOKEN, mode = 0o600) {
  await fs.writeFile(filePath, `${token}\n`, { mode });
  await fs.chmod(filePath, mode);
}

function goodFetches() {
  const requests = [];
  return {
    requests,
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith("/_matrix/client/v3/account/whoami")) {
        return reply(200, { user_id: USER_ID, device_id: DEVICE_ID, is_guest: false });
      }
      return reply(200, { name: USER_ID, admin: false, deactivated: false });
    },
  };
}

test("validates exact user/device and a protected Synapse admin API response", async () => {
  const { requests, fetchImpl } = goodFetches();
  assert.deepEqual(await verifyAdminSession({
    userId: USER_ID,
    deviceId: DEVICE_ID,
    accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008/",
    fetchImpl,
  }), { status: "valid" });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, "http://synapse:8008/_matrix/client/v3/account/whoami");
  assert.equal(requests[1].url, "http://synapse:8008/_synapse/admin/v2/users/%40demo-policy-admin%3Aexample.test");
  for (const request of requests) {
    assert.equal(request.options.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(request.options.redirect, "manual");
  }
});

test("rejects mismatched identity/device and requires protected admin endpoint access", async () => {
  for (const whoami of [
    { user_id: "@other:example.test", device_id: DEVICE_ID },
    { user_id: USER_ID, device_id: "RANDOMDEVICE" },
  ]) {
    await assert.rejects(verifyAdminSession({
      userId: USER_ID, deviceId: DEVICE_ID, accessToken: TOKEN,
      homeserverUrl: "http://synapse:8008",
      fetchImpl: async () => reply(200, whoami),
    }), /identity does not match/);
  }
  for (const [status, adminRecord] of [
    [403, { errcode: "M_FORBIDDEN" }],
    [200, { name: "@other:example.test", admin: true }],
  ]) {
    let requestCount = 0;
    await assert.rejects(verifyAdminSession({
      userId: USER_ID, deviceId: DEVICE_ID, accessToken: TOKEN,
      homeserverUrl: "http://synapse:8008",
      fetchImpl: async () => {
        requestCount++;
        return requestCount === 1
          ? reply(200, { user_id: USER_ID, device_id: DEVICE_ID })
          : reply(status, adminRecord);
      },
    }), /did not prove/);
  }
});

test("only an explicit unknown-token response is eligible for renewal", async () => {
  assert.deepEqual(await verifyAdminSession({
    userId: USER_ID, deviceId: DEVICE_ID, accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008",
    fetchImpl: async () => reply(401, { errcode: "M_UNKNOWN_TOKEN" }),
  }), { status: "expired" });

  await assert.rejects(verifyAdminSession({
    userId: USER_ID, deviceId: DEVICE_ID, accessToken: TOKEN,
    homeserverUrl: "http://synapse:8008",
    fetchImpl: async (url) => url.endsWith("/whoami")
      ? reply(403, { errcode: "M_FORBIDDEN" })
      : reply(200, { name: USER_ID, admin: true }),
  }), /identity does not match/);
});

test("issues a token with the explicit admin flag, verifies it, and stores it as 0600", async () => {
  const { directory, filePath } = await tempTokenPath();
  try {
    const { requests, fetchImpl } = goodFetches();
    const calls = [];
    const result = await ensureSynapseAdminToken({
      username: USERNAME, userId: USER_ID, deviceId: DEVICE_ID,
      tokenPath: filePath, homeserverUrl: "http://synapse:8008",
      fetchImpl,
      runMas: async (args) => {
        calls.push(args);
        return `Compatibility token issued: ${TOKEN}`;
      },
    });
    assert.deepEqual(result, { status: "issued" });
    assert.deepEqual(calls, [[
      "manage", "issue-compatibility-token", USERNAME, DEVICE_ID,
      "--yes-i-want-to-grant-synapse-admin-privileges",
    ]]);
    assert.equal(requests.length, 2);
    assert.equal(await fs.readFile(filePath, "utf8"), `${TOKEN}\n`);
    assert.equal(await fs.readFile(`${filePath}.verified`, "utf8"), "verified\n");
    const stat = await fs.stat(filePath);
    const markerStat = await fs.stat(`${filePath}.verified`);
    assert.equal(stat.mode & 0o777, 0o600);
    assert.equal(markerStat.mode & 0o777, 0o600);
    assert.equal(stat.uid, process.getuid());
    assert.equal(markerStat.uid, process.getuid());
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("reuses a valid unexpired CLI token without creating another MAS session", async () => {
  const { directory, filePath } = await tempTokenPath();
  try {
    await writeToken(filePath);
    const { fetchImpl } = goodFetches();
    let issueCount = 0;
    assert.deepEqual(await ensureSynapseAdminToken({
      username: USERNAME, userId: USER_ID, deviceId: DEVICE_ID,
      tokenPath: filePath, homeserverUrl: "http://synapse:8008", fetchImpl,
      runMas: async () => { issueCount++; return ""; },
    }), { status: "reused" });
    assert.equal(issueCount, 0);
    assert.equal(await fs.readFile(filePath, "utf8"), `${TOKEN}\n`);
    assert.equal(await fs.readFile(`${filePath}.verified`, "utf8"), "verified\n");
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("renews the same device only after an explicit unknown-token response", async () => {
  const { directory, filePath } = await tempTokenPath();
  try {
    await writeToken(filePath);
    let requests = 0;
    const fetchImpl = async (url) => {
      requests++;
      if (requests === 1) return reply(401, { errcode: "M_UNKNOWN_TOKEN" });
      if (url.endsWith("/_matrix/client/v3/account/whoami")) {
        return reply(200, { user_id: USER_ID, device_id: DEVICE_ID });
      }
      return reply(200, { name: USER_ID, admin: false });
    };
    const result = await ensureSynapseAdminToken({
      username: USERNAME, userId: USER_ID, deviceId: DEVICE_ID,
      tokenPath: filePath, homeserverUrl: "http://synapse:8008", fetchImpl,
      runMas: async (args) => {
        assert.deepEqual(args, [
          "manage", "issue-compatibility-token", USERNAME, DEVICE_ID,
          "--yes-i-want-to-grant-synapse-admin-privileges",
        ]);
        return `Compatibility token issued: ${NEW_TOKEN}`;
      },
    });
    assert.deepEqual(result, { status: "renewed" });
    assert.equal(await fs.readFile(filePath, "utf8"), `${NEW_TOKEN}\n`);
    assert.equal(await fs.readFile(`${filePath}.verified`, "utf8"), "verified\n");
    assert.equal((await fs.stat(filePath)).mode & 0o777, 0o600);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("never overwrites an unsafe token file or writes an unverified replacement", async () => {
  const { directory, filePath } = await tempTokenPath();
  try {
    const target = path.join(directory, "target-token");
    await writeToken(target);
    await fs.symlink(target, filePath);
    await assert.rejects(readAdminTokenFile(filePath), /unsafe/);
    await fs.unlink(filePath);

    await writeToken(filePath, TOKEN, 0o644);
    await assert.rejects(readAdminTokenFile(filePath), /unsafe/);
    await fs.chmod(filePath, 0o600);

    let requests = 0;
    await assert.rejects(ensureSynapseAdminToken({
      username: USERNAME, userId: USER_ID, deviceId: DEVICE_ID,
      tokenPath: filePath, homeserverUrl: "http://synapse:8008",
      fetchImpl: async (url) => {
        requests++;
        if (url.endsWith("/_matrix/client/v3/account/whoami")) {
          return requests === 1
            ? reply(401, { errcode: "M_UNKNOWN_TOKEN" })
            : reply(200, { user_id: USER_ID, device_id: DEVICE_ID });
        }
        return reply(403, { errcode: "M_FORBIDDEN" });
      },
      runMas: async () => `Compatibility token issued: ${NEW_TOKEN}`,
    }), /did not prove/);
    assert.equal(await fs.readFile(filePath, "utf8"), `${TOKEN}\n`);
    assert.equal(requests, 3);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("hardens the legacy owner-only 0755 volume and rejects wrong-owner or symlink parents", async () => {
  const { directory, filePath } = await tempTokenPath();
  try {
    await writeToken(filePath);
    await fs.chmod(directory, 0o755);
    assert.deepEqual((await readAdminTokenFile(filePath)).token, TOKEN);
    assert.equal((await fs.stat(directory)).mode & 0o777, 0o700);
    await assert.rejects(readAdminTokenFile(filePath, { uid: process.getuid() + 1 }), /directory.*unsafe/);

    const movedDirectory = `${directory}-moved`;
    await fs.rename(directory, movedDirectory);
    await fs.symlink(movedDirectory, directory);
    await assert.rejects(readAdminTokenFile(filePath), /directory.*unsafe/);
    await fs.unlink(directory);
    await fs.rename(movedDirectory, directory);
  } finally {
    const current = await fs.lstat(directory).catch(() => null);
    if (current?.isDirectory() && !current.isSymbolicLink()) await fs.chmod(directory, 0o700);
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("Compose health checks recent admin-token verification, not token age", async () => {
  const compose = await fs.readFile(new URL("../../deployment/compose/compose.yaml", import.meta.url), "utf8");
  const service = compose.match(/^  provisioner:\n([\s\S]*?)(?=^  [A-Za-z0-9_-]+:|^volumes:)/m)?.[1];
  assert.ok(service);
  assert.match(service, /MAS_ADMIN_TOKEN_VERIFIED_PATH: \/run\/mas-bootstrap\/synapse-admin-token\.verified/);
  const healthcheck = service.slice(service.indexOf("    healthcheck:"));
  assert.ok(healthcheck);
  assert.match(healthcheck, /MAS_ADMIN_TOKEN_VERIFIED_PATH/);
  assert.match(healthcheck, /age>300000/);
  assert.match(healthcheck, /age<0/);
  assert.doesNotMatch(healthcheck, /MAS_ADMIN_TOKEN_PATH\)\.mtimeMs/);
  const dockerfile = await fs.readFile(new URL("./Dockerfile", import.meta.url), "utf8");
  assert.match(dockerfile, /COPY src\/provisioner\/admin-session\.mjs/);
  assert.doesNotMatch(dockerfile, /^ENV .*\bMAS_ADMIN_TOKEN_(?:VERIFIED_)?PATH=/m);
});
