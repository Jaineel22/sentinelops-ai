// Client-side auth helpers (Phase 10.1). The token is a real JWT issued by
// apps/api (`/api/v1/auth/login`, proxied at `/api/auth/login`) — this module
// never invents identity itself, it only stores/decodes what the server issued.
//
// Scope note: apps/api's JWT authenticates the *dashboard's login screen*. The
// four backend services it renders (incident/rca/remediation/detector) are
// still unauthenticated internal services (Phase 10 note, unchanged) — RBAC
// here gates the *UI*, matching what a `curl` caller could already do.

export type Role = "viewer" | "approver" | "admin";

export interface AuthUser {
  username: string;
  role: Role;
  disabled: boolean;
}

const TOKEN_KEY = "sentinelops.token";
const ROLE_RANK: Record<Role, number> = { viewer: 0, approver: 1, admin: 2 };

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null; // SSR, or storage blocked
  }
}

export function getToken(): string | null {
  return storage()?.getItem(TOKEN_KEY) ?? null;
}

function setToken(token: string): void {
  storage()?.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  storage()?.removeItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}

/** Decode the JWT payload client-side — no network call, just for a snappy UI
 * (username/role display, gating buttons). The token is still verified
 * server-side on every API call; a tampered token simply gets a 401/403 back. */
export function currentUser(): AuthUser | null {
  const token = getToken();
  if (!token) return null;
  try {
    const [, payloadB64] = token.split(".");
    if (!payloadB64) return null;
    const payload = JSON.parse(atob(payloadB64.replace(/-/g, "+").replace(/_/g, "/")));
    if (typeof payload.sub !== "string" || typeof payload.role !== "string") return null;
    return { username: payload.sub, role: payload.role as Role, disabled: false };
  } catch {
    return null;
  }
}

export function hasRole(minimum: Role): boolean {
  const user = currentUser();
  if (!user) return false;
  return ROLE_RANK[user.role] >= ROLE_RANK[minimum];
}

export class AuthError extends Error {}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new AuthError(
      typeof body?.detail === "string" ? body.detail : "incorrect username or password",
    );
  }
  const { access_token } = (await res.json()) as { access_token: string };
  setToken(access_token);
  return await fetchMe();
}

export async function fetchMe(): Promise<AuthUser> {
  const token = getToken();
  if (!token) throw new AuthError("not authenticated");
  const res = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    clearToken();
    throw new AuthError("session expired");
  }
  return (await res.json()) as AuthUser;
}

export function logout(): void {
  clearToken();
  window.location.href = "/login";
}
