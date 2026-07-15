"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { assetUrl, getJSON } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type Listing = {
  project_id: string;
  name: string;
  scenes: number;
  created_at: string;
  updated_at: string;
  cost_cents: number;
  status: string;
  thumb_asset_id: string | null;
};

type Activity = {
  project_id: string;
  project_name: string;
  reason: string;
  created_at: string;
};

type Provider = { id: string; name: string; connected: boolean };

const STATUS_LABEL: Record<string, string> = {
  storyboard: "Storyboard ready",
  running: "Generating",
  image_ready: "Images in progress",
  images_ready: "Images ready",
  video_ready: "Video ready",
};

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function Dashboard() {
  const q = (useSearchParams().get("q") ?? "").toLowerCase();
  const [projects, setProjects] = useState<Listing[] | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);

  useEffect(() => {
    getJSON("/projects").then(setProjects).catch(() => setProjects([]));
    getJSON("/activity").then(setActivity).catch(() => {});
    getJSON("/providers").then(setProviders).catch(() => {});
  }, []);

  const filtered = (projects ?? []).filter((p) => p.name.toLowerCase().includes(q));
  const monthStart = new Date();
  monthStart.setDate(1);
  monthStart.setHours(0, 0, 0, 0);
  const monthSpend = (projects ?? [])
    .filter((p) => new Date(p.created_at) >= monthStart)
    .reduce((a, p) => a + p.cost_cents, 0);

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6 md:p-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-muted">
            Product photos in, platform-ready ads out.
          </p>
        </div>
        <Link href="/create">
          <Button variant="accent" size="lg">+ New Ad</Button>
        </Link>
      </div>

      {/* projects */}
      <section id="projects" className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
          Projects {q && <span className="normal-case">· matching “{q}”</span>}
        </h2>
        {projects === null ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : filtered.length === 0 ? (
          <Card className="flex flex-col items-center gap-3 p-12 text-center">
            <span className="text-3xl">✦</span>
            <p className="text-sm text-muted">
              {q ? "No projects match your search." : "No ads yet. Upload product photos and get a storyboard in ~30 seconds."}
            </p>
            {!q && (
              <Link href="/create"><Button variant="accent">Create your first ad</Button></Link>
            )}
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filtered.map((p) => (
              <Link key={p.project_id} href={`/projects/${p.project_id}`}>
                <Card className="group overflow-hidden transition-all hover:-translate-y-0.5 hover:shadow-md">
                  {p.thumb_asset_id ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={assetUrl(p.project_id, p.thumb_asset_id)}
                      alt=""
                      className="aspect-video w-full object-cover"
                    />
                  ) : (
                    <div className="flex aspect-video w-full items-center justify-center bg-line/40 text-2xl text-muted/50">
                      🎬
                    </div>
                  )}
                  <div className="space-y-2 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="truncate text-sm font-semibold">{p.name}</h3>
                      <Badge tone={p.status}>{STATUS_LABEL[p.status] ?? p.status}</Badge>
                    </div>
                    <div className="flex items-center justify-between text-xs text-muted">
                      <span>{p.scenes} scenes · ${(p.cost_cents / 100).toFixed(2)}</span>
                      <span>{ago(p.updated_at)}</span>
                    </div>
                    <div className="pt-1 text-xs font-medium text-accent opacity-0 transition-opacity group-hover:opacity-100">
                      Open →
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* recent activity */}
        <Card className="p-5 lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
            Recent activity
          </h2>
          {activity.length === 0 ? (
            <p className="text-sm text-muted">Nothing yet.</p>
          ) : (
            <ul className="space-y-2.5">
              {activity.slice(0, 8).map((a, i) => (
                <li key={i} className="flex items-center justify-between gap-3 text-sm">
                  <span className="truncate">
                    <Link href={`/projects/${a.project_id}`} className="font-medium hover:text-accent">
                      {a.project_name}
                    </Link>{" "}
                    <span className="text-muted">— {a.reason}</span>
                  </span>
                  <span className="shrink-0 text-xs text-muted">{ago(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* usage + providers */}
        <div className="space-y-6">
          <Card className="p-5">
            <h2 className="mb-1 text-sm font-semibold uppercase tracking-wider text-muted">
              API usage
            </h2>
            <p className="text-3xl font-semibold tracking-tight">
              ${(monthSpend / 100).toFixed(2)}
              <span className="ml-1.5 text-sm font-normal text-muted">this month</span>
            </p>
          </Card>
          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
                Providers
              </h2>
              <Link href="/providers" className="text-xs text-accent hover:underline">
                Manage →
              </Link>
            </div>
            <ul className="space-y-2">
              {providers.map((p) => (
                <li key={p.id} className="flex items-center justify-between text-sm">
                  {p.name}
                  <Badge tone={p.connected ? "connected" : "missing"}>
                    {p.connected ? "Connected" : "Not configured"}
                  </Badge>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  );
}
