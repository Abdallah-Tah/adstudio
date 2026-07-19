"use client";

import { useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Stepper, type Step } from "@/components/ui/stepper";
import { usePageHeader } from "@/components/header-store";
import { cn } from "@/lib/utils";

const STEPS = ["Product", "Creative Brief", "Style", "Review"] as const;

const STYLE_CARDS = [
  { id: "minimal_tech", name: "Minimal Tech", blurb: "Clean whites, negative space, engineered precision.", swatch: ["#fafafa", "#cbd5e1", "#64748b", "#2563eb"] },
  { id: "warm_lifestyle", name: "Warm Lifestyle", blurb: "Golden light, cozy textures, everyday moments.", swatch: ["#fef3c7", "#fbbf24", "#b45309", "#7c2d12"] },
  { id: "bold_energy", name: "Bold Energy", blurb: "High contrast, punchy color, kinetic pacing.", swatch: ["#18181b", "#ef4444", "#facc15", "#f5f5f5"] },
  { id: "studio_luxury", name: "Studio Luxury", blurb: "Deep shadows, rich materials, premium restraint.", swatch: ["#0c0a09", "#44403c", "#a8a29e", "#ca8a04"] },
  { id: "ugc_handheld", name: "UGC Handheld", blurb: "Authentic, casual, shot-on-a-phone credibility.", swatch: ["#e7e5e4", "#a3e635", "#38bdf8", "#f472b6"] },
] as const;

const PIPELINE = [
  { key: "analysis", label: "Product analysis", eta: 9 },
  { key: "brief", label: "Creative brief", eta: 5 },
  { key: "strategy", label: "Strategy", eta: 7 },
  { key: "storyboard", label: "Storyboard", eta: 12 },
] as const;

type Photo = { file: File; url: string };

type PhotoIssue = { code: string; message: string; tip: string };
type PhotoVerdict = {
  filename: string;
  verdict: "good" | "usable" | "replace";
  quality_score: number;
  cutout_ok: boolean;
  issues: PhotoIssue[];
};
type Precheck = {
  ok_to_proceed: boolean;
  summary: string;
  photos: PhotoVerdict[];
  ai_checked: boolean;
};

const VERDICT_UI = {
  good: { label: "Great photo", cls: "bg-success/10 text-success border-success/30" },
  usable: { label: "Usable", cls: "bg-warn/10 text-warn border-warn/30" },
  replace: { label: "Replace", cls: "bg-danger/10 text-danger border-danger/30" },
} as const;

export default function CreateAd() {
  usePageHeader({ breadcrumb: [{ label: "Create Ad" }] });
  const [step, setStep] = useState(0);
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [description, setDescription] = useState("");
  const [descLoading, setDescLoading] = useState(false);
  const [descEdited, setDescEdited] = useState(false);
  const [descError, setDescError] = useState("");
  const [brief, setBrief] = useState({ audience: "", offer: "", cta: "", tone: "" });
  const [style, setStyle] = useState<string>("");
  const [duration, setDuration] = useState(20);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [stageIdx, setStageIdx] = useState(0);
  const [mode, setMode] = useState<"manual" | "auto">("auto");
  const [precheck, setPrecheck] = useState<Precheck | null>(null);
  const [precheckLoading, setPrecheckLoading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const descReq = useRef(0);
  const precheckReq = useRef(0);

  async function runPrecheck(current: Photo[]) {
    if (current.length === 0) { setPrecheck(null); return; }
    const id = ++precheckReq.current;
    setPrecheckLoading(true);
    try {
      const fd = new FormData();
      current.forEach((p) => fd.append("photos", p.file));
      const r = await fetch(`${API}/uploads/precheck`, { method: "POST", credentials: "include", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const result: Precheck = await r.json();
      if (id === precheckReq.current) setPrecheck(result);
    } catch {
      if (id === precheckReq.current) setPrecheck(null); // check unavailable — don't block
    } finally {
      if (id === precheckReq.current) setPrecheckLoading(false);
    }
  }

  function addFiles(list: FileList | File[]) {
    const next = [...photos];
    for (const f of Array.from(list)) {
      if (f.type.startsWith("image/")) next.push({ file: f, url: URL.createObjectURL(f) });
    }
    setPhotos(next.slice(0, 8));
  }

  async function generateDescription(current: Photo[]) {
    if (current.length === 0) return;
    const id = ++descReq.current;
    setDescLoading(true); setDescError("");
    try {
      const fd = new FormData();
      current.slice(0, 4).forEach((p) => fd.append("photos", p.file));
      const r = await fetch(`${API}/describe`, { method: "POST", credentials: "include", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const { description: text } = await r.json();
      if (id === descReq.current && !descEdited) setDescription(text);
    } catch {
      if (id === descReq.current) setDescError("Couldn't auto-describe — write one below.");
    } finally {
      if (id === descReq.current) setDescLoading(false);
    }
  }

  useEffect(() => {
    if (photos.length === 0) { setDescription(""); setDescEdited(false); setPrecheck(null); return; }
    if (!descEdited) generateDescription(photos);
    runPrecheck(photos);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [photos]);

  useEffect(() => {
    if (!running) return;
    if (stageIdx >= PIPELINE.length - 1) return;
    const t = setTimeout(() => setStageIdx((i) => i + 1), PIPELINE[stageIdx].eta * 1000);
    return () => clearTimeout(t);
  }, [running, stageIdx]);

  const photosOk = !precheck || precheck.ok_to_proceed;
  const canNext =
    step === 0 ? photos.length > 0 && !descLoading && !precheckLoading &&
                 photosOk && description.trim().length > 10 :
    step === 1 ? true : step === 2 ? true : true;

  async function submit() {
    setRunning(true); setStageIdx(0); setError("");
    const fd = new FormData();
    photos.forEach((p) => fd.append("photos", p.file));
    fd.append("description", description);
    if (brief.audience) fd.append("audience", brief.audience);
    if (brief.offer) fd.append("offer", brief.offer);
    if (brief.cta) fd.append("cta", brief.cta);
    if (brief.tone) fd.append("tone", brief.tone);
    if (style) fd.append("style", style);
    fd.append("target_duration_s", String(duration));
    fd.append("mode", mode);
    try {
      const r = await fetch(`${API}/projects`, { method: "POST", credentials: "include", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const project = await r.json();
      setStageIdx(PIPELINE.length);
      window.location.href = `/projects/${project.project_id}`;
    } catch (e) { setError(String(e)); setRunning(false); }
  }

  if (running) {
    const steps: Step[] = [
      ...PIPELINE.map((s, i): Step => ({
        label: s.label,
        state: i < stageIdx ? "done" : i === stageIdx ? "active" : "pending",
      })),
      {
        label: "Images · Videos · Render",
        state: "pending",
        hint: mode === "auto" ? "auto-pilot continues on the project page" : "next, in the editor",
      },
    ];
    return (
      <div className="mx-auto flex max-w-md flex-col justify-center gap-6 p-8 pt-20">
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-600 to-fuchsia-600 text-white shadow-lg shadow-violet-600/20">
            <Icon name="sparkles" size={22} />
          </div>
          <h1 className="text-h1">Building your storyboard</h1>
          <p className="mt-1 text-secondary">The engine is analysing your product — about 30 seconds.</p>
        </div>
        <Card className="p-6"><Stepper steps={steps} /></Card>
        {error && <p className="text-center text-sm text-danger">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-8 lg:p-10">
      <div>
        <h1 className="text-h1">Create New Advertisement</h1>
        <p className="mt-1 text-secondary">Four steps, then the storyboard engine takes over.</p>
      </div>

      {/* stepper */}
      <ol className="flex items-center">
        {STEPS.map((label, i) => (
          <li key={label} className={cn("flex items-center", i < STEPS.length - 1 && "flex-1")}>
            <button
              onClick={() => i < step && setStep(i)}
              disabled={i > step}
              className="focus-ring flex items-center gap-2 rounded-lg disabled:cursor-default"
            >
              <span className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-colors",
                i < step ? "bg-success text-white" :
                i === step ? "bg-accent text-accent-ink" : "border border-line text-muted"
              )}>
                {i < step ? <Icon name="check" size={14} /> : i + 1}
              </span>
              <span className={cn("hidden text-sm sm:block", i === step ? "font-medium" : "text-muted")}>{label}</span>
            </button>
            {i < STEPS.length - 1 && <span className={cn("mx-2 h-px flex-1", i < step ? "bg-success" : "bg-line")} />}
          </li>
        ))}
      </ol>

      {/* STEP 0 — product */}
      {step === 0 && (
        <div className="space-y-5">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
            onClick={() => fileInput.current?.click()}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-12 text-center transition-colors",
              dragOver ? "border-accent bg-accent-soft/50" : "border-line hover:border-line-strong hover:bg-surface-2"
            )}
          >
            <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft text-accent-2">
              <Icon name="image" size={22} />
            </span>
            <div>
              <p className="text-h3">Drag product photos here</p>
              <p className="text-caption">or click to browse · up to 8 · every angle sharpens identity</p>
            </div>
            <input ref={fileInput} type="file" accept="image/*" multiple hidden
                   onChange={(e) => e.target.files && addFiles(e.target.files)} />
          </div>

          {photos.length > 0 && (
            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-overline">Reference images · {photos.length}/8</p>
                {precheckLoading && (
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <Icon name="sparkles" size={12} className="animate-pulse" /> Checking photo quality…
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-3">
                {photos.map((p, i) => {
                  const v = precheck?.photos[i];
                  return (
                    <div key={i} className="group relative w-24">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={p.url} alt="" className={cn(
                        "h-24 w-24 rounded-xl border object-cover",
                        v?.verdict === "replace" ? "border-danger" :
                        v?.verdict === "usable" ? "border-warn" : "border-line"
                      )} />
                      <button
                        onClick={() => setPhotos(photos.filter((_, j) => j !== i))}
                        className="absolute -right-1.5 -top-1.5 hidden h-5 w-5 items-center justify-center rounded-full bg-danger text-[10px] text-white shadow group-hover:flex"
                      >✕</button>
                      {v && !precheckLoading && (
                        <span className={cn(
                          "mt-1 block truncate rounded-full border px-1.5 py-0.5 text-center text-[10px] font-medium",
                          VERDICT_UI[v.verdict].cls
                        )}>
                          {VERDICT_UI[v.verdict].label}
                        </span>
                      )}
                    </div>
                  );
                })}
                {photos.length < 8 && (
                  <button onClick={() => fileInput.current?.click()}
                          className="flex h-24 w-24 items-center justify-center rounded-xl border border-dashed border-line text-muted hover:border-line-strong">
                    <Icon name="create" size={18} />
                  </button>
                )}
              </div>
              {precheck && !precheckLoading && (
                <div className={cn(
                  "mt-3 space-y-2 rounded-xl border p-3 text-sm",
                  precheck.ok_to_proceed ? "border-line bg-surface-2" : "border-danger/40 bg-danger/5"
                )}>
                  <p className={cn("font-medium", !precheck.ok_to_proceed && "text-danger")}>
                    {precheck.ok_to_proceed ? "✓ " : ""}{precheck.summary}
                    {precheck.ai_checked && (
                      <span className="ml-2 text-[10px] font-normal text-muted">AI-reviewed</span>
                    )}
                  </p>
                  {precheck.photos.some((p) => p.issues.length > 0) && (
                    <ul className="space-y-1 text-xs text-secondary">
                      {precheck.photos.flatMap((p, i) =>
                        p.issues.map((issue, j) => (
                          <li key={`${i}-${j}`}>
                            <span className="font-medium">Photo {i + 1}:</span> {issue.message}.
                            {issue.tip && <span className="text-muted"> {issue.tip}</span>}
                          </li>
                        ))
                      )}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}

          {photos.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="flex items-center gap-2 text-overline">
                  Product description
                  <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[9px] font-medium normal-case text-accent-2">
                    <Icon name="sparkles" size={10} />
                    {descLoading ? "generating…" : "AI suggestion — edit if needed"}
                  </span>
                </span>
                <button type="button" onClick={() => { setDescEdited(false); generateDescription(photos); }}
                        disabled={descLoading} className="text-xs text-accent-2 hover:underline disabled:opacity-50">
                  Regenerate
                </button>
              </div>
              <textarea
                rows={3}
                value={descLoading && !description ? "" : description}
                onChange={(e) => { setDescription(e.target.value); setDescEdited(true); }}
                placeholder={descLoading ? "Reading your photos…" : "Describe the product…"}
                className={cn("focus-ring w-full rounded-xl border border-line bg-surface p-3 text-sm", descLoading && "animate-pulse text-muted")}
              />
              {descError && <p className="mt-1 text-xs text-warn">{descError}</p>}
            </div>
          )}
        </div>
      )}

      {/* STEP 1 — brief */}
      {step === 1 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {([["audience", "Audience", "e.g. busy parents who care about ingredients"],
             ["offer", "Offer", "e.g. 20% off launch week"],
             ["cta", "Call to action", "e.g. Shop now"],
             ["tone", "Tone", "e.g. confident but friendly"]] as const
          ).map(([k, label, ph]) => (
            <label key={k} className="block">
              <span className="mb-1.5 block text-overline">
                {label} <span className="font-normal normal-case text-muted-2">optional</span>
              </span>
              <input value={brief[k]} onChange={(e) => setBrief({ ...brief, [k]: e.target.value })} placeholder={ph}
                     className="focus-ring w-full rounded-xl border border-line bg-surface p-3 text-sm" />
            </label>
          ))}
          <label className="block sm:col-span-2">
            <span className="mb-2 block text-overline">Target duration — <span className="text-ink">{duration}s</span></span>
            <input type="range" min={10} max={60} step={5} value={duration}
                   onChange={(e) => setDuration(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
            <div className="mt-1 flex justify-between text-[10px] text-muted-2"><span>10s</span><span>60s</span></div>
          </label>
        </div>
      )}

      {/* STEP 2 — style */}
      {step === 2 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <button onClick={() => setStyle("")}
            className={cn("rounded-xl border p-4 text-left transition-all", style === "" ? "border-accent ring-1 ring-accent" : "border-line hover:border-line-strong")}>
            <div className="mb-3 flex h-16 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/20 to-fuchsia-500/20 text-accent-2">
              <Icon name="sparkles" size={22} />
            </div>
            <p className="text-h3">Let AI choose</p>
            <p className="mt-0.5 text-caption">The strategy stage picks the best fit for the product.</p>
          </button>
          {STYLE_CARDS.map((s) => (
            <button key={s.id} onClick={() => setStyle(s.id)}
              className={cn("rounded-xl border p-4 text-left transition-all", style === s.id ? "border-accent ring-1 ring-accent" : "border-line hover:border-line-strong")}>
              <div className="mb-3 flex h-16 overflow-hidden rounded-lg">
                {s.swatch.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
              </div>
              <p className="text-h3">{s.name}</p>
              <p className="mt-0.5 text-caption">{s.blurb}</p>
            </button>
          ))}
        </div>
      )}

      {/* STEP 3 — review */}
      {step === 3 && (
        <>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <button onClick={() => setMode("auto")}
            className={cn("rounded-xl border p-4 text-left transition-all",
              mode === "auto" ? "border-accent ring-1 ring-accent" : "border-line hover:border-line-strong")}>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 text-white">
                <Icon name="sparkles" size={15} />
              </span>
              <p className="text-h3">Auto-pilot</p>
            </div>
            <p className="text-caption">
              The AI builds everything: storyboard, quality-checked scene images,
              and the final video — hands-free. It pauses and asks you only if the
              product identity check fails.
            </p>
          </button>
          <button onClick={() => setMode("manual")}
            className={cn("rounded-xl border p-4 text-left transition-all",
              mode === "manual" ? "border-accent ring-1 ring-accent" : "border-line hover:border-line-strong")}>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-2 text-ink">
                <Icon name="edit" size={15} />
              </span>
              <p className="text-h3">Manual review</p>
            </div>
            <p className="text-caption">
              Review and edit every step yourself: approve the storyboard, pick
              each scene image, then start production when you are ready.
            </p>
          </button>
        </div>
        <Card className="divide-y divide-line">
          <div className="flex items-center gap-3 overflow-x-auto p-4">
            {photos.map((p, i) => (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img key={i} src={p.url} alt="" className="h-16 w-16 shrink-0 rounded-lg border border-line object-cover" />
            ))}
            <p className="text-sm text-muted">{photos.length} photo{photos.length !== 1 && "s"}</p>
          </div>
          {[
            ["Description", description],
            ["Audience", brief.audience || "AI decides"],
            ["Offer", brief.offer || "AI decides"],
            ["CTA", brief.cta || "AI decides"],
            ["Tone", brief.tone || "AI decides"],
            ["Style", STYLE_CARDS.find((s) => s.id === style)?.name ?? "AI decides"],
            ["Duration", `${duration}s`],
            ["Build mode", mode === "auto" ? "Auto-pilot — AI builds the whole ad" : "Manual review"],
          ].map(([k, v]) => (
            <div key={k} className="flex gap-4 p-4 text-sm">
              <span className="w-28 shrink-0 font-medium text-muted">{k}</span>
              <span className="min-w-0 break-words">{v}</span>
            </div>
          ))}
        </Card>
        </>
      )}

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex justify-between">
        <Button variant="outline" disabled={step === 0} onClick={() => setStep(step - 1)}>
          <Icon name="chevron" size={14} className="rotate-90" /> Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button variant="accent" disabled={!canNext} onClick={() => setStep(step + 1)}>
            Continue <Icon name="chevronRight" size={14} />
          </Button>
        ) : (
          <Button variant="ai" size="lg" onClick={submit}>
            <Icon name="sparkles" size={16} />
            {mode === "auto" ? "Build my ad automatically" : "Generate storyboard"}
          </Button>
        )}
      </div>
    </div>
  );
}
