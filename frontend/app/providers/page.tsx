"use client";

import { useEffect, useState } from "react";
import { getJSON } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

type Provider = {
  id: string;
  name: string;
  role: string;
  models: string[];
  connected: boolean;
};

export default function Providers() {
  const [providers, setProviders] = useState<Provider[] | null>(null);

  useEffect(() => {
    getJSON("/providers").then(setProviders).catch(() => setProviders([]));
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-6 md:p-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Providers</h1>
        <p className="mt-1 text-sm text-muted">
          Every stage of the pipeline runs on a dedicated model. Keys are configured
          backend-side in <code className="rounded bg-line/50 px-1">backend/.env</code> — never in the browser.
        </p>
      </div>
      {providers === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {providers.map((p) => (
            <Card key={p.id} className="space-y-3 p-5">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">{p.name}</h2>
                <Badge tone={p.connected ? "connected" : "missing"}>
                  {p.connected ? "Connected" : "Not configured"}
                </Badge>
              </div>
              <p className="text-xs text-muted">{p.role}</p>
              {p.models.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {p.models.map((m) => (
                    <span key={m} className="rounded-md bg-line/50 px-2 py-0.5 font-mono text-[10px]">
                      {m}
                    </span>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
