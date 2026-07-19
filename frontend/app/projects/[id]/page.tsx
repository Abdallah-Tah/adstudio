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
  provider: string;
  model: string;
  status: string;
  stale?: boolean;
  cost_cents: number;
  reference_assets?: string[];
  reference_types?: string[];
  generation_mode?: string | null;
  qc_notes: string | null;
  qc_override?: {
    overridden_by: string;
    overridden_at: string;
    reason: string;
    acknowledgement: string;
  } | null;
  identity_qc?: {
    identity_score: number;
    severe_failure: boolean;
    invented_parts: string[];
    missing_parts: string[];
    notes: string;
  } | null;
  error_code?: string | null;
  error_message?: string | null;
  asset: { asset_id: string } | null;
  created_at: string;
  queued_at?: string | null;
  started_at?: string | null;
  provider_called_at?: string | null;
  provider_completed_at?: string | null;
  provider_job_id?: string | null;
  provider_status?: string | null;
  provider_progress?: number | null;
  provider_submitted_at?: string | null;
  last_provider_check_at?: string | null;
  next_provider_check_at?: string | null;
  last_heartbeat_at?: string | null;
  source_generation_id?: string | null;
  asset_uploaded_at?: string | null;
  finished_at?: string | null;
  attempt_number?: number;
  queue_wait_ms?: number | null;
  provider_latency_ms?: number | null;
  download_latency_ms?: number | null;
  upload_latency_ms?: number | null;
  qc_latency_ms?: number | null;
  total_latency_ms?: number | null;
};

type ProductionJob = {
  production_job_id: string;
  status: string;
  progress_percent: number;
  current_scene_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
};

type BlockingReason = { code: string; scene_id: string | null; message: string };
type ProductionReadiness = {
  ready: boolean;
  blocking_reasons: BlockingReason[];
  scene_summary: { total: number; ready: number; blocked: number };
  estimated_video_cost_cents: number;
  estimated_duration_s: number;
};

const VIDEO_QC_OVERRIDE_ACK = "I understand this video may not accurately match the product.";

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

type AutomationState = {
  mode: "manual" | "auto";
  status: "idle" | "generating_images" | "checking_consistency" | "producing"
        | "completed" | "needs_review" | "failed";
  detail: string;
  updated_at: string | null;
};

type Project = {
  project_id: string;
  product: { name: string; processing_warnings: ProcessingWarning[] };
  strategy: { hook: string; style_id: string };
  brief: { target_duration_s: number };
  scenes: Scene[];
  storyboard_approval: { status: string; approved_at: string | null };
  final_render: { asset_id: string } | null;
  automation?: AutomationState;
  production_job: ProductionJob | null;
  cost: Record<string, number> & { total: number };
  reference_ad?: {
    goal: string;
    dna: { hook_options: string[]; visual_world: string; pacing: string };
  } | null;
};

const AUTOPILOT_ACTIVE = new Set(["generating_images", "checking_consistency", "producing"]);
const AUTOPILOT_LABEL: Record<AutomationState["status"], string> = {
  idle: "Auto-pilot",
  generating_images: "Auto-pilot · generating scene images",
  checking_consistency: "Auto-pilot · checking product identity across scenes",
  producing: "Auto-pilot · producing your video",
  completed: "Auto-pilot · your ad is ready",
  needs_review: "Auto-pilot paused · needs your review",
  failed: "Auto-pilot stopped",
};

/* ------------------------------- helpers -------------------------------- */

function videoAttempts(s: Scene) {
  const billable = new Set([
    "provider_queued", "provider_processing", "downloading", "uploading",
    "qc_running", "succeeded", "qc_rejected", "cancelled", "timed_out",
  ]);
  return s.generations.filter((g) => (
    g.kind === "video" &&
    (Boolean(g.provider_job_id) || g.cost_cents > 0 || billable.has(g.status))
  )).length;
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
function isActive(g: Generation) {
  return [
    "queued", "submitting", "provider_queued", "provider_processing",
    "downloading", "uploading", "qc_running", "running", "retrying",
  ].includes(g.status);
}
function activeImageGen(s: Scene): Generation | undefined {
  return [...s.generations].reverse().find((g) => g.kind !== "video" && isActive(g));
}
function activeVideoGen(s: Scene): Generation | undefined {
  return [...s.generations].reverse().find((g) => g.kind === "video" && isActive(g));
}
function queuePosition(project: Project, generation: Generation): number | null {
  const active = project.scenes
    .flatMap((s) => s.generations)
    .filter((g) => g.kind === generation.kind && isActive(g))
    .sort((a, b) => Date.parse(a.queued_at ?? a.created_at) - Date.parse(b.queued_at ?? b.created_at));
  const idx = active.findIndex((g) => g.generation_id === generation.generation_id);
  return idx >= 0 ? idx + 1 : null;
}
function elapsedSeconds(g: Generation): number {
  const start = g.started_at ?? g.queued_at ?? g.created_at;
  const ms = Date.now() - Date.parse(start);
  return Number.isFinite(ms) ? Math.max(0, Math.round(ms / 1000)) : 0;
}
function formatMs(ms?: number | null): string {
  if (ms == null) return "n/a";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
}
function identityLabel(g: Generation): string {
  if (!g.identity_qc) return "n/a";
  return `${Math.round(g.identity_qc.identity_score * 100)}%`;
}
function qcStatus(g: Generation): string {
  if (g.qc_override) return "Override";
  if (g.status === "qc_rejected") return "Rejected";
  if (!g.identity_qc) return "n/a";
  return g.identity_qc.severe_failure ? "Failed" : "Passed";
}
function generationStep(g: Generation) {
  const labels: Record<string, string> = {
    queued: "Queued",
    submitting: "Submitting to provider",
    provider_queued: "Queued at provider",
    provider_processing: "Provider processing",
    downloading: "Downloading video",
    uploading: "Uploading asset",
    qc_running: "Running QC",
    running: g.kind === "video" ? "Provider processing" : "Calling GPT Image",
    retrying: "Waiting to retry",
  };
  return labels[g.status] ?? "Generating";
}
function failureLabel(g: Generation) {
  const msg = g.error_message ?? g.qc_notes ?? "provider error";
  return `Failed: ${msg}`;
}
function sceneStatus(s: Scene): string {
  if (s.generations.some(isActive)) return "generating";
  if (s.selected_video) return "completed";
  if (!s.selected_video && s.generations.some((g) => g.kind === "video" && g.status === "qc_rejected")) return "qc_failed";
  if (s.selected_image) return "ready";
  return "draft";
}
function isProductionActive(job?: ProductionJob | null) {
  return Boolean(job && [
    "preflight", "queued", "generating_videos", "generating_voiceover",
    "selecting_music", "rendering", "qc_running",
  ].includes(job.status));
}
function parseApiDetail(error: unknown): unknown {
  const text = String(error).replace(/^Error:\s*/, "");
  try {
    const parsed = JSON.parse(text);
    return parsed?.detail ?? parsed;
  } catch {
    return null;
  }
}
function readinessFromError(error: unknown): ProductionReadiness | null {
  const detail = parseApiDetail(error) as Partial<ProductionReadiness> & { error_code?: string } | null;
  if (detail?.error_code !== "PROJECT_NOT_READY") return null;
  return {
    ready: false,
    blocking_reasons: detail.blocking_reasons ?? [],
    scene_summary: detail.scene_summary ?? { total: 0, ready: 0, blocked: 0 },
    estimated_video_cost_cents: detail.estimated_video_cost_cents ?? 0,
    estimated_duration_s: detail.estimated_duration_s ?? 0,
  };
}
function sceneNumbersFromBlockers(readiness: ProductionReadiness | null, project: Project | null, codes: string[]) {
  if (!readiness || !project) return [];
  const ids = [...new Set(readiness.blocking_reasons
    .filter((b) => codes.includes(b.code) && b.scene_id)
    .map((b) => b.scene_id!))];
  return ids
    .map((id) => project.scenes.find((s) => s.scene_id === id)?.order)
    .filter((order): order is number => order != null)
    .sort((a, b) => a - b)
    .map((order) => order + 1);
}
function readinessMessage(readiness: ProductionReadiness | null, project: Project | null) {
  const inconsistent = sceneNumbersFromBlockers(readiness, project, ["PRODUCT_IDENTITY_INCONSISTENT"]);
  if (inconsistent.length > 0) {
    const scenes = inconsistent.length === 1
      ? `Scene ${inconsistent[0]}`
      : `Scenes ${inconsistent.slice(0, -1).join(", ")} and ${inconsistent[inconsistent.length - 1]}`;
    return `Cannot produce ad yet. ${scenes} shows a different-looking product than the other scenes (cross-scene identity check). Regenerate or reselect the image${inconsistent.length === 1 ? "" : "s"} so every scene shows the exact uploaded product.`;
  }
  const nums = sceneNumbersFromBlockers(readiness, project, ["SCENE_QC_FAILED"]);
  const exhausted = sceneNumbersFromBlockers(readiness, project, ["SCENE_VIDEO_ATTEMPTS_EXHAUSTED"]);
  if (nums.length > 0) {
    const scenes = nums.length === 1
      ? `Scene ${nums[0]}`
      : `Scenes ${nums.slice(0, -1).join(", ")} and ${nums[nums.length - 1]}`;
    const qcText = `${scenes} failed product identity QC. Regenerate or select a QC-approved image for ${nums.length === 1 ? "this scene" : "these scenes"}.`;
    if (exhausted.length === 0) return `Cannot produce ad yet. ${qcText}`;
    const cappedScenes = exhausted.length === 1
      ? `Scene ${exhausted[0]}`
      : `Scenes ${exhausted.slice(0, -1).join(", ")} and ${exhausted[exhausted.length - 1]}`;
    return `Cannot produce ad yet. ${qcText} ${cappedScenes} used all video attempts and needs a passing selection, QC override, or retry-budget increase.`;
  }
  if (exhausted.length > 0) {
    const scenes = exhausted.length === 1
      ? `Scene ${exhausted[0]}`
      : `Scenes ${exhausted.slice(0, -1).join(", ")} and ${exhausted[exhausted.length - 1]}`;
    return `Cannot produce ad yet. ${scenes} used all video attempts. Select a passing video, override QC, or increase the paid video retry budget before another attempt.`;
  }
  const first = readiness?.blocking_reasons[0]?.message;
  return first ? `Cannot produce ad yet. ${first}` : "Cannot produce ad yet.";
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
  const [readiness, setReadiness] = useState<ProductionReadiness | null>(null);
  const [readinessOpen, setReadinessOpen] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("preview");
  const [menu, setMenu] = useState<{ x: number; y: number; idx: number } | null>(null);
  const [showHookPack, setShowHookPack] = useState(false);
  const [overrideSceneId, setOverrideSceneId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    Promise.all([
      getJSON(`/projects/${id}`),
      getJSON(`/projects/${id}/production-readiness`),
    ]).then(([p, r]) => {
      setProject(p);
      setReadiness(r);
    }).catch((e) => setError(String(e)));
  }, [id]);
  useEffect(refresh, [refresh]);

  useEffect(() => {
    if (!project) return;
    const busy = project.scenes.some((s) => s.generations.some(isActive)) ||
      isProductionActive(project.production_job) ||
      (project.automation?.mode === "auto" && AUTOPILOT_ACTIVE.has(project.automation.status));
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
    try {
      await fn();
      refresh();
    } catch (e) {
      const blocked = readinessFromError(e);
      if (blocked) {
        setReadiness(blocked);
        setReadinessOpen(true);
        setError(readinessMessage(blocked, project));
      } else {
        const detail = parseApiDetail(e) as { detail?: string; message?: string } | string | null;
        setError(typeof detail === "string" ? detail : detail?.detail ?? detail?.message ?? String(e));
      }
    }
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
  const cancelGeneration = (gid: string) => act(() => postJSON(`/projects/${pid}/generations/${gid}/cancel`));
  const clearStaleGenerations = () => act(() => postJSON(`/projects/${pid}/clear-stale-generations`));
  const blockedCount = readiness?.scene_summary.blocked ?? 0;
  const productionActive = isProductionActive(project.production_job);
  function latestRejectedVideo(sceneId: string): Generation | undefined {
    const blockedScene = project!.scenes.find((s) => s.scene_id === sceneId);
    return [...(blockedScene?.generations ?? [])].reverse()
      .find((g) => g.kind === "video" && g.status === "qc_rejected" && g.asset);
  }
  function overrideRejectedVideo(sceneId: string) {
    const candidate = latestRejectedVideo(sceneId);
    if (!candidate) {
      setError("No QC-rejected video with an uploaded asset is available to override.");
      return;
    }
    setOverrideSceneId(sceneId);
  }
  function confirmRejectedVideoOverride(sceneId: string) {
    const candidate = latestRejectedVideo(sceneId);
    if (!candidate) {
      setOverrideSceneId(null);
      setError("No QC-rejected video with an uploaded asset is available to override.");
      return;
    }
    setOverrideSceneId(null);
    setReadinessOpen(false);
    return act(() => postJSON(
      `/projects/${pid}/scenes/${sceneId}/videos/${candidate.generation_id}/override-qc`,
      {
        acknowledgement: VIDEO_QC_OVERRIDE_ACK,
        reason: "User accepted QC risk from production readiness modal",
      }
    ));
  }
  async function produceAd() {
    setError("");
    try {
      const r: ProductionReadiness = await getJSON(`/projects/${pid}/production-readiness`);
      setReadiness(r);
      if (!r.ready) {
        setReadinessOpen(true);
        setError(readinessMessage(r, project));
        return;
      }
      await postJSON(`/projects/${pid}/produce`);
      refresh();
    } catch (e) {
      const blocked = readinessFromError(e);
      if (blocked) {
        setReadiness(blocked);
        setReadinessOpen(true);
        setError(readinessMessage(blocked, project));
      } else {
        const detail = parseApiDetail(e) as { detail?: string; message?: string } | string | null;
        setError(typeof detail === "string" ? detail : detail?.detail ?? detail?.message ?? String(e));
      }
      refresh();
    }
  }
  function openScene(sceneId: string) {
    const idx = project!.scenes.findIndex((s) => s.scene_id === sceneId);
    if (idx >= 0) {
      setSelected(idx);
      setTab("preview");
      setReadinessOpen(false);
    }
  }
  function regenerateBlockedScenes() {
    const sceneIds = [...new Set((readiness?.blocking_reasons ?? [])
      .filter((b) => b.scene_id && b.code === "SCENE_QC_FAILED")
      .map((b) => b.scene_id!))];
    setReadinessOpen(false);
    return act(async () => {
      for (const sceneId of sceneIds) {
        const blockedScene = project!.scenes.find((s) => s.scene_id === sceneId);
        const path = blockedScene?.selected_image
          ? `/projects/${pid}/scenes/${sceneId}/generate-video`
          : `/projects/${pid}/scenes/${sceneId}/generate-image`;
        await postJSON(path);
      }
    });
  }

  return (
    <div className="flex h-full flex-col">
      {/* action toolbar */}
      <div className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2">
        <p className="hidden min-w-0 flex-1 truncate text-xs text-muted md:block">
          <Icon name="sparkles" size={12} className="mr-1 inline text-accent-2" />
          {project.strategy.hook}
        </p>
        <div className="ml-auto flex items-center gap-2">
          {project.reference_ad?.dna.hook_options.length ? (
            <Button size="sm" variant="outline" onClick={() => setShowHookPack((open) => !open)}>
              <Icon name="copy" size={14} /> {showHookPack ? "Hide hooks" : "Hook pack"}
            </Button>
          ) : null}
          {!approved ? (
            <Button size="sm" variant="outline" onClick={() => act(() => postJSON(`/projects/${pid}/storyboard/approve`))}>
              <Icon name="check" size={14} /> Approve storyboard
            </Button>
          ) : (
            <Button size="sm" variant="ai"
              disabled={project.scenes.some((s) => s.generations.some(isActive))}
              onClick={() => act(() => postJSON(`/projects/${pid}/generate-images`))}>
              <Icon name="sparkles" size={14} /> Generate all scenes
            </Button>
          )}
          {!project.final_render && productionActive && (
            <Button size="sm" variant="accent" onClick={() => setReadinessOpen(true)}>
              <Icon name="film" size={14} /> Producing ad · {Math.round(project.production_job?.progress_percent ?? 0)}%
            </Button>
          )}
          {!project.final_render && !productionActive && readiness === null && (
            <Button size="sm" variant="outline" disabled>
              <Icon name="film" size={14} /> Checking readiness
            </Button>
          )}
          {!project.final_render && !productionActive && readiness !== null && blockedCount > 0 && (
            <Button size="sm" variant="outline" onClick={() => setReadinessOpen(true)}
              title={(readiness?.blocking_reasons ?? []).map((b) => b.message).join(" ")}>
              <Icon name="alertTriangle" size={14} /> Resolve {blockedCount} blocked scenes
            </Button>
          )}
          {!project.final_render && !productionActive && readiness !== null && blockedCount === 0 && (
            <Button size="sm" variant="accent" onClick={produceAd}>
              <Icon name="film" size={14} /> Produce ad
            </Button>
          )}
        </div>
      </div>
      {showHookPack && project.reference_ad && (
        <div className="border-b border-line bg-accent-soft/20 px-4 py-3">
          <div className="mb-2 flex items-center gap-2">
            <Icon name="film" size={14} className="text-accent-2" />
            <p className="text-xs font-medium">Reference-ad hook pack</p>
            <span className="text-xs text-muted">{project.reference_ad.dna.pacing} · {project.reference_ad.dna.visual_world}</span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {project.reference_ad.dna.hook_options.map((hook, index) => (
              <button key={hook} type="button" onClick={() => navigator.clipboard.writeText(hook)}
                className="rounded-lg border border-line bg-surface px-3 py-2 text-left text-xs text-ink-2 hover:border-accent"
                title="Copy hook">
                <span className="mr-1 text-muted">{index + 1}.</span>{hook}
              </button>
            ))}
          </div>
        </div>
      )}

      {project.automation && (project.automation.mode === "auto" ||
        ["needs_review", "failed", "completed"].includes(project.automation.status)) &&
        project.automation.status !== "idle" && !project.final_render && (
        <div className={cn(
          "flex items-start gap-3 border-b px-4 py-2.5 text-sm",
          project.automation.status === "needs_review" ? "border-warn/30 bg-warn/10" :
          project.automation.status === "failed" ? "border-danger/30 bg-danger/10" :
          "border-accent/20 bg-accent-soft/40"
        )}>
          <span className={cn(
            "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-white",
            project.automation.status === "needs_review" ? "bg-warn" :
            project.automation.status === "failed" ? "bg-danger" :
            "bg-gradient-to-br from-violet-600 to-fuchsia-600"
          )}>
            <Icon name={project.automation.status === "needs_review" || project.automation.status === "failed"
              ? "alertTriangle" : "sparkles"} size={13} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="font-medium">
              {AUTOPILOT_LABEL[project.automation.status]}
              {AUTOPILOT_ACTIVE.has(project.automation.status) && (
                <span className="ml-2 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent align-middle" />
              )}
            </p>
            {project.automation.detail && (
              <p className="truncate text-xs text-secondary" title={project.automation.detail}>
                {project.automation.detail}
              </p>
            )}
          </div>
          {AUTOPILOT_ACTIVE.has(project.automation.status) ? (
            <Button size="sm" variant="outline"
              onClick={() => act(() => postJSON(`/projects/${pid}/autopilot`, { action: "stop" }))}>
              <Icon name="pause" size={13} /> Pause
            </Button>
          ) : project.automation.status !== "completed" && (
            <Button size="sm" variant="accent"
              onClick={() => act(() => postJSON(`/projects/${pid}/autopilot`, { action: "start" }))}>
              <Icon name="play" size={13} /> Resume auto-pilot
            </Button>
          )}
        </div>
      )}

      {readinessOpen && (
        <ProductionReadinessModal
          readiness={readiness}
          project={project}
          job={project.production_job}
          onClose={() => setReadinessOpen(false)}
          onOpenScene={openScene}
          onRegenerateFailed={regenerateBlockedScenes}
          onOverrideScene={overrideRejectedVideo}
        />
      )}
      {overrideSceneId && (
        <QCOverrideModal
          onClose={() => setOverrideSceneId(null)}
          onConfirm={() => confirmRejectedVideoOverride(overrideSceneId)}
        />
      )}

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
          onCancelGeneration={cancelGeneration}
          onClearStale={clearStaleGenerations}
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
  project, scene, onGenImage, onGenVideo, onApproveImage, onSelectVideo,
  onCancelGeneration, onClearStale, visible, progress, error,
}: {
  project: Project; scene: Scene;
  onGenImage: () => void; onGenVideo: () => void;
  onApproveImage: (gid: string) => void; onSelectVideo: (gid: string) => void;
  onCancelGeneration: (gid: string) => void; onClearStale: () => void;
  visible: boolean; progress: number; error: string;
}) {
  const pid = project.project_id;
  const [zoom, setZoom] = useState(1);
  const [compare, setCompare] = useState(false);
  const [showHistory, setShowHistory] = useState(true);
  const wrapRef = useRef<HTMLDivElement>(null);

  const thumb = thumbGen(scene);
  const vid = selectedVideoGen(scene);
  const busy = scene.generations.some(isActive);
  const imageGens = scene.generations.filter((g) => g.kind !== "video" && g.status === "succeeded" && g.asset);
  const canCompare = imageGens.length >= 2 && !vid;
  const currentAsset = vid?.asset ?? thumb?.asset ?? null;
  const currentGeneration = vid ?? thumb ?? null;
  // most recent terminal failure (surfaced when there's nothing to preview)
  const lastFailed = [...scene.generations].reverse()
    .find((g) => g.status === "failed" || g.status === "qc_rejected");

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
          <GeneratingCard
            project={project}
            scene={scene}
            progress={progress}
            onCancelGeneration={onCancelGeneration}
            onClearStale={onClearStale}
          />
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
        ) : lastFailed ? (
          <div className="w-full max-w-sm">
            <div className="flex aspect-[9/16] max-h-[55vh] flex-col items-center justify-center rounded-xl border border-dashed border-danger/40 bg-danger-soft/40">
              <EmptyState
                icon={<span className="text-danger"><Icon name="refresh" size={22} /></span>}
                title={lastFailed.status === "qc_rejected" ? "QC rejected this generation" : failureLabel(lastFailed)}
                description={lastFailed.error_code ?? "The provider returned an error."}
                action={
                  scene.generation_attempts < 3 ? (
                    <Button variant="ai" onClick={onGenImage} disabled={Boolean(activeImageGen(scene))}>
                      <Icon name="refresh" size={15} /> Retry ({scene.generation_attempts}/3)
                    </Button>
                  ) : (
                    <span className="text-xs text-muted">Attempt cap reached (3/3).</span>
                  )
                }
              />
              <GenerationDetails generation={lastFailed} />
            </div>
          </div>
        ) : (
          <div className="w-full max-w-sm">
            <div className="flex aspect-[9/16] max-h-[55vh] flex-col items-center justify-center rounded-xl border border-dashed border-line bg-surface">
              <EmptyState
                icon={<Icon name="image" size={22} />}
                title="No image generated yet"
                description="Generate an image to preview this scene."
                action={<Button variant="ai" onClick={onGenImage} disabled={scene.generation_attempts >= 3 || Boolean(activeImageGen(scene))}>
                  <Icon name="sparkles" size={15} /> Generate image
                </Button>}
              />
            </div>
          </div>
        )}
      </div>

      {!busy && currentGeneration && (
        <div className="border-t border-line bg-surface px-3 py-2">
          <GenerationDetails generation={currentGeneration} compact />
        </div>
      )}

      {/* final render + actions */}
      {project.final_render && <FinalRenderBar project={project} />}

      {/* action bar */}
      <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface px-3 py-2">
        <Button size="sm" variant="ai" onClick={onGenImage} disabled={scene.generation_attempts >= 3 || Boolean(activeImageGen(scene))}>
          <Icon name="sparkles" size={14} />
          {scene.generations.length ? "Regenerate" : "Generate"} <span className="opacity-70">{scene.generation_attempts}/3</span>
        </Button>
        {thumb && thumb.generation_id !== scene.selected_image && thumb.status === "succeeded" && (
          <Button size="sm" variant="success" onClick={() => onApproveImage(thumb.generation_id)}>
            <Icon name="check" size={14} /> Approve image
          </Button>
        )}
        {scene.selected_image && (
          <Button size="sm" variant="outline" onClick={onGenVideo} disabled={videoAttempts(scene) >= 3 || Boolean(activeVideoGen(scene))}>
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
                    <div className="flex aspect-[9/16] items-center justify-center rounded-md bg-line/40 px-1 text-center text-[9px] text-muted">{g.status}</div>
                  )}
                  <span className="absolute right-0.5 top-0.5 rounded bg-black/60 px-1 text-[8px] text-white">
                    {g.kind === "video" ? "V" : "I"}{gi + 1}
                  </span>
                </div>
                <div className="mt-0.5 flex justify-center">
                  <Badge tone={g.stale ? "stale" : g.status} size="sm">{g.stale ? "stale" : g.status}</Badge>
                </div>
                {g.identity_qc && (
                  <p className="mt-0.5 text-[10px] text-muted">ID {identityLabel(g)}</p>
                )}
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

function GeneratingCard({
  project, scene, progress, onCancelGeneration, onClearStale,
}: {
  project: Project; scene: Scene; progress: number;
  onCancelGeneration: (gid: string) => void; onClearStale: () => void;
}) {
  const steps: Step[] = pipelineSteps(project);
  const generation = activeImageGen(scene) ?? activeVideoGen(scene) ?? scene.generations.find(isActive);
  const elapsed = generation ? elapsedSeconds(generation) : 0;
  const position = generation ? queuePosition(project, generation) : null;
  const activeCount = generation
    ? project.scenes.flatMap((s) => s.generations).filter((g) => g.kind === generation.kind && isActive(g)).length
    : 0;
  return (
    <div className="w-full max-w-xs animate-fade-in space-y-4">
      <div className="flex aspect-[9/16] max-h-[45vh] flex-col items-center justify-center rounded-xl border border-line bg-surface">
        <div className="skeleton h-full w-full rounded-xl" />
      </div>
      <div className="rounded-xl border border-line bg-surface p-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-h3">{generation ? generationStep(generation) : "Generating"}</p>
          <span className="text-xs text-muted">{Math.round(progress)}%</span>
        </div>
        {generation && (
          <div className="mb-3 grid grid-cols-3 gap-2 text-[11px] text-muted">
            <span>Elapsed: {elapsed}s</span>
            <span>Attempt: {generation.attempt_number ?? 1} of 3</span>
            <span>Queue: {position ?? 1}/{Math.max(activeCount, 1)}</span>
          </div>
        )}
        {generation?.kind === "video" && (
          <div className="mb-3 space-y-1 text-[11px] text-muted">
            <p>Provider: {generation.provider_status ?? generation.status}</p>
            {generation.provider_progress != null && (
              <p>Progress: {Math.round(generation.provider_progress * 100)}%</p>
            )}
            {generation.source_generation_id && <p>Source image: {generation.source_generation_id}</p>}
          </div>
        )}
        {elapsed >= 90 && (
          <p className="mb-3 rounded-md bg-warn-soft px-2 py-1 text-xs text-warn">
            This is taking longer than expected.
          </p>
        )}
        <Stepper steps={steps} />
        {generation && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => onCancelGeneration(generation.generation_id)}>
              Cancel generation
            </Button>
            {elapsed >= 300 && (
              <Button size="sm" variant="outline" onClick={onClearStale}>
                Clear stale queued job
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function GenerationDetails({ generation, compact = false }: { generation: Generation; compact?: boolean }) {
  const rows = [
    ["Provider", generation.provider],
    ["Model", generation.model],
    ["Status", generation.status],
    ["Provider state", generation.provider_status ?? "n/a"],
    ["Source image", generation.source_generation_id ?? "n/a"],
    ["Mode", generation.generation_mode ?? "n/a"],
    ["Identity", identityLabel(generation)],
    ["QC", qcStatus(generation)],
    ["QC override", generation.qc_override ? `${generation.qc_override.overridden_by} at ${generation.qc_override.overridden_at}` : "n/a"],
    ["References", generation.reference_types?.length ? generation.reference_types.join(", ") : (generation.reference_assets?.length ? `${generation.reference_assets.length} assets` : "n/a")],
    ["Cost", generation.cost_cents ? `$${(generation.cost_cents / 100).toFixed(2)}` : "$0.00"],
    ["Queued", generation.queued_at ?? generation.created_at],
    ["Started", generation.started_at ?? "not started"],
    ["Queue wait", formatMs(generation.queue_wait_ms)],
    ["Provider latency", formatMs(generation.provider_latency_ms)],
    ["Download", formatMs(generation.download_latency_ms)],
    ["Upload", formatMs(generation.upload_latency_ms)],
    ["QC time", formatMs(generation.qc_latency_ms)],
    ["Elapsed", formatMs(generation.total_latency_ms)],
    ["Attempt", `${generation.attempt_number ?? 1} of 3`],
    ["Error", generation.error_code ?? generation.status],
    ["Message", generation.error_message ?? generation.qc_notes ?? "n/a"],
  ];
  const defects = [
    ...(generation.identity_qc?.missing_parts ?? []),
    ...(generation.identity_qc?.invented_parts ?? []),
  ];
  return (
    <dl className={cn(
      "w-full space-y-1 rounded-md border bg-bg/70 p-2 text-[10px]",
      compact ? "grid gap-x-4 md:grid-cols-2" : "mt-3 max-w-[18rem] border-danger/20"
    )}>
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[6rem_1fr] gap-2">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 truncate text-ink-2" title={v}>{v}</dd>
        </div>
      ))}
      {defects.length > 0 && (
        <div className="md:col-span-2">
          <dt className="text-muted">Defects</dt>
          <dd className="mt-0.5 text-ink-2">{defects.slice(0, 3).join(", ")}</dd>
        </div>
      )}
    </dl>
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

function QCOverrideModal({ onClose, onConfirm }: { onClose: () => void; onConfirm: () => void }) {
  const [typed, setTyped] = useState("");
  const matches = typed === VIDEO_QC_OVERRIDE_ACK;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby="qc-override-title">
      <div className="w-full max-w-lg rounded-xl border border-warn/40 bg-surface p-5 shadow-xl">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn">
            <Icon name="alertTriangle" size={18} />
          </span>
          <div>
            <h2 id="qc-override-title" className="text-h3">Override product identity QC</h2>
            <p className="mt-1 text-sm text-muted">This video did not fully match the uploaded product. Only continue if you accept that risk.</p>
          </div>
        </div>
        <label className="mt-4 block">
          <span className="mb-1.5 block text-xs font-medium text-ink">Type this exact acknowledgement</span>
          <code className="block rounded-md bg-surface-2 p-2 text-xs text-ink-2">{VIDEO_QC_OVERRIDE_ACK}</code>
          <textarea value={typed} onChange={(e) => setTyped(e.target.value)} rows={3}
            className="focus-ring mt-2 w-full rounded-lg border border-line bg-surface p-2 text-sm"
            placeholder="Type the acknowledgement exactly" autoFocus />
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <Button size="sm" variant="outline" onClick={onClose}>Cancel</Button>
          <Button size="sm" variant="warn" disabled={!matches} onClick={onConfirm}>
            Override QC
          </Button>
        </div>
      </div>
    </div>
  );
}

function ProductionReadinessModal({
  readiness, project, job, onClose, onOpenScene, onRegenerateFailed, onOverrideScene,
}: {
  readiness: ProductionReadiness | null;
  project: Project;
  job: ProductionJob | null;
  onClose: () => void;
  onOpenScene: (sceneId: string) => void;
  onRegenerateFailed: () => void;
  onOverrideScene: (sceneId: string) => void;
}) {
  const blockers = readiness?.blocking_reasons ?? [];
  const sceneBlockers = blockers.filter((b) => b.scene_id);
  const qcSceneIds = [...new Set(sceneBlockers
    .filter((b) => b.code === "SCENE_QC_FAILED")
    .map((b) => b.scene_id!)
  )];
  const exhaustedSceneIds = [...new Set(sceneBlockers
    .filter((b) => b.code === "SCENE_VIDEO_ATTEMPTS_EXHAUSTED")
    .map((b) => b.scene_id!)
  )];
  const summary = readiness?.scene_summary;
  const title = isProductionActive(job) ? "Production in progress" : "Ad is not ready to produce";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-lg border border-line bg-surface p-4 shadow-xl">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn">
            <Icon name={isProductionActive(job) ? "film" : "alertTriangle"} size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-h3">{title}</h2>
            {isProductionActive(job) ? (
              <p className="mt-1 text-sm text-muted">
                {job?.status.replaceAll("_", " ")} · {Math.round(job?.progress_percent ?? 0)}%
              </p>
            ) : summary ? (
              <div className="mt-1 space-y-1 text-sm text-muted">
                <p>{summary.ready} of {summary.total} scenes are ready.</p>
                <p>{readinessMessage(readiness, project)}</p>
              </div>
            ) : (
              <p className="mt-1 text-sm text-muted">Checking production readiness.</p>
            )}
          </div>
          <button className="focus-ring rounded-md p-1 text-muted hover:bg-surface-hover hover:text-ink"
            onClick={onClose} title="Close">
            <Icon name="chevron" size={16} />
          </button>
        </div>

        {job?.error_message && (
          <div className="mt-3 rounded-md border border-danger/30 bg-danger-soft p-2 text-sm text-danger">
            {job.error_message}
          </div>
        )}

        {blockers.length > 0 && (
          <div className="mt-4">
            <p className="text-overline">Blocking issues</p>
            <div className="mt-2 max-h-64 space-y-2 overflow-auto">
              {blockers.map((b, i) => (
                <div key={`${b.code}-${b.scene_id ?? "project"}-${i}`}
                  className="rounded-md border border-line bg-bg/70 p-2">
                  <div className="flex items-center gap-2">
                    <Badge tone={b.code === "SCENE_QC_FAILED" ? "qc_rejected" : "failed"} size="sm">
                      {b.code.replaceAll("_", " ")}
                    </Badge>
                    <p className="min-w-0 flex-1 text-sm text-ink-2">{b.message}</p>
                  </div>
                  {b.scene_id && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button size="xs" variant="outline"
                        onClick={() => onOpenScene(b.scene_id!)}>
                        Open scene
                      </Button>
                      {b.code === "SCENE_VIDEO_ATTEMPTS_EXHAUSTED" && (
                        <Button size="xs" variant="warn"
                          onClick={() => onOverrideScene(b.scene_id!)}>
                          Override latest failed video
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {readiness && !readiness.ready && (
          <div className="mt-4 rounded-md bg-surface-2 p-3 text-sm text-muted">
            <p>Estimated remaining video cost: ${(readiness.estimated_video_cost_cents / 100).toFixed(2)}</p>
            <p>Estimated duration: {Math.round(readiness.estimated_duration_s)}s</p>
          </div>
        )}

        <div className="mt-4 flex flex-wrap justify-end gap-2">
          {qcSceneIds.length > 0 && (
            <Button size="sm" variant="ai" onClick={onRegenerateFailed}>
              <Icon name="refresh" size={14} /> Regenerate failed scenes
            </Button>
          )}
          {exhaustedSceneIds.length === 1 && (
            <Button size="sm" variant="warn" onClick={() => onOverrideScene(exhaustedSceneIds[0])}>
              <Icon name="check" size={14} /> Override Scene QC
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={onClose}>Close</Button>
        </div>
      </div>
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
  const busyImg = p.scenes.some((s) => s.generations.some((g) => g.kind !== "video" && isActive(g)));
  const busyVid = p.scenes.some((s) => s.generations.some((g) => g.kind === "video" && isActive(g)));
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
