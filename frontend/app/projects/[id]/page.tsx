"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { assetUrl, getJSON, postJSON } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icon";
import { EmptyState } from "@/components/ui/empty-state";
import { Stepper, type Step } from "@/components/ui/stepper";
import { CompareSlider } from "@/components/ui/compare-slider";
import { usePageHeader } from "@/components/header-store";
import { cn } from "@/lib/utils";

type Generation = {
  generation_id: string;
  kind: string;
  status: string;
  stale?: boolean;
  cost_cents: number;
  qc_notes: string | null;
  asset: { asset_id: string } | null;
  created_at: string;
};

type Scene = {
  scene_id: string;
  order: number;
  duration_s: number;
  camera: string;
  lighting: string;
  action: string;
  vo_line: string | null;
  caption: string | null;
  caption_style: string;
  transition_out: string;
  generations: Generation[];
  selected_image: string | null;
  selected_video: string | null;
  generation_attempts: number;
};

type ProcessingWarning = { code: string; message: string; asset_id: string | null };

type Project = {
  project_id: string;
  product: { name: string; processing_warnings: ProcessingWarning[] };
  strategy: { hook: string; style_id: string };
  brief: { target_duration_s: number };
  scenes: Scene[];
  storyboard_approval: { status: string; approved_at: string | null };
  final_render: { asset_id: string } | null;
  cost: Record<string, number> & { total: number };
};

/* ------------------------------- helpers -------------------------------- */

function videoAttempts(s: Scene) {
  return s.generations.filter((g) => g.kind === "video").length;
}
function selectedVideoGen(s: Scene) {
  return s.generations.find((g) => g.generation_id === s.selected_video);
}
function sceneCost(s: Scene) {
  return s.generations.reduce((a, g) => a + g.cost_cents, 0);
}
function thumbGen(s: Scene): Generation | undefined {
  return (
    s.generations.find((g) => g.generation_id === s.selected_image) ??
    [...s.generations].reverse().find((g) => g.kind !== "video" && g.status === "succeeded")
  );
}
function sceneStatus(s: Scene): string {
  if (s.generations.some((g) => g.status === "queued" || g.status === "running")) return "generating";
  if (s.selected_video) return "completed";
  if (!s.selected_video && s.generations.some((g) => g.kind === "video" && g.status === "qc_rejected")) return "qc_failed";
  if (s.selected_image) return "ready";
  return "draft";
}
const STATUS_LABEL: Record<string, string> = {
  generating: "Generating", completed: "Completed", qc_failed: "QC failed",
  ready: "Ready", draft: "Draft",
};

const INTENT_GROUPS = [
  { title: "Visual", fields: [["action", "Action"], ["camera", "Camera"], ["lighting", "Lighting"]] },
  { title: "Dialogue & captions", fields: [["vo_line", "Voiceover line"], ["caption", "On-screen caption"]] },
] as const;

const TABS = [["scenes", "Scenes"], ["preview", "Preview"], ["intent", "Intent"]] as const;

/* -------------------------------- editor -------------------------------- */

export default function Editor({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [project, setProject] = useState<Project | null>(null);
  const [selected, setSelected] = useState(0);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("preview");
  const [menu, setMenu] = useState<{ x: number; y: number; idx: number } | null>(null);

  const refresh = useCallback(() => {
    getJSON(`/projects/${id}`).then(setProject).catch((e) => setError(String(e)));
  }, [id]);
  useEffect(refresh, [refresh]);

  useEffect(() => {
    if (!project) return;
    const busy = project.scenes.some((s) => s.generations.some((g) => g.status === "queued" || g.status === "running"));
    if (!busy) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [project, refresh]);

  useEffect(() => {
    if (!menu) return;
    const h = () => setMenu(null);
    window.addEventListener("click", h);
    return () => window.removeEventListener("click", h);
  }, [menu]);

  // publish header context (breadcrumb / status / cost / progress)
  const progress = project ? computeProgress(project) : 0;
  const approved = project?.storyboard_approval.status === "approved";
  usePageHeader({
    breadcrumb: [{ label: "Projects", href: "/" }, { label: project?.product.name ?? "…" }],
    badges: project ? [{ tone: approved ? "approved" : "storyboard", label: approved ? "Storyboard approved" : `Storyboard ${project.storyboard_approval.status}`, dot: true }] : [],
    metrics: project ? [
      { label: "Cost", value: `$${(project.cost.total / 100).toFixed(2)}`, accent: "text-accent-2" },
      { label: "Provider", value: "GPT Image" },
    ] : [],
    progress: project ? progress : null,
  });

  if (error && !project) return <div className="p-8 text-sm text-danger">{error}</div>;
  if (!project) return <EditorSkeleton />;

  const scene = project.scenes[selected];
  const sorted = [...project.scenes].sort((a, b) => a.order - b.order);

  async function act(fn: () => Promise<unknown>) {
    setError("");
    try { await fn(); refresh(); } catch (e) { setError(String(e)); }
  }
  function patchScene(updates: Record<string, unknown>) {
    return act(() => postJSON(`/projects/${project!.project_id}/scenes/${scene.scene_id}`, updates));
  }
  async function reorder(from: number, to: number) {
    if (from === to) return;
    const ids = project!.scenes.map((s) => s.scene_id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    await act(async () => {
      for (let i = 0; i < ids.length; i++) {
        const s = project!.scenes.find((x) => x.scene_id === ids[i])!;
        if (s.order !== i) await postJSON(`/projects/${project!.project_id}/scenes/${ids[i]}`, { order: i });
      }
    });
    setSelected(to);
  }
  const pid = project.project_id;
  const genImage = (s: Scene) => act(() => postJSON(`/projects/${pid}/scenes/${s.scene_id}/generate-image`));
  const genVideo = (s: Scene) => act(() => postJSON(`/projects/${pid}/scenes/${s.scene_id}/generate-video`));
  const approveImage = (s: Scene, gid: string) => act(() => postJSON(`/projects/${pid}/scenes/${s.scene_id}/select-image`, { generation_id: gid }));

  return (
    <div className="flex h-full flex-col">
      {/* action toolbar */}
      <div className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2">
        <p className="hidden min-w-0 flex-1 truncate text-xs text-muted md:block">
          <Icon name="sparkles" size={12} className="mr-1 inline text-accent-2" />
          {project.strategy.hook}
        </p>
        <div className="ml-auto flex items-center gap-2">
          {!approved ? (
            <Button size="sm" variant="outline" onClick={() => act(() => postJSON(`/projects/${pid}/storyboard/approve`))}>
              <Icon name="check" size={14} /> Approve storyboard
            </Button>
          ) : (
            <Button size="sm" variant="ai" onClick={() => act(() => postJSON(`/projects/${pid}/generate-images`))}>
              <Icon name="sparkles" size={14} /> Generate all scenes
            </Button>
          )}
          {project.scenes.every((s) => s.selected_image) && !project.final_render && (
            <Button size="sm" variant="accent" onClick={() => act(() => postJSON(`/projects/${pid}/produce`))}>
              <Icon name="film" size={14} /> Produce ad
            </Button>
          )}
        </div>
      </div>

      {project.product.processing_warnings?.length > 0 && (
        <div className="border-b border-warn/30 bg-warn-soft px-4 py-1.5 text-xs text-warn">
          {project.product.processing_warnings.map((w, i) => (
            <p key={i}>⚠ {w.code}: {w.message} (original photo kept as reference)</p>
          ))}
        </div>
      )}

      {/* mobile tabs */}
      <div className="flex border-b border-line bg-surface md:hidden">
        {TABS.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={cn("flex-1 border-b-2 py-2 text-xs font-medium",
              tab === key ? "border-accent text-ink" : "border-transparent text-muted")}>
            {label}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* scene rail */}
        <aside className={cn(
          "w-full shrink-0 space-y-1.5 overflow-y-auto border-r border-line bg-surface-2 p-2.5 md:block md:w-64",
          tab !== "scenes" && "hidden md:block"
        )}>
          <div className="flex items-center justify-between px-1 pb-1">
            <p className="text-overline">Scenes · {sorted.length}</p>
            <span className="text-caption">{Math.round(progress)}%</span>
          </div>
          {sorted.map((s) => {
            const idx = project.scenes.indexOf(s);
            const t = thumbGen(s);
            const st = sceneStatus(s);
            const active = idx === selected;
            const gcount = s.generations.length;
            const qc = s.generations.some((g) => g.kind === "video" && g.status === "qc_rejected");
            return (
              <div
                key={s.scene_id}
                draggable
                onDragStart={() => setDragFrom(sorted.indexOf(s))}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => dragFrom !== null && reorder(dragFrom, sorted.indexOf(s))}
                onClick={() => { setSelected(idx); setDraft({}); setTab("preview"); }}
                onContextMenu={(e) => { e.preventDefault(); setSelected(idx); setMenu({ x: e.clientX, y: e.clientY, idx }); }}
                className={cn(
                  "group flex cursor-pointer gap-2.5 rounded-lg border p-2 transition-all",
                  active
                    ? "border-accent bg-accent-soft/60 ring-1 ring-accent shadow-sm"
                    : "border-line bg-surface hover:border-line-strong"
                )}
              >
                <div className="relative h-16 w-11 shrink-0 overflow-hidden rounded-md bg-surface-2">
                  {t?.asset ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img src={assetUrl(pid, t.asset.asset_id)} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center text-muted-2">
                      <Icon name="image" size={16} />
                    </div>
                  )}
                  <span className="absolute left-0 top-0 rounded-br-md bg-black/60 px-1 text-[9px] font-semibold text-white">
                    {s.order + 1}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-xs font-semibold">Scene {s.order + 1}</span>
                    <span className="text-[10px] text-muted">{s.duration_s}s</span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] leading-tight text-muted">{s.action}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <Badge tone={qc ? "qc_rejected" : st} dot size="sm">{STATUS_LABEL[st] ?? st}</Badge>
                    {gcount > 0 && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] text-muted" title="generations">
                        <Icon name="history" size={10} /> {gcount}
                      </span>
                    )}
                    {sceneCost(s) > 0 && <span className="text-[10px] text-muted">${(sceneCost(s) / 100).toFixed(2)}</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </aside>

        {/* preview */}
        <PreviewPane
          key={scene.scene_id}
          project={project}
          scene={scene}
          onGenImage={() => genImage(scene)}
          onGenVideo={() => genVideo(scene)}
          onApproveImage={(gid) => approveImage(scene, gid)}
          onSelectVideo={(gid) => act(() => postJSON(`/projects/${pid}/scenes/${scene.scene_id}/select-video`, { generation_id: gid }))}
          visible={tab === "preview"}
          progress={progress}
          error={error}
        />

        {/* intent */}
        <aside className={cn(
          "w-full shrink-0 space-y-4 overflow-y-auto border-l border-line bg-surface p-4 md:block md:w-80",
          tab !== "intent" && "hidden md:block"
        )}>
          <div>
            <h2 className="text-h3">Scene {scene.order + 1} intent</h2>
            <p className="text-caption">Edits snapshot automatically.</p>
          </div>

          {INTENT_GROUPS.map((group) => (
            <div key={group.title} className="space-y-2.5">
              <p className="text-overline">{group.title}</p>
              {group.fields.map(([field, label]) => (
                <label key={field} className="block">
                  <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
                  <textarea
                    rows={2}
                    value={draft[field] ?? (scene[field as keyof Scene] as string) ?? ""}
                    onChange={(e) => setDraft({ ...draft, [field]: e.target.value })}
                    onBlur={() => {
                      const v = draft[field];
                      if (v !== undefined && v !== ((scene[field as keyof Scene] as string) ?? "")) patchScene({ [field]: v });
                    }}
                    className="focus-ring w-full resize-none rounded-lg border border-line bg-bg p-2 text-xs"
                  />
                </label>
              ))}
            </div>
          ))}

          <div className="space-y-2.5">
            <p className="text-overline">Timing</p>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-muted">Duration (seconds)</span>
              <input
                type="number" step="0.5" min="0.5" max="8"
                defaultValue={scene.duration_s} key={scene.scene_id}
                onBlur={(e) => { const v = parseFloat(e.target.value); if (v !== scene.duration_s) patchScene({ duration_s: v }); }}
                className="focus-ring w-full rounded-lg border border-line bg-bg p-2 text-xs"
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-muted">Caption style</span>
                <select value={scene.caption_style} onChange={(e) => patchScene({ caption_style: e.target.value })}
                  className="focus-ring w-full rounded-lg border border-line bg-bg p-2 text-xs">
                  {["bounce", "highlight", "plain"].map((v) => <option key={v}>{v}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-muted">Transition</span>
                <select value={scene.transition_out} onChange={(e) => patchScene({ transition_out: e.target.value })}
                  className="focus-ring w-full rounded-lg border border-line bg-bg p-2 text-xs">
                  {["cut", "fade", "whip"].map((v) => <option key={v}>{v}</option>)}
                </select>
              </label>
            </div>
          </div>

          <p className="rounded-lg bg-surface-2 p-2.5 text-[11px] leading-relaxed text-muted">
            Existing generations get a <span className="font-medium text-orange-500">stale</span> badge and an
            approved storyboard reverts to draft when intent changes.
          </p>
        </aside>
      </div>

      {/* context menu */}
      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y}
          scene={project.scenes[menu.idx]}
          onRegenerate={() => genImage(project.scenes[menu.idx])}
          onGenVideo={() => genVideo(project.scenes[menu.idx])}
          onApprove={() => {
            const t = thumbGen(project.scenes[menu.idx]);
            if (t?.status === "succeeded") approveImage(project.scenes[menu.idx], t.generation_id);
          }}
        />
      )}
    </div>
  );
}

/* ----------------------------- preview pane ----------------------------- */

function PreviewPane({
  project, scene, onGenImage, onGenVideo, onApproveImage, onSelectVideo, visible, progress, error,
}: {
  project: Project; scene: Scene;
  onGenImage: () => void; onGenVideo: () => void;
  onApproveImage: (gid: string) => void; onSelectVideo: (gid: string) => void;
  visible: boolean; progress: number; error: string;
}) {
  const pid = project.project_id;
  const [zoom, setZoom] = useState(1);
  const [compare, setCompare] = useState(false);
  const [showHistory, setShowHistory] = useState(true);
  const wrapRef = useRef<HTMLDivElement>(null);

  const thumb = thumbGen(scene);
  const vid = selectedVideoGen(scene);
  const busy = scene.generations.some((g) => g.status === "queued" || g.status === "running");
  const imageGens = scene.generations.filter((g) => g.kind !== "video" && g.status === "succeeded" && g.asset);
  const canCompare = imageGens.length >= 2 && !vid;
  const currentAsset = vid?.asset ?? thumb?.asset ?? null;

  function fullscreen() {
    wrapRef.current?.requestFullscreen?.().catch(() => {});
  }

  const hasMedia = Boolean(currentAsset);

  return (
    <section className={cn("flex min-w-0 flex-1 flex-col bg-bg", visible ? "flex" : "hidden md:flex")}>
      {/* preview toolbar */}
      <div className="flex items-center gap-1 border-b border-line bg-surface px-3 py-1.5">
        <span className="mr-1 text-xs font-medium text-muted">Scene {scene.order + 1}</span>
        <div className="ml-auto flex items-center gap-0.5">
          <ToolBtn icon="zoomOut" label="Zoom out" disabled={!hasMedia || zoom <= 1} onClick={() => setZoom((z) => Math.max(1, +(z - 0.25).toFixed(2)))} />
          <span className="w-9 text-center text-[11px] tabular-nums text-muted">{Math.round(zoom * 100)}%</span>
          <ToolBtn icon="zoomIn" label="Zoom in" disabled={!hasMedia || zoom >= 2.5} onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.25).toFixed(2)))} />
          <span className="mx-1 h-4 w-px bg-line" />
          <ToolBtn icon="compare" label="Compare" active={compare} disabled={!canCompare} onClick={() => setCompare((c) => !c)} />
          <ToolBtn icon="history" label="History" active={showHistory} onClick={() => setShowHistory((s) => !s)} />
          <ToolBtn icon="expand" label="Fullscreen" disabled={!hasMedia} onClick={fullscreen} />
          {currentAsset && (
            <a href={assetUrl(pid, currentAsset.asset_id)} download title="Download"
               className="focus-ring flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-surface-hover hover:text-ink">
              <Icon name="download" size={15} />
            </a>
          )}
        </div>
      </div>

      {/* stage */}
      <div ref={wrapRef} className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-6">
        {busy ? (
          <GeneratingCard project={project} progress={progress} />
        ) : compare && canCompare ? (
          <div className="w-full max-w-[300px]">
            <CompareSlider
              before={assetUrl(pid, imageGens[imageGens.length - 2].asset!.asset_id)}
              after={assetUrl(pid, imageGens[imageGens.length - 1].asset!.asset_id)}
            />
          </div>
        ) : vid?.asset ? (
          <video controls playsInline src={assetUrl(pid, vid.asset.asset_id)}
            style={{ transform: `scale(${zoom})` }}
            className="max-h-full rounded-xl bg-black shadow-lg transition-transform" />
        ) : thumb?.asset ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img src={assetUrl(pid, thumb.asset.asset_id)} alt=""
            style={{ transform: `scale(${zoom})` }}
            className="max-h-full rounded-xl shadow-lg transition-transform" />
        ) : (
          <div className="w-full max-w-sm">
            <div className="flex aspect-[9/16] max-h-[55vh] flex-col items-center justify-center rounded-xl border border-dashed border-line bg-surface">
              <EmptyState
                icon={<Icon name="image" size={22} />}
                title="No image generated yet"
                description="Generate an image to preview this scene."
                action={<Button variant="ai" onClick={onGenImage} disabled={scene.generation_attempts >= 3}>
                  <Icon name="sparkles" size={15} /> Generate image
                </Button>}
              />
            </div>
          </div>
        )}
      </div>

      {/* final render + actions */}
      {project.final_render && <FinalRenderBar project={project} />}

      {/* action bar */}
      <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface px-3 py-2">
        <Button size="sm" variant="ai" onClick={onGenImage} disabled={scene.generation_attempts >= 3}>
          <Icon name="sparkles" size={14} />
          {scene.generations.length ? "Regenerate" : "Generate"} <span className="opacity-70">{scene.generation_attempts}/3</span>
        </Button>
        {thumb && thumb.generation_id !== scene.selected_image && thumb.status === "succeeded" && (
          <Button size="sm" variant="success" onClick={() => onApproveImage(thumb.generation_id)}>
            <Icon name="check" size={14} /> Approve image
          </Button>
        )}
        {scene.selected_image && (
          <Button size="sm" variant="outline" onClick={onGenVideo} disabled={videoAttempts(scene) >= 3}>
            <Icon name="video" size={14} />
            {videoAttempts(scene) ? "Regen video" : "Generate video"} <span className="opacity-70">{videoAttempts(scene)}/3</span>
          </Button>
        )}
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>

      {/* history strip */}
      {showHistory && scene.generations.length > 0 && (
        <div className="border-t border-line bg-surface-2 px-3 py-2">
          <p className="mb-1.5 text-overline">History</p>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {scene.generations.map((g, gi) => (
              <div key={g.generation_id} className="w-16 shrink-0 text-center" title={g.qc_notes ?? undefined}>
                <div className="relative">
                  {g.kind === "video" && g.asset ? (
                    <video src={assetUrl(pid, g.asset.asset_id)} muted playsInline
                      onClick={() => g.status === "succeeded" && onSelectVideo(g.generation_id)}
                      className={cn("aspect-[9/16] w-full cursor-pointer rounded-md bg-black object-cover",
                        g.generation_id === scene.selected_video && "ring-2 ring-info")} />
                  ) : g.asset ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img src={assetUrl(pid, g.asset.asset_id)} alt=""
                      onClick={() => onApproveImage(g.generation_id)}
                      className={cn("aspect-[9/16] w-full cursor-pointer rounded-md object-cover",
                        g.generation_id === scene.selected_image && "ring-2 ring-success")} />
                  ) : (
                    <div className="flex aspect-[9/16] items-center justify-center rounded-md bg-line/40 text-[9px] text-muted">{g.status}</div>
                  )}
                  <span className="absolute right-0.5 top-0.5 rounded bg-black/60 px-1 text-[8px] text-white">
                    {g.kind === "video" ? "V" : "I"}{gi + 1}
                  </span>
                </div>
                <div className="mt-0.5 flex justify-center">
                  <Badge tone={g.stale ? "stale" : g.status} size="sm">{g.stale ? "stale" : g.status}</Badge>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function ToolBtn({ icon, label, onClick, active, disabled }: {
  icon: string; label: string; onClick: () => void; active?: boolean; disabled?: boolean;
}) {
  return (
    <button onClick={onClick} disabled={disabled} title={label}
      className={cn("focus-ring flex h-8 w-8 items-center justify-center rounded-lg transition-colors disabled:opacity-40",
        active ? "bg-accent-soft text-accent-2" : "text-muted hover:bg-surface-hover hover:text-ink")}>
      <Icon name={icon} size={15} />
    </button>
  );
}

function GeneratingCard({ project, progress }: { project: Project; progress: number }) {
  const steps: Step[] = pipelineSteps(project);
  return (
    <div className="w-full max-w-xs animate-fade-in space-y-4">
      <div className="flex aspect-[9/16] max-h-[45vh] flex-col items-center justify-center rounded-xl border border-line bg-surface">
        <div className="skeleton h-full w-full rounded-xl" />
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-h3">Generating…</p>
          <span className="text-xs text-muted">{Math.round(progress)}%</span>
        </div>
        <Stepper steps={steps} />
      </div>
    </div>
  );
}

function FinalRenderBar({ project }: { project: Project }) {
  return (
    <div className="flex items-center gap-3 border-t border-accent/30 bg-accent-soft/40 px-3 py-2">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-ink">
        <Icon name="film" size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold">Final ad ready</p>
        <p className="text-[11px] text-muted">Spent ${(project.cost.total / 100).toFixed(2)} on this video</p>
      </div>
      <a href={assetUrl(project.project_id, project.final_render!.asset_id)} download>
        <Button size="sm" variant="accent"><Icon name="download" size={14} /> Download MP4</Button>
      </a>
    </div>
  );
}

function ContextMenu({ x, y, scene, onRegenerate, onGenVideo, onApprove }: {
  x: number; y: number; scene: Scene;
  onRegenerate: () => void; onGenVideo: () => void; onApprove: () => void;
}) {
  const t = thumbGen(scene);
  const canApprove = t?.status === "succeeded" && t.generation_id !== scene.selected_image;
  const item = "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-ink-2 hover:bg-surface-hover disabled:opacity-40 disabled:hover:bg-transparent";
  return (
    <div style={{ left: x, top: y }} className="fixed z-50 w-52 animate-fade-in rounded-lg border border-line bg-surface p-1.5 shadow-lg"
      onClick={(e) => e.stopPropagation()}>
      <button className={item} onClick={onRegenerate} disabled={scene.generation_attempts >= 3}>
        <Icon name="refresh" size={14} /> Regenerate image
      </button>
      <button className={item} onClick={onApprove} disabled={!canApprove}>
        <Icon name="check" size={14} /> Approve image
      </button>
      <button className={item} onClick={onGenVideo} disabled={!scene.selected_image || videoAttempts(scene) >= 3}>
        <Icon name="video" size={14} /> Generate video
      </button>
      <div className="my-1 h-px bg-line" />
      <button className={item} disabled title="Coming with backend v4">
        <Icon name="copy" size={14} /> Duplicate <span className="ml-auto text-[9px] uppercase text-muted">soon</span>
      </button>
      <button className={item} disabled title="Coming with backend v4">
        <Icon name="trash" size={14} /> Delete <span className="ml-auto text-[9px] uppercase text-muted">soon</span>
      </button>
    </div>
  );
}

function EditorSkeleton() {
  return (
    <div className="flex h-full">
      <div className="hidden w-64 shrink-0 space-y-2 border-r border-line bg-surface-2 p-2.5 md:block">
        {Array.from({ length: 5 }).map((_, i) => <div key={i} className="skeleton h-20 rounded-lg" />)}
      </div>
      <div className="flex flex-1 items-center justify-center">
        <div className="skeleton aspect-[9/16] h-[50vh] rounded-xl" />
      </div>
      <div className="hidden w-80 shrink-0 space-y-3 border-l border-line bg-surface p-4 md:block">
        {Array.from({ length: 6 }).map((_, i) => <div key={i} className="skeleton h-10 rounded-lg" />)}
      </div>
    </div>
  );
}

/* --------------------------- progress helpers --------------------------- */

function computeProgress(p: Project): number {
  const n = p.scenes.length || 1;
  const imgs = p.scenes.filter((s) => s.selected_image).length / n;
  const vids = p.scenes.filter((s) => s.selected_video).length / n;
  const storyboard = p.storyboard_approval.status === "approved" ? 1 : 0.5;
  return Math.min(100, storyboard * 15 + imgs * 35 + vids * 35 + (p.final_render ? 15 : 0));
}

function pipelineSteps(p: Project): Step[] {
  const n = p.scenes.length || 1;
  const imgs = p.scenes.filter((s) => s.selected_image).length;
  const vids = p.scenes.filter((s) => s.selected_video).length;
  const busyImg = p.scenes.some((s) => s.generations.some((g) => g.kind !== "video" && (g.status === "queued" || g.status === "running")));
  const busyVid = p.scenes.some((s) => s.generations.some((g) => g.kind === "video" && (g.status === "queued" || g.status === "running")));
  const state = (done: boolean, active: boolean): Step["state"] => done ? "done" : active ? "active" : "pending";
  return [
    { label: "Product analysis", state: "done" },
    { label: "Creative brief", state: "done" },
    { label: "Storyboard", state: p.storyboard_approval.status === "approved" ? "done" : "active" },
    { label: `Images (${imgs}/${n})`, state: state(imgs === n, busyImg || (p.storyboard_approval.status === "approved" && imgs < n)) },
    { label: `Videos (${vids}/${n})`, state: state(vids === n, busyVid) },
    { label: "Final render", state: p.final_render ? "done" : "pending" },
  ];
}
