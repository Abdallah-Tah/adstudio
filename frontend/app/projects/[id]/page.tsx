"use client";

import { use, useCallback, useEffect, useState } from "react";
import { assetUrl, getJSON, postJSON } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type Generation = {
  generation_id: string;
  status: string;
  stale?: boolean;
  cost_cents: number;
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
  cost: Record<string, number> & { total: number };
};

const INTENT_FIELDS = [
  ["action", "Action"],
  ["camera", "Camera"],
  ["lighting", "Lighting"],
  ["vo_line", "VO line"],
  ["caption", "Caption"],
] as const;

function sceneStatus(s: Scene): string {
  if (s.selected_image) return "image_ready";
  if (s.generations.some((g) => g.status === "queued" || g.status === "running")) return "running";
  return "draft";
}

function thumbGen(s: Scene): Generation | undefined {
  return (
    s.generations.find((g) => g.generation_id === s.selected_image) ??
    [...s.generations].reverse().find((g) => g.status === "succeeded")
  );
}

export default function Editor({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [project, setProject] = useState<Project | null>(null);
  const [selected, setSelected] = useState(0);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  const refresh = useCallback(() => {
    getJSON(`/projects/${id}`).then(setProject).catch((e) => setError(String(e)));
  }, [id]);

  useEffect(refresh, [refresh]);

  // poll while any generation is in flight
  useEffect(() => {
    if (!project) return;
    const busy = project.scenes.some((s) =>
      s.generations.some((g) => g.status === "queued" || g.status === "running")
    );
    if (!busy) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [project, refresh]);

  if (error) return <main className="p-8 text-red-600">{error}</main>;
  if (!project) return <main className="p-8 text-neutral-500">Loading…</main>;

  const scene = project.scenes[selected];
  const thumb = thumbGen(scene);

  async function act(fn: () => Promise<unknown>) {
    setError("");
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(String(e));
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

  const sorted = [...project.scenes].sort((a, b) => a.order - b.order);

  return (
    <main className="flex h-screen flex-col">
      {/* header + cost ledger, always visible */}
      <header className="flex items-center justify-between border-b border-neutral-200 bg-white px-4 py-2">
        <div>
          <a href="/" className="text-xs text-neutral-400 hover:underline">← projects</a>
          <h1 className="text-sm font-semibold">{project.product.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={project.storyboard_approval.status === "approved" ? "succeeded" : "draft"}>
            storyboard {project.storyboard_approval.status}
          </Badge>
          {project.storyboard_approval.status !== "approved" ? (
            <Button
              variant="outline"
              onClick={() => act(() => postJSON(`/projects/${project.project_id}/storyboard/approve`))}
            >
              Approve storyboard
            </Button>
          ) : (
            <Button
              onClick={() => act(() => postJSON(`/projects/${project.project_id}/generate-images`))}
            >
              Generate all scenes
            </Button>
          )}
        </div>
        <div className="flex gap-3 text-xs text-neutral-600">
          {Object.entries(project.cost)
            .filter(([k, v]) => k !== "total" && v > 0)
            .map(([k, v]) => (
              <span key={k}>{k}: {(v / 100).toFixed(2)}$</span>
            ))}
          <span className="font-semibold">total: {(project.cost.total / 100).toFixed(2)}$</span>
        </div>
      </header>

      {project.product.processing_warnings?.length > 0 && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-xs text-amber-800">
          {project.product.processing_warnings.map((w, i) => (
            <p key={i}>⚠ {w.code}: {w.message} (original photo kept as reference — you can continue or replace it)</p>
          ))}
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        {/* scene rail */}
        <aside className="w-56 space-y-2 overflow-y-auto border-r border-neutral-200 bg-white p-2">
          {sorted.map((s, i) => {
            const t = thumbGen(s);
            return (
              <div
                key={s.scene_id}
                draggable
                onDragStart={() => setDragFrom(i)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => dragFrom !== null && reorder(dragFrom, i)}
                onClick={() => { setSelected(project.scenes.indexOf(s)); setDraft({}); }}
                className={`cursor-pointer rounded-md border p-2 ${
                  s.scene_id === scene.scene_id ? "border-neutral-900" : "border-neutral-200"
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-medium">#{s.order} · {s.duration_s}s</span>
                  <Badge tone={sceneStatus(s)}>{sceneStatus(s)}</Badge>
                </div>
                {t?.asset ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={assetUrl(project.project_id, t.asset.asset_id)} alt="" className="aspect-[9/16] w-full rounded object-cover" />
                ) : (
                  <div className="flex aspect-[9/16] items-center justify-center rounded bg-neutral-100 text-xs text-neutral-400">
                    no image
                  </div>
                )}
                <p className="mt-1 line-clamp-2 text-xs text-neutral-500">{s.action}</p>
              </div>
            );
          })}
        </aside>

        {/* preview pane */}
        <section className="flex flex-1 flex-col items-center justify-center gap-3 overflow-y-auto p-4">
          {thumb?.asset ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img src={assetUrl(project.project_id, thumb.asset.asset_id)} alt="" className="max-h-[70vh] rounded-lg shadow" />
          ) : (
            <div className="flex aspect-[9/16] h-[60vh] items-center justify-center rounded-lg bg-neutral-100 text-neutral-400">
              no image yet
            </div>
          )}
          <div className="flex gap-2">
            <Button
              onClick={() => act(() => postJSON(`/projects/${project.project_id}/scenes/${scene.scene_id}/generate-image`))}
              disabled={scene.generation_attempts >= 3}
            >
              {scene.generations.length ? "Regenerate" : "Generate"} ({scene.generation_attempts}/3)
            </Button>
            {thumb && thumb.generation_id !== scene.selected_image && thumb.status === "succeeded" && (
              <Button
                variant="outline"
                onClick={() => act(() => postJSON(`/projects/${project.project_id}/scenes/${scene.scene_id}/select-image`, { generation_id: thumb.generation_id }))}
              >
                Approve this image
              </Button>
            )}
          </div>

          {/* generation history / compare + select */}
          {scene.generations.length > 0 && (
            <div className="flex max-w-full gap-2 overflow-x-auto">
              {scene.generations.map((g) => (
                <div key={g.generation_id} className="w-24 shrink-0 text-center">
                  {g.asset ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={assetUrl(project.project_id, g.asset.asset_id)}
                      alt=""
                      onClick={() => act(() => postJSON(`/projects/${project.project_id}/scenes/${scene.scene_id}/select-image`, { generation_id: g.generation_id }))}
                      className={`aspect-[9/16] w-full cursor-pointer rounded object-cover ${
                        g.generation_id === scene.selected_image ? "ring-2 ring-emerald-500" : ""
                      }`}
                    />
                  ) : (
                    <div className="flex aspect-[9/16] items-center justify-center rounded bg-neutral-100 text-[10px] text-neutral-400">{g.status}</div>
                  )}
                  <div className="mt-0.5 flex justify-center gap-1">
                    <Badge tone={g.status}>{g.status}</Badge>
                    {g.stale && <Badge tone="stale">stale</Badge>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* intent panel */}
        <aside className="w-80 space-y-3 overflow-y-auto border-l border-neutral-200 bg-white p-4">
          <h2 className="text-sm font-semibold">Scene #{scene.order} intent</h2>
          {INTENT_FIELDS.map(([field, label]) => (
            <label key={field} className="block text-xs">
              <span className="mb-1 block font-medium text-neutral-600">{label}</span>
              <textarea
                rows={2}
                value={draft[field] ?? scene[field] ?? ""}
                onChange={(e) => setDraft({ ...draft, [field]: e.target.value })}
                onBlur={() => {
                  const v = draft[field];
                  if (v !== undefined && v !== (scene[field] ?? "")) patchScene({ [field]: v });
                }}
                className="w-full rounded-md border border-neutral-300 p-2"
              />
            </label>
          ))}
          <label className="block text-xs">
            <span className="mb-1 block font-medium text-neutral-600">Duration (s)</span>
            <input
              type="number" step="0.5" min="0.5" max="8"
              defaultValue={scene.duration_s}
              key={scene.scene_id}
              onBlur={(e) => { const v = parseFloat(e.target.value); if (v !== scene.duration_s) patchScene({ duration_s: v }); }}
              className="w-full rounded-md border border-neutral-300 p-2"
            />
          </label>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <label>
              <span className="mb-1 block font-medium text-neutral-600">Caption style</span>
              <select value={scene.caption_style} onChange={(e) => patchScene({ caption_style: e.target.value })} className="w-full rounded-md border border-neutral-300 p-2">
                {["bounce", "highlight", "plain"].map((v) => <option key={v}>{v}</option>)}
              </select>
            </label>
            <label>
              <span className="mb-1 block font-medium text-neutral-600">Transition</span>
              <select value={scene.transition_out} onChange={(e) => patchScene({ transition_out: e.target.value })} className="w-full rounded-md border border-neutral-300 p-2">
                {["cut", "fade", "whip"].map((v) => <option key={v}>{v}</option>)}
              </select>
            </label>
          </div>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <p className="text-[10px] text-neutral-400">
            Edits snapshot automatically; existing generations get a “stale” badge when intent changes.
          </p>
        </aside>
      </div>
    </main>
  );
}
