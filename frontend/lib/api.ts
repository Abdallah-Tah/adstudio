export const API = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export async function getJSON(path: string) {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function postJSON(path: string, body?: unknown) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function assetUrl(projectId: string, assetId: string) {
  return `${API}/projects/${projectId}/assets/${assetId}`;
}
