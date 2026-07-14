"use client";

import { useEffect, useRef, useState } from "react";
import { API, getJSON } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type Listing = {
  project_id: string;
  name: string;
  scenes: number;
  created_at: string;
  cost_cents: number;
};

const STYLES = ["minimal_tech", "warm_lifestyle", "bold_energy", "studio_luxury", "ugc_handheld"];

export default function Home() {
  const [projects, setProjects] = useState<Listing[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    getJSON("/projects").then(setProjects).catch(() => {});
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!form.current) return;
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`${API}/projects`, { method: "POST", body: new FormData(form.current) });
      if (!r.ok) throw new Error(await r.text());
      const project = await r.json();
      window.location.href = `/projects/${project.project_id}`;
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl space-y-8 p-8">
      <h1 className="text-2xl font-bold">AI Ad Studio</h1>

      <Card className="p-6">
        <h2 className="mb-4 text-lg font-semibold">New project</h2>
        <form ref={form} onSubmit={create} className="space-y-3">
          <input type="file" name="photos" accept="image/*" multiple required className="block w-full text-sm" />
          <textarea name="description" required placeholder="Product description…" className="w-full rounded-md border border-neutral-300 p-2 text-sm" rows={3} />
          <div className="grid grid-cols-2 gap-3">
            <input name="audience" placeholder="Audience (optional)" className="rounded-md border border-neutral-300 p-2 text-sm" />
            <input name="offer" placeholder="Offer (optional)" className="rounded-md border border-neutral-300 p-2 text-sm" />
            <input name="cta" placeholder="CTA (optional)" className="rounded-md border border-neutral-300 p-2 text-sm" />
            <select name="style" className="rounded-md border border-neutral-300 p-2 text-sm">
              <option value="">Style (auto)</option>
              {STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <Button disabled={busy}>{busy ? "Storyboarding… (~30s)" : "Create storyboard"}</Button>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </form>
      </Card>

      <Card className="p-6">
        <h2 className="mb-4 text-lg font-semibold">Projects</h2>
        {projects.length === 0 && <p className="text-sm text-neutral-500">None yet.</p>}
        <ul className="divide-y divide-neutral-100">
          {projects.map((p) => (
            <li key={p.project_id} className="flex items-center justify-between py-2">
              <a href={`/projects/${p.project_id}`} className="text-sm font-medium hover:underline">
                {p.name}
              </a>
              <span className="text-xs text-neutral-500">
                {p.scenes} scenes · {(p.cost_cents / 100).toFixed(2)} $
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </main>
  );
}
