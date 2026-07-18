"use client";

import { useCallback, useEffect, useState } from "react";
import { API, getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageHeader } from "@/components/header-store";

type Provider = {
  id: string;
  name: string;
  role: string;
  models: string[];
  active_model?: string;
  connected: boolean;
};

// Brand glyph + accent per provider (no trademarked logos).
const BRAND: Record<string, { glyph: string; className: string }> = {
  openai: { glyph: "◎", className: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  fal: { glyph: "✦", className: "bg-violet-500/15 text-violet-600 dark:text-violet-400" },
  anthropic: { glyph: "◈", className: "bg-orange-500/15 text-orange-600 dark:text-orange-400" },
  elevenlabs: { glyph: "◐", className: "bg-blue-500/15 text-blue-600 dark:text-blue-400" },
  music: { glyph: "♪", className: "bg-pink-500/15 text-pink-600 dark:text-pink-400" },
};

// Reference pricing mirrored from backend/app/pricing.py (PRICING_VERSION
// 2026-07-15). Display-only; the backend is the source of truth for billing.
const PRICING: Record<string, string> = {
  openai: "Text $0.75 / $4.50 per 1M tok · Images $0.006–$0.21 each",
  fal: "Kling $0.084/s · LTX $0.02/s (audio off)",
  anthropic: "Haiku $1.00 / $5.00 per 1M tok",
  elevenlabs: "≈ $0.22 per 1k characters",
  music: "Licensed stock catalog · per-track license",
};

export default function Providers() {
  usePageHeader({ breadcrumb: [{ label: "Providers" }] });
  const [providers, setProviders] = useState<Provider[] | null>(null);

  const refresh = useCallback(() => {
    getJSON("/providers").then(setProviders).catch(() => setProviders([]));
  }, []);
  useEffect(refresh, [refresh]);

  const okCount = providers?.filter((p) => p.connected).length ?? 0;

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8 lg:p-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-h1">Providers</h1>
          <p className="mt-1 max-w-2xl text-secondary">
            Every stage runs on a dedicated model. Keys are stored server-side in a
            git-ignored file and are write-only — once saved, only connection status is shown.
          </p>
        </div>
        {providers && (
          <Badge tone={okCount === providers.length ? "connected" : "missing"} dot>
            {okCount}/{providers.length} configured
          </Badge>
        )}
      </div>

      {providers === null ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-52 rounded-xl" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {providers.map((p) => (
            <ProviderCard key={p.id} p={p} onSaved={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}

function ProviderCard({ p, onSaved }: { p: Provider; onSaved: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [checked, setChecked] = useState<string | null>(null);
  const brand = BRAND[p.id] ?? { glyph: "●", className: "bg-line/60 text-muted" };

  useEffect(() => {
    setChecked(localStorage.getItem(`prov_checked_${p.id}`));
  }, [p.id]);

  async function save() {
    if (key.trim().length < 8) return;
    setBusy(true); setMsg("");
    try {
      const r = await postJSON(`/providers/${p.id}/key`, { api_key: key.trim() });
      setKey(""); setMsg(r.note ? "Saved. Restart the worker to pick it up there." : "Saved.");
      onSaved();
    } catch (e) { setMsg(String(e)); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setMsg("");
    try {
      await fetch(`${API}/providers/${p.id}/key`, { method: "DELETE", credentials: "include" });
      setMsg("Key removed."); onSaved();
    } finally { setBusy(false); }
  }
  async function recheck() {
    setBusy(true);
    try {
      await getJSON("/providers");
      const now = new Date().toLocaleString();
      localStorage.setItem(`prov_checked_${p.id}`, now);
      setChecked(now);
      onSaved();
    } finally { setBusy(false); }
  }

  return (
    <Card className="flex flex-col gap-4 p-5">
      {/* header */}
      <div className="flex items-start gap-3">
        <span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg", brand.className)}>
          {brand.glyph}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-h3">{p.name}</h2>
            <Badge tone={p.connected ? "connected" : "missing"} dot size="sm">
              {p.connected ? "Connected" : "Not configured"}
            </Badge>
          </div>
          <p className="mt-0.5 text-xs text-muted">{p.role}</p>
        </div>
      </div>

      {/* models */}
      {p.models.length > 0 && (
        <div>
          <p className="mb-1.5 text-overline">Models</p>
          <div className="flex flex-wrap gap-1.5">
            {p.models.map((m) => {
              const active = p.active_model === m;
              return (
                <span key={m} className={cn("rounded-md px-2 py-0.5 font-mono text-[10px]",
                  active ? "bg-accent-soft text-accent-2 ring-1 ring-accent/40" : "bg-surface-2 text-muted")}>
                  {m}{active && p.models.length > 1 ? " · active" : ""}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* pricing */}
      <div>
        <p className="mb-1 text-overline">Pricing <span className="font-normal normal-case text-muted-2">reference</span></p>
        <p className="text-xs text-muted">{PRICING[p.id] ?? "—"}</p>
      </div>

      {/* key management */}
      <div className="mt-auto space-y-2 border-t border-line pt-4">
        <p className="text-overline">API key</p>
        <div className="flex gap-2">
          <input
            type="password" autoComplete="off"
            placeholder={p.connected ? "•••••••• saved — enter to replace" : "Paste API key…"}
            value={key} onChange={(e) => setKey(e.target.value)}
            className="focus-ring flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 font-mono text-xs"
          />
          <Button size="sm" variant="accent" disabled={busy || key.trim().length < 8} onClick={save}>
            {busy ? "…" : "Save"}
          </Button>
          {p.connected && (
            <Button size="sm" variant="outline" disabled={busy} onClick={remove}>Remove</Button>
          )}
        </div>
        <div className="flex items-center justify-between">
          <button
            onClick={recheck} disabled={busy}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md px-1 py-0.5 text-xs text-accent-2 hover:underline disabled:opacity-50"
            title="Re-reads server configuration status"
          >
            <Icon name="refresh" size={13} /> Recheck
          </button>
          <span className="text-[11px] text-muted-2">
            {checked ? `Last checked ${checked}` : "Not checked yet"}
          </span>
        </div>
        {msg && <p className="text-[11px] text-muted">{msg}</p>}
      </div>
    </Card>
  );
}
