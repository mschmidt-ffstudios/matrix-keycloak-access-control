import crypto from "node:crypto";
import fs from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import path from "node:path";
import { ensurePrivateDirectory } from "./admin-session.mjs";

const SESSION_FIELDS = new Set(["schema_version", "user_id", "device_id", "access_token", "admin"]);
const SESSION_LIMIT = 8192;
const ACCESS_TOKEN_RE = /^[\x21-\x7e]{16,8192}$/;
const SAFE_ID_RE = /^[\x21-\x7e]{1,255}$/;

function fail(message) {
  throw new Error(message);
}

function validateDocument(document, userId, deviceId) {
  if (!document || typeof document !== "object" || Array.isArray(document) ||
      Object.keys(document).length !== SESSION_FIELDS.size ||
      Object.keys(document).some((key) => !SESSION_FIELDS.has(key)) ||
      document.schema_version !== 1 ||
      document.user_id !== userId || !SAFE_ID_RE.test(document.user_id || "") ||
      document.device_id !== deviceId || !SAFE_ID_RE.test(document.device_id || "") ||
      document.admin !== false || !ACCESS_TOKEN_RE.test(document.access_token || "")) {
    fail("OrgBot session file has an invalid identity, schema, or privilege contract");
  }
  return document;
}

function sessionFileMetadata(info, uid) {
  return info.isFile() && !info.isSymbolicLink() && info.uid === uid &&
    (info.mode & 0o777) === 0o600;
}

async function assertPrivateDirectory(directory, uid, fsImpl) {
  let info;
  try {
    info = await fsImpl.lstat(directory);
  } catch {
    fail("OrgBot session directory is missing or unsafe");
  }
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== uid || (info.mode & 0o777) !== 0o700) {
    fail("OrgBot session directory is missing or unsafe");
  }
}

export async function readPrivateSessionFile(filePath, { userId, deviceId, uid = process.getuid(), fsImpl = fs }) {
  if (!path.isAbsolute(filePath) || !Number.isInteger(uid)) fail("OrgBot session file path or owner is invalid");
  const directory = path.dirname(filePath);
  await assertPrivateDirectory(directory, uid, fsImpl);

  let before;
  try {
    before = await fsImpl.lstat(filePath);
  } catch (error) {
    if (error?.code === "ENOENT") return { document: null, identity: null };
    fail("OrgBot session file is unreadable or unsafe");
  }
  if (!sessionFileMetadata(before, uid) || before.size > SESSION_LIMIT) {
    fail("OrgBot session file is unreadable or unsafe");
  }

  let handle;
  try {
    handle = await fsImpl.open(filePath, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0));
    const opened = await handle.stat();
    if (!sessionFileMetadata(opened, uid) || opened.dev !== before.dev || opened.ino !== before.ino || opened.size > SESSION_LIMIT) {
      fail("OrgBot session file changed during read");
    }
    const contents = await handle.readFile({ encoding: "utf8" });
    const document = validateDocument(JSON.parse(contents), userId, deviceId);
    return { document, identity: { dev: opened.dev, ino: opened.ino } };
  } catch (error) {
    if (error?.message?.startsWith("OrgBot session")) throw error;
    fail("OrgBot session file is unreadable or unsafe");
  } finally {
    await handle?.close().catch(() => {});
  }
}

export async function verifyNonAdminSession({ userId, deviceId, accessToken, homeserverUrl, fetchImpl = fetch }) {
  if (!SAFE_ID_RE.test(userId || "") || !SAFE_ID_RE.test(deviceId || "") ||
      !ACCESS_TOKEN_RE.test(accessToken || "") || typeof homeserverUrl !== "string" || !homeserverUrl) {
    fail("OrgBot session verification inputs are invalid");
  }
  const base = homeserverUrl.replace(/\/$/, "");
  const headers = { authorization: `Bearer ${accessToken}` };
  const request = async (url) => {
    try {
      return await fetchImpl(url, {
        method: "GET",
        headers,
        redirect: "manual",
        signal: AbortSignal.timeout(10000),
      });
    } catch {
      fail("OrgBot session identity could not be verified");
    }
  };
  const parse = async (response) => {
    try {
      return await response.json();
    } catch {
      return null;
    }
  };

  const whoami = await request(`${base}/_matrix/client/v3/account/whoami`);
  const identity = await parse(whoami);
  if (whoami.status === 401 && identity?.errcode === "M_UNKNOWN_TOKEN") {
    return { status: "expired" };
  }
  if (whoami.status !== 200 || identity?.user_id !== userId || identity?.device_id !== deviceId) {
    fail("OrgBot Matrix session user/device identity does not match");
  }

  const adminProbe = await request(
    `${base}/_synapse/admin/v1/users/${encodeURIComponent(userId)}/joined_rooms`,
  );
  const denial = await parse(adminProbe);
  if (adminProbe.status !== 403 || denial?.errcode !== "M_FORBIDDEN") {
    fail("OrgBot Matrix session did not prove non-admin access");
  }
  return { status: "valid" };
}

async function writePrivateSessionFile(filePath, document, existingIdentity, uid, fsImpl) {
  const directory = path.dirname(filePath);
  await assertPrivateDirectory(directory, uid, fsImpl);
  const tempPath = `${filePath}.tmp-${process.pid}-${crypto.randomBytes(8).toString("hex")}`;
  let handle;
  try {
    handle = await fsImpl.open(tempPath, "wx", 0o600);
    const tempInfo = await handle.stat();
    if (!sessionFileMetadata(tempInfo, uid)) fail("Temporary OrgBot session file permissions are unsafe");
    await handle.writeFile(`${JSON.stringify(document)}\n`, { encoding: "utf8" });
    await handle.sync();
    await handle.close();
    handle = null;

    if (existingIdentity) {
      let current;
      try {
        current = await fsImpl.lstat(filePath);
      } catch {
        fail("OrgBot session file changed before replacement");
      }
      if (!sessionFileMetadata(current, uid) || current.dev !== existingIdentity.dev || current.ino !== existingIdentity.ino) {
        fail("OrgBot session file collision detected");
      }
      await fsImpl.rename(tempPath, filePath);
    } else {
      // link() creates the final name atomically and fails if any file or
      // symlink already occupies it; rename() would overwrite that collision.
      await fsImpl.link(tempPath, filePath);
      await fsImpl.unlink(tempPath);
    }
  } catch (error) {
    if (error?.message?.startsWith("OrgBot session") || error?.message?.startsWith("Temporary OrgBot")) throw error;
    fail("OrgBot session file could not be written safely");
  } finally {
    await handle?.close().catch(() => {});
    await fsImpl.unlink(tempPath).catch(() => {});
  }
}

export async function ensureOrgBotSession({
  username,
  userId,
  deviceId,
  sessionPath,
  homeserverUrl,
  runMas,
  fetchImpl = fetch,
  fsImpl = fs,
  uid = process.getuid(),
}) {
  if (typeof username !== "string" || !/^[a-z0-9][a-z0-9._=-]*$/i.test(username) ||
      typeof userId !== "string" ||
      typeof runMas !== "function") {
    fail("OrgBot session identity configuration is invalid");
  }
  const userPrefix = `@${username}:`;
  if (!userId.startsWith(userPrefix) || userId.length <= userPrefix.length) {
    fail("OrgBot session identity configuration is invalid");
  }

  // Older images seeded the owned named volume at 0755 before first boot.
  // Harden only that known mode before reading or creating a session.
  if (!path.isAbsolute(sessionPath) || !Number.isInteger(uid)) fail("OrgBot session file path or owner is invalid");
  await ensurePrivateDirectory(path.dirname(sessionPath), uid, fsImpl, "OrgBot session directory");
  const existing = await readPrivateSessionFile(sessionPath, { userId, deviceId, uid, fsImpl });
  if (existing.document) {
    const status = await verifyNonAdminSession({
      userId, deviceId, accessToken: existing.document.access_token, homeserverUrl, fetchImpl,
    });
    if (status.status === "valid") return { status: "reused" };
  }

  let output;
  try {
    output = await runMas(["manage", "issue-compatibility-token", username, deviceId]);
  } catch {
    fail("MAS could not issue the non-admin OrgBot session");
  }
  const match = String(output).match(/Compatibility token issued:\s*(\S+)/);
  if (!match?.[1] || !ACCESS_TOKEN_RE.test(match[1])) fail("MAS returned an invalid OrgBot session token");
  const document = { schema_version: 1, user_id: userId, device_id: deviceId, access_token: match[1], admin: false };
  const verification = await verifyNonAdminSession({
    userId, deviceId, accessToken: document.access_token, homeserverUrl, fetchImpl,
  });
  if (verification.status !== "valid") fail("New OrgBot Matrix session did not verify");
  await writePrivateSessionFile(sessionPath, document, existing.identity, uid, fsImpl);
  return { status: existing.document ? "renewed" : "issued" };
}
