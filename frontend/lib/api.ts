export const API = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export async function getJSON(path: string) {
  const r = await fetch(`${API}${path}`, { cache: "no-store", credentials: "include" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function postJSON(path: string, body?: unknown) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function assetUrl(projectId: string, assetId: string) {
  return `${API}/projects/${projectId}/assets/${assetId}`;
}

export type AuthStatus = { registered: boolean; authenticated: boolean; email: string | null };

export async function authStatus(): Promise<AuthStatus> {
  const r = await fetch(`${API}/auth/status`, { cache: "no-store", credentials: "include" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
