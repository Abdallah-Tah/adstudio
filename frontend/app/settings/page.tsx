"use client";

import { useCallback, useEffect, useState } from "react";
import { API, getJSON, postJSON } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type Provider = {
  id: string;
  name: string;
  role: string;
  connected: boolean;
};

export default function Settings() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string>("");
  const [msg, setMsg] = useState<Record<string, string>>({});

  const refresh = useCallback(() => {
    getJSON("/providers").then(setProviders).catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);

  async function save(id: string) {
    const key = (drafts[id] ?? "").trim();
    if (key.length < 8) return;
    setBusy(id);
    setMsg((m) => ({ ...m, [id]: "" }));
    try {
      const r = await postJSON(`/providers/${id}/key`, { api_key: key });
      setDrafts((d) => ({ ...d, [id]: "" }));
      setMsg((m) => ({ ...m, [id]: r.note ? `Saved. ${r.note}.` : "Saved." }));
      refresh();
    } catch (e) {
      setMsg((m) => ({ ...m, [id]: String(e) }));
    } finally {
      setBusy("");
    }
  }

  async function remove(id: string) {
    setBusy(id);
    try {
      await fetch(`${API}/providers/${id}/key`, { method: "DELETE" });
      setMsg((m) => ({ ...m, [id]: "Key removed." }));
      refresh();
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Provider tokens are stored server-side in a git-ignored file and are
          write-only: once saved, a key is never shown again — only its
          connection status.
        </p>
      </div>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
          Provider tokens
        </h2>
        {providers.map((p) => (
          <Card key={p.id} className="space-y-3 p-5">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold">{p.name}</h3>
                <p className="text-xs text-muted">{p.role}</p>
              </div>
              <Badge tone={p.connected ? "connected" : "missing"}>
                {p.connected ? "Connected" : "Not configured"}
              </Badge>
            </div>
            <div className="flex gap-2">
              <input
                type="password"
                autoComplete="off"
                placeholder={p.connected ? "•••••••• (saved — enter to replace)" : "Paste API key…"}
                value={drafts[p.id] ?? ""}
                onChange={(e) => setDrafts({ ...drafts, [p.id]: e.target.value })}
                className="flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 font-mono text-xs focus:border-accent focus:outline-none"
              />
              <Button
                size="sm"
                variant="accent"
                disabled={busy === p.id || (drafts[p.id] ?? "").trim().length < 8}
                onClick={() => save(p.id)}
              >
                {busy === p.id ? "Saving…" : "Save"}
              </Button>
              {p.connected && (
                <Button size="sm" variant="outline" disabled={busy === p.id} onClick={() => remove(p.id)}>
                  Remove
                </Button>
              )}
            </div>
            {msg[p.id] && <p className="text-xs text-muted">{msg[p.id]}</p>}
          </Card>
        ))}
      </section>

      <p className="text-xs text-muted">
        ⚠ This instance has no login yet (auth ships in the beta phase). Anyone
        who can reach this URL can change these keys — keep the tunnel URL
        private or put Cloudflare Access in front of it.
      </p>
    </div>
  );
}
