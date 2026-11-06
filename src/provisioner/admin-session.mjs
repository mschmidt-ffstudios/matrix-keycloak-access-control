import crypto from "node:crypto";
import fs from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import path from "node:path";

const TOKEN_LIMIT = 8192;
const TOKEN_RE = /^[\x21-\x7e]{16,8192}$/;
const SAFE_ID_RE = /^[\x21-\x7e]{1,255}$/;

function fail(message) {
  throw new Error(message);
}

function privateFile(info, uid) {
  return info.isFile() && !info.isSymbolicLink() && info.uid === uid &&
    (info.mode & 0o777) === 0o600 && info.size <= TOKEN_LIMIT;
}

export async function ensurePrivateDirectory(directory, uid, fsImpl, label = "Synapse admin token directory") {
  let info;
  try {
    info = await fsImpl.lstat(directory);
  } catch {
    fail(`${label} is missing or unsafe`);
  }
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== uid) {
    fail(`${label} is missing or unsafe`);
  }
  let handle;
  try {
    handle = await fsImpl.open(directory, fsConstants.O_RDONLY | (fsConstants.O_DIRECTORY || 0) | (fsConstants.O_NOFOLLOW || 0));
    const opened = await handle.stat();
    if (!opened.isDirectory() || opened.uid !== uid || opened.dev !== info.dev || opened.ino !== info.ino) {
      fail(`${label} changed during validation`);
    }
    const permissions = opened.mode & 0o7777;
    if (permissions === 0o755) await handle.chmod(0o700);
    else if (permissions !== 0o700) fail(`${label} is missing or unsafe`);
    const secured = await handle.stat();
    if ((secured.mode & 0o7777) !== 0o700 || secured.uid !== uid) {
      fail(`${label} could not be secured`);
    }
  } catch (error) {
    if (error?.message?.startsWith(label)) throw error;
    fail(`${label} is missing or unsafe`);
  } finally {
    await handle?.close().catch(() => {});
  }
}

export async function readAdminTokenFile(filePath, { uid = process.getuid(), fsImpl = fs } = {}) {
  if (!path.isAbsolute(filePath) || !Number.isInteger(uid)) fail("Synapse admin token path or owner is invalid");
  await ensurePrivateDirectory(path.dirname(filePath), uid, fsImpl);
  let before;
  try {
    before = await fsImpl.lstat(filePath);
  } catch (error) {
    if (error?.code === "ENOENT") return { token: null, identity: null };
    fail("Synapse admin token file is unreadable or unsafe");
  }
  if (!privateFile(before, uid)) fail("Synapse admin token file is unreadable or unsafe");

  let handle;
  try {
    handle = await fsImpl.open(filePath, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0));
    const opened = await handle.stat();
    if (!privateFile(opened, uid) || opened.dev !== before.dev || opened.ino !== before.ino) {
      fail("Synapse admin token file changed during read");
    }
    const raw = await handle.readFile({ encoding: "utf8" });
    const token = raw.endsWith("\n") ? raw.slice(0, -1) : raw;
    if (!TOKEN_RE.test(token) || token.includes("\n") || token.includes("\r")) {
      fail("Synapse admin token file has an invalid format");
    }
    return { token, identity: { dev: opened.dev, ino: opened.ino } };
  } catch (error) {
    if (error?.message?.startsWith("Synapse admin token")) throw error;
    fail("Synapse admin token file is unreadable or unsafe");
  } finally {
    await handle?.close().catch(() => {});
  }
}

export async function verifyAdminSession({ userId, deviceId, accessToken, homeserverUrl, fetchImpl = fetch }) {
  if (!SAFE_ID_RE.test(userId || "") || !SAFE_ID_RE.test(deviceId || "") ||
      !TOKEN_RE.test(accessToken || "") || typeof homeserverUrl !== "string" || !homeserverUrl) {
    fail("Synapse admin token verification inputs are invalid");
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
      fail("Synapse admin token identity could not be verified");
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
  if (whoami.status === 401 && identity?.errcode === "M_UNKNOWN_TOKEN") return { status: "expired" };
  if (whoami.status !== 200 || identity?.user_id !== userId || identity?.device_id !== deviceId) {
    fail("Synapse admin token user/device identity does not match");
  }

  const account = await request(`${base}/_synapse/admin/v2/users/${encodeURIComponent(userId)}`);
  const adminRecord = await parse(account);
  // This protected endpoint checks the requester's Synapse/MAS admin scope.
  // Its `admin` field describes the target user's Synapse database flag, not
  // the authority carried by this access token (MAS admin scope is separate).
  if (account.status !== 200 || adminRecord?.name !== userId) {
    fail("Synapse admin token did not prove the expected administrator identity");
  }
  return { status: "valid" };
}

async function writePrivateFile(filePath, contents, existingIdentity, uid, fsImpl, label) {
  const directory = path.dirname(filePath);
  await ensurePrivateDirectory(directory, uid, fsImpl);
  const tempPath = `${filePath}.tmp-${process.pid}-${crypto.randomBytes(8).toString("hex")}`;
  let handle;
  try {
    handle = await fsImpl.open(tempPath, "wx", 0o600);
    const tempInfo = await handle.stat();
    if (!privateFile(tempInfo, uid)) fail("Temporary Synapse admin token permissions are unsafe");
    await handle.writeFile(contents, { encoding: "utf8" });
    await handle.sync();
    await handle.close();
    handle = null;

    if (existingIdentity) {
      let current;
      try {
        current = await fsImpl.lstat(filePath);
      } catch {
        fail("Synapse admin token file changed before replacement");
      }
      if (!privateFile(current, uid) || current.dev !== existingIdentity.dev || current.ino !== existingIdentity.ino) {
        fail("Synapse admin token file collision detected");
      }
      await fsImpl.rename(tempPath, filePath);
    } else {
      await fsImpl.link(tempPath, filePath);
      await fsImpl.unlink(tempPath);
    }
  } catch (error) {
    if (error?.message?.startsWith("Synapse admin token") || error?.message?.startsWith("Temporary Synapse")) throw error;
    fail(`${label} could not be written safely`);
  } finally {
    await handle?.close().catch(() => {});
    await fsImpl.unlink(tempPath).catch(() => {});
  }
}

async function writeAdminTokenFile(filePath, token, existingIdentity, uid, fsImpl) {
  await writePrivateFile(filePath, `${token}\n`, existingIdentity, uid, fsImpl, "Synapse admin token file");
}

async function readVerificationMarker(filePath, uid, fsImpl) {
  await ensurePrivateDirectory(path.dirname(filePath), uid, fsImpl);
  let before;
  try {
    before = await fsImpl.lstat(filePath);
  } catch (error) {
    if (error?.code === "ENOENT") return { identity: null };
    fail("Synapse admin verification marker is unsafe");
  }
  if (!privateFile(before, uid)) fail("Synapse admin verification marker is unsafe");
  let handle;
  try {
    handle = await fsImpl.open(filePath, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0));
    const opened = await handle.stat();
    if (!privateFile(opened, uid) || opened.dev !== before.dev || opened.ino !== before.ino) {
      fail("Synapse admin verification marker changed during read");
    }
    if ((await handle.readFile({ encoding: "utf8" })) !== "verified\n") {
      fail("Synapse admin verification marker is invalid");
    }
    return { identity: { dev: opened.dev, ino: opened.ino } };
  } catch (error) {
    if (error?.message?.startsWith("Synapse admin verification marker")) throw error;
    fail("Synapse admin verification marker is unsafe");
  } finally {
    await handle?.close().catch(() => {});
  }
}

async function writeVerificationMarker(filePath, uid, fsImpl) {
  const existing = await readVerificationMarker(filePath, uid, fsImpl);
  await writePrivateFile(filePath, "verified\n", existing.identity, uid, fsImpl, "Synapse admin verification marker");
}

export async function ensureSynapseAdminToken({
  username,
  userId,
  deviceId,
  tokenPath,
  verificationPath = `${tokenPath}.verified`,
  homeserverUrl,
  runMas,
  fetchImpl = fetch,
  fsImpl = fs,
  uid = process.getuid(),
}) {
  if (typeof username !== "string" || !/^[a-z0-9][a-z0-9._=-]*$/i.test(username) ||
      typeof userId !== "string" || typeof runMas !== "function") {
    fail("Synapse admin token identity configuration is invalid");
  }
  const userPrefix = `@${username}:`;
  if (!userId.startsWith(userPrefix) || userId.length <= userPrefix.length) {
    fail("Synapse admin token identity configuration is invalid");
  }

  if (!path.isAbsolute(verificationPath) || verificationPath === tokenPath) {
    fail("Synapse admin verification marker path is invalid");
  }
  const existing = await readAdminTokenFile(tokenPath, { uid, fsImpl });
  if (existing.token) {
    const status = await verifyAdminSession({
      userId, deviceId, accessToken: existing.token, homeserverUrl, fetchImpl,
    });
    if (status.status === "valid") {
      await writeVerificationMarker(verificationPath, uid, fsImpl);
      return { status: "reused" };
    }
  }

  let output;
  try {
    output = await runMas([
      "manage", "issue-compatibility-token", username, deviceId,
      "--yes-i-want-to-grant-synapse-admin-privileges",
    ]);
  } catch {
    fail("MAS could not issue the Synapse administrator session");
  }
  const match = String(output).match(/Compatibility token issued:\s*(\S+)/);
  if (!match?.[1] || !TOKEN_RE.test(match[1])) fail("MAS returned an invalid Synapse administrator token");
  const verification = await verifyAdminSession({
    userId, deviceId, accessToken: match[1], homeserverUrl, fetchImpl,
  });
  if (verification.status !== "valid") fail("New Synapse administrator session did not verify");
  await writeAdminTokenFile(tokenPath, match[1], existing.identity, uid, fsImpl);
  await writeVerificationMarker(verificationPath, uid, fsImpl);
  return { status: existing.token ? "renewed" : "issued" };
}
