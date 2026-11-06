/*
 * Foldfence contact-picker changes for Element Web.
 * SPDX-License-Identifier: AGPL-3.0-only
 */
import type { MatrixClient } from "matrix-js-sdk/src/matrix";

export interface ContactGrant {
    userId: string;
    allowed: ReadonlySet<string>;
    expiresAt: number;
}

export function parseContactGrant(value: unknown, userId: string, now = Date.now()): ContactGrant {
    const data = value as Record<string, unknown> | null;
    const expiresAt = typeof data?.expires_at === "string" ? Date.parse(data.expires_at) : NaN;
    if (
        data?.schema_version !== 1 || data.user_id !== userId ||
        !Number.isFinite(expiresAt) || expiresAt <= now || expiresAt - now > 60_000 ||
        !Array.isArray(data.allowed_user_ids) ||
        data.allowed_user_ids.some((id) => typeof id !== "string" || !id.startsWith("@") || id === userId)
    ) {
        throw new Error("Invalid or expired contact policy");
    }
    return { userId, allowed: new Set(data.allowed_user_ids as string[]), expiresAt };
}

export function permitsContact(grant: ContactGrant | null, userId: string, now = Date.now()): boolean {
    return !!grant && grant.expiresAt > now && grant.allowed.has(userId);
}

export async function loadContactGrant(client: MatrixClient): Promise<ContactGrant> {
    const token = client.getAccessToken();
    const userId = client.getUserId();
    if (!token || !userId) throw new Error("No authenticated Matrix session");
    const url = new URL("/_synapse/client/foldfence/contacts", client.getHomeserverUrl());
    const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error("Contact policy unavailable");
    const grant = parseContactGrant(await response.json(), userId);
    if (client.getUserId() !== userId) throw new Error("Matrix session changed during contact lookup");
    return grant;
}
