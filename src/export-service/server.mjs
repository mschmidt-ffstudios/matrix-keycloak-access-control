import { createHash, randomUUID } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, mkdtemp, open, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createRemoteJWKSet, jwtVerify } from "jose";
import { loadCurrentRooms, requestedScope } from "./room-access.mjs";

const PORT = positiveInteger(process.env.PORT ?? "8090", "PORT");
const ISSUER = required("EXPORT_OIDC_ISSUER");
const JWKS_URL = required("EXPORT_OIDC_JWKS_URL");
const REQUIRED_ROLE = process.env.EXPORT_ADMIN_ROLE ?? "org-export-admin";
const ALLOWED_AZP = csv(required("EXPORT_ALLOWED_AZP"));
const ROOM_MAP_PATH = process.env.EXPORT_ROOM_MAP_PATH ?? "";
const CHAT_POLICY_PATH = process.env.EXPORT_CHAT_POLICY_PATH || (ROOM_MAP_PATH ? "/data/chat-policy.json" : "");
const DIRECT_ROOMS_PATH = process.env.EXPORT_DIRECT_ROOMS_PATH || "/run/chat-guard/direct-rooms.json";
const DYNAMIC_POLICY_CONFIGURED = Boolean(ROOM_MAP_PATH || process.env.EXPORT_CHAT_POLICY_PATH);
if (!DYNAMIC_POLICY_CONFIGURED) throw new Error("Current Matrix entitlement policy is required for export");
const BOT_EXPORT_URL = required("BOT_EXPORT_URL");
const BOT_EXPORT_TOKEN = required("BOT_EXPORT_TOKEN");
const AUDIT_PATH = process.env.EXPORT_AUDIT_PATH ?? "/data/export-audit.jsonl";
const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_ARCHIVE_BYTES = positiveInteger(process.env.EXPORT_MAX_ARCHIVE_BYTES ?? "209715200", "EXPORT_MAX_ARCHIVE_BYTES");
const EXPORT_FORMAT = "matrix-portable-v1";
if (ALLOWED_AZP.length === 0) throw new Error("EXPORT_ALLOWED_AZP must contain at least one allowed Keycloak client ID");
const jwks = createRemoteJWKSet(new URL(JWKS_URL));

async function currentRooms(actor) {
  return loadCurrentRooms({
    roomMapPath: ROOM_MAP_PATH,
    chatPolicyPath: CHAT_POLICY_PATH,
    registryPath: DIRECT_ROOMS_PATH,
    actorSubject: actor.subject,
  });
}

function positiveInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) throw new Error(`${name} must be a positive integer`);
  return parsed;
}

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Required configuration is missing: ${name}`);
  return value;
}

function csv(value) {
  return (value ?? "").split(",").map((item) => item.trim()).filter(Boolean);
}

function json(res, status, value) {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(JSON.stringify(value));
}

async function writeAudit(record) {
  await mkdir(dirname(AUDIT_PATH), { recursive: true });
  const file = await open(AUDIT_PATH, "a", 0o600);
  try {
    await file.chmod(0o600);
    await file.appendFile(`${JSON.stringify(record)}\n`);
  } finally {
    await file.close();
  }
}

async function readJson(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) throw Object.assign(new Error("Request body is too large"), { status: 413 });
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw Object.assign(new Error("Request body must be valid JSON"), { status: 400 });
  }
}

function bearerToken(req) {
  const match = /^Bearer\s+(.+)$/i.exec(req.headers.authorization ?? "");
  return match?.[1];
}

async function authorize(req) {
  const token = bearerToken(req);
  if (!token) throw Object.assign(new Error("Bearer token required"), { status: 401 });
  let payload;
  try {
    ({ payload } = await jwtVerify(token, jwks, {
      issuer: ISSUER,
      algorithms: ["RS256"],
    }));
  } catch {
    throw Object.assign(new Error("Token is invalid or expired"), { status: 401 });
  }

  const roles = payload.realm_access?.roles ?? [];
  const actor = {
    subject: typeof payload.sub === "string" && payload.sub.length > 0 ? payload.sub : "unknown",
    issuer: payload.iss,
    clientId: typeof payload.azp === "string" ? payload.azp : "unknown",
  };
  if (!Array.isArray(roles) || !roles.includes(REQUIRED_ROLE)) {
    throw Object.assign(new Error("Export role required"), { status: 403, actor });
  }
  if (!ALLOWED_AZP.includes(payload.azp)) {
    throw Object.assign(new Error("Token client is not allowed"), { status: 403, actor });
  }
  if (actor.subject === "unknown") {
    throw Object.assign(new Error("Token subject is missing"), { status: 401 });
  }
  return actor;
}

async function requestBotArchive(requestId, actor, requested) {
  const response = await fetch(BOT_EXPORT_URL, {
    method: "POST",
    headers: {
      authorization: `Bearer ${BOT_EXPORT_TOKEN}`,
      "content-type": "application/json",
      accept: "application/zip",
    },
    body: JSON.stringify({
      request_id: requestId,
      requested_by: actor.subject,
      scope: requested.scope,
      room_ids: requested.roomIds,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!response.ok) throw Object.assign(new Error("OrgBot export request failed"), { status: 502 });
  const contentType = (response.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/zip") {
    throw Object.assign(new Error("OrgBot did not return a ZIP archive"), { status: 502 });
  }
  if (response.headers.get("x-export-format") !== EXPORT_FORMAT) {
    throw Object.assign(new Error("OrgBot did not identify the required portable export format"), { status: 502 });
  }

  const echoedRooms = csv(response.headers.get("x-export-room-ids"));
  if (echoedRooms.length === 0 || !sameSet(echoedRooms, requested.roomIds)) {
    throw Object.assign(new Error("OrgBot did not confirm the exact requested room scope"), { status: 502 });
  }
  const completeness = response.headers.get("x-export-completeness") ?? "unknown";
  if (!["complete", "partial", "unknown"].includes(completeness)) {
    throw Object.assign(new Error("OrgBot returned an invalid completeness status"), { status: 502 });
  }

  const stored = await storeArchive(response);
  return { ...stored, completeness };
}

async function storeArchive(response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_ARCHIVE_BYTES) {
    await response.body?.cancel();
    throw Object.assign(new Error("OrgBot archive exceeds the configured size limit"), { status: 502 });
  }
  if (!response.body) throw Object.assign(new Error("OrgBot response has no archive body"), { status: 502 });

  const directory = await mkdtemp(join(tmpdir(), "matrix-export-"));
  const path = join(directory, "export.zip");
  const hash = createHash("sha256");
  let size = 0;
  try {
    const boundedHash = new Transform({
      transform(chunk, _encoding, callback) {
        size += chunk.length;
        if (size > MAX_ARCHIVE_BYTES) {
          callback(Object.assign(new Error("OrgBot archive exceeds the configured size limit"), { status: 502 }));
          return;
        }
        hash.update(chunk);
        callback(null, chunk);
      },
    });
    await pipeline(
      Readable.fromWeb(response.body),
      boundedHash,
      createWriteStream(path, { flags: "wx", mode: 0o600 }),
    );
    if (size === 0) throw Object.assign(new Error("OrgBot returned an empty archive"), { status: 502 });
    return { path, tempDir: directory, bytes: size, sha256: hash.digest("hex") };
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

function sameSet(left, right) {
  const a = [...new Set(left)].sort();
  const b = [...new Set(right)].sort();
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

async function handleExport(req, res) {
  const requestId = randomUUID();
  let actor = { subject: "unknown", issuer: "unknown" };
  let requested = { scope: "unknown", roomIds: [] };
  let archive;
  let auditWritten = false;
  try {
    actor = await authorize(req);
    const body = await readJson(req);
    requested = {
      scope: body?.scope === "all" || body?.scope === "selected" ? body.scope : "unknown",
      roomIds: Array.isArray(body?.room_ids)
        ? [...new Set(body.room_ids.filter((roomId) => typeof roomId === "string" && roomId.length <= 255))].sort()
        : [],
    };
    requested = requestedScope(body, await currentRooms(actor));
    await writeAudit({
      request_id: requestId,
      at: new Date().toISOString(),
      actor_sub: actor.subject,
      issuer: actor.issuer,
      client_id: actor.clientId,
      scope: requested.scope,
      room_ids: requested.roomIds,
      state: "accepted",
    });
    auditWritten = true;
    archive = await requestBotArchive(requestId, actor, requested);
    // A valid old JWT must not outlive a fresh role withdrawal while the ZIP
    // was being prepared. Recheck the actor and the exact selected room scope.
    requestedScope({ scope: "selected", room_ids: requested.roomIds }, await currentRooms(actor));
    await writeAudit({
      request_id: requestId,
      at: new Date().toISOString(),
      actor_sub: actor.subject,
      issuer: actor.issuer,
      client_id: actor.clientId,
      scope: requested.scope,
      room_ids: requested.roomIds,
      state: "completed",
      export_format: EXPORT_FORMAT,
      completeness: archive.completeness,
      sha256: archive.sha256,
      bytes: archive.bytes,
    });
    res.writeHead(200, {
      "content-type": "application/zip",
      "content-disposition": `attachment; filename="matrix-export-${requestId}.zip"`,
      "cache-control": "no-store",
      "x-export-request-id": requestId,
      "x-export-completeness": archive.completeness,
      "x-export-sha256": archive.sha256,
      "x-export-format": EXPORT_FORMAT,
      "x-export-room-ids": requested.roomIds.join(","),
      "x-content-type-options": "nosniff",
    });
    await pipeline(createReadStream(archive.path), res);
  } catch (error) {
    if (error.actor) actor = error.actor;
    const status = error.status ?? 502;
    try {
      await writeAudit({
        request_id: requestId,
        at: new Date().toISOString(),
        actor_sub: actor.subject,
        issuer: actor.issuer,
        client_id: actor.clientId ?? "unknown",
        scope: requested.scope,
        room_ids: requested.roomIds,
        state: auditWritten ? "failed" : "denied",
        http_status: status,
        reason: status === 401 ? "unauthenticated"
          : status === 403 ? "forbidden"
            : status === 400 ? "invalid_request"
              : status === 413 ? "request_too_large"
                : status === 503 ? "service_unavailable"
                  : "upstream_failure",
      });
    } catch {
      if (!res.headersSent) return json(res, 503, { error: "Export audit is unavailable; request failed closed" });
      res.destroy();
      return;
    }
    if (!res.headersSent) {
      const publicMessage = status === 401 ? "Authentication required"
        : status === 403 ? "Export is not authorized"
          : status === 400 ? "Invalid export request"
            : status === 413 ? "Export request is too large"
              : status === 503 ? "Export service is unavailable"
                : "Export request failed";
      json(res, status, { error: publicMessage, request_id: requestId });
    }
  } finally {
    if (archive?.tempDir) {
      try {
        await rm(archive.tempDir, { recursive: true, force: true });
      } catch {
        console.error("Temporary export cleanup failed");
      }
    }
  }
}

async function listRooms(req, res) {
  try {
    const actor = await authorize(req);
    const rooms = await currentRooms(actor);
    return json(res, 200, {
      rooms: rooms.map((room) => ({
        ...room,
        exportable: true,
      })),
    });
  } catch (error) {
    const status = error.status ?? 502;
    return json(res, status, {
      error: status === 401 ? "Authentication required"
        : status === 403 ? "Export is not authorized"
          : "Room list is unavailable",
    });
  }
}

const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/healthz") {
    return json(res, 200, { status: "ok" });
  }
  if (req.method === "GET" && req.url === "/v1/rooms") {
    return listRooms(req, res);
  }
  if (req.method === "POST" && req.url === "/v1/exports") {
    return handleExport(req, res);
  }
  return json(res, 404, { error: "Not found" });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Export service listening on ${PORT}`);
});
