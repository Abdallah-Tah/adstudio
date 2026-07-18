"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { assetUrl, getJSON } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, StatCard } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageHeader } from "@/components/header-store";

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

type Activity = { project_id: string; project_name: string; reason: string; created_at: string };
type Provider = { id: string; name: string; connected: boolean };

const STATUS_LABEL: Record<string, string> = {
  storyboard: "Storyboard",
  running: "Generating",
  image_ready: "Images",
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
  usePageHeader({ breadcrumb: [{ label: "Dashboard" }] });
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

  const stats = useMemo(() => {
    const ps = projects ?? [];
    const monthStart = new Date();
    monthStart.setDate(1);
    monthStart.setHours(0, 0, 0, 0);
    const monthSpend = ps.filter((p) => new Date(p.created_at) >= monthStart)
      .reduce((a, p) => a + p.cost_cents, 0);
    const ready = ps.filter((p) => p.status === "video_ready");
    const avg = ready.length ? ready.reduce((a, p) => a + p.cost_cents, 0) / ready.length : 0;
    return {
      monthSpend,
      videos: ready.length,
      totalAds: ps.length,
      avgCost: avg,
      providersOk: providers.filter((p) => p.connected).length,
      providersTotal: providers.length,
    };
  }, [projects, providers]);

  return (
    <div className="mx-auto max-w-7xl space-y-8 p-6 md:p-8 lg:p-10">
      {/* hero */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-display">Studio</h1>
          <p className="mt-1 text-secondary">Product photos in, platform-ready video ads out.</p>
        </div>
        <Link href="/create">
          <Button variant="ai" size="lg">
            <Icon name="sparkles" size={16} /> New Ad
          </Button>
        </Link>
      </div>

      {/* stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Spend this month" accent="text-accent-2"
          icon={<Icon name="analytics" size={18} />}
          value={projects === null ? <Skeleton className="h-7 w-20" /> : `$${(stats.monthSpend / 100).toFixed(2)}`}
          sub="across all projects"
        />
        <StatCard
          label="Videos generated" accent="text-info"
          icon={<Icon name="video" size={18} />}
          value={projects === null ? <Skeleton className="h-7 w-10" /> : stats.videos}
          sub={stats.videos ? `avg $${(stats.avgCost / 100).toFixed(2)} each` : "no finished ads yet"}
        />
        <StatCard
          label="Total projects" accent="text-success"
          icon={<Icon name="film" size={18} />}
          value={projects === null ? <Skeleton className="h-7 w-10" /> : stats.totalAds}
          sub="all time"
        />
        <StatCard
          label="Providers" accent="text-warn"
          icon={<Icon name="plug" size={18} />}
          value={providers.length === 0 ? <Skeleton className="h-7 w-12" /> : `${stats.providersOk}/${stats.providersTotal}`}
          sub={<Link href="/providers" className="text-accent-2 hover:underline">Manage →</Link>}
        />
      </div>

      {/* projects */}
      <section id="projects" className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-h2">
            Projects {q && <span className="font-normal text-muted">· “{q}”</span>}
          </h2>
          {filtered.length > 0 && (
            <span className="text-caption">{filtered.length} {filtered.length === 1 ? "ad" : "ads"}</span>
          )}
        </div>

        {projects === null ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="overflow-hidden">
                <Skeleton className="aspect-video w-full rounded-none" />
                <div className="space-y-2 p-4">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </Card>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<Icon name="sparkles" size={22} />}
              title={q ? "No projects match your search" : "Create your first ad"}
              description={q ? "Try a different keyword." : "Upload product photos and get a storyboard in ~30 seconds."}
              action={!q && (
                <Link href="/create"><Button variant="ai"><Icon name="sparkles" size={15} /> New Ad</Button></Link>
              )}
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filtered.map((p) => (
              <Link key={p.project_id} href={`/projects/${p.project_id}`}>
                <Card variant="interactive" className="group overflow-hidden">
                  <div className="relative">
                    {p.thumb_asset_id ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img src={assetUrl(p.project_id, p.thumb_asset_id)} alt="" className="aspect-video w-full object-cover" />
                    ) : (
                      <div className="flex aspect-video w-full items-center justify-center bg-surface-2 text-muted-2">
                        <Icon name="image" size={26} />
                      </div>
                    )}
                    <div className="absolute left-2 top-2">
                      <Badge tone={p.status} dot size="sm">{STATUS_LABEL[p.status] ?? p.status}</Badge>
                    </div>
                    {p.status === "video_ready" && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/25">
                        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-white/90 text-ink opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
                          <Icon name="play" size={16} />
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="space-y-1.5 p-4">
                    <h3 className="truncate text-h3">{p.name}</h3>
                    <div className="flex items-center justify-between text-xs text-muted">
                      <span>{p.scenes} scenes · ${(p.cost_cents / 100).toFixed(2)}</span>
                      <span>{ago(p.updated_at)}</span>
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* activity + providers */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <h2 className="mb-3 text-overline">Recent activity</h2>
          {activity.length === 0 ? (
            <EmptyState compact icon={<Icon name="history" size={18} />} title="No activity yet" description="Actions across your projects appear here." />
          ) : (
            <ul className="divide-y divide-line">
              {activity.slice(0, 8).map((a, i) => (
                <li key={i} className="flex items-center justify-between gap-3 py-2.5 text-sm first:pt-0">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    <Link href={`/projects/${a.project_id}`} className="truncate font-medium hover:text-accent-2">
                      {a.project_name}
                    </Link>
                    <span className="truncate text-muted">— {a.reason}</span>
                  </span>
                  <span className="shrink-0 text-xs text-muted">{ago(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-overline">Providers</h2>
            <Link href="/providers" className="text-xs text-accent-2 hover:underline">Manage →</Link>
          </div>
          {providers.length === 0 ? (
            <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
          ) : (
            <ul className="space-y-1">
              {providers.map((p) => (
                <li key={p.id} className="flex items-center justify-between rounded-lg px-1 py-1.5 text-sm">
                  <span className="flex items-center gap-2">
                    <Icon name="plug" size={14} className="text-muted" /> {p.name}
                  </span>
                  <Badge tone={p.connected ? "connected" : "missing"} dot size="sm">
                    {p.connected ? "Connected" : "Not set"}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<div className="p-10 text-sm text-muted">Loading…</div>}>
      <Dashboard />
    </Suspense>
  );
}
