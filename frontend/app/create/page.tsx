"use client";

import { useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const STEPS = ["Product", "Creative Brief", "Style", "Review"] as const;

const STYLE_CARDS = [
  {
    id: "minimal_tech", name: "Minimal Tech",
    blurb: "Clean whites, negative space, engineered precision.",
    swatch: ["#fafafa", "#cbd5e1", "#64748b", "#2563eb"],
  },
  {
    id: "warm_lifestyle", name: "Warm Lifestyle",
    blurb: "Golden light, cozy textures, everyday moments.",
    swatch: ["#fef3c7", "#fbbf24", "#b45309", "#7c2d12"],
  },
  {
    id: "bold_energy", name: "Bold Energy",
    blurb: "High contrast, punchy color, kinetic pacing.",
    swatch: ["#18181b", "#ef4444", "#facc15", "#f5f5f5"],
  },
  {
    id: "studio_luxury", name: "Studio Luxury",
    blurb: "Deep shadows, rich materials, premium restraint.",
    swatch: ["#0c0a09", "#44403c", "#a8a29e", "#ca8a04"],
  },
  {
    id: "ugc_handheld", name: "UGC Handheld",
    blurb: "Authentic, casual, shot-on-a-phone credibility.",
    swatch: ["#e7e5e4", "#a3e635", "#38bdf8", "#f472b6"],
  },
] as const;

const PIPELINE = [
  { key: "analysis", label: "Product Analysis", eta: 9 },
  { key: "brief", label: "Creative Brief", eta: 5 },
  { key: "strategy", label: "Strategy", eta: 7 },
  { key: "storyboard", label: "Storyboard", eta: 12 },
] as const;

type Photo = { file: File; url: string };

export default function CreateAd() {
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
  const fileInput = useRef<HTMLInputElement>(null);
  const descReq = useRef(0);

  function addFiles(list: FileList | File[]) {
    const next = [...photos];
    for (const f of Array.from(list)) {
      if (f.type.startsWith("image/")) next.push({ file: f, url: URL.createObjectURL(f) });
    }
    setPhotos(next.slice(0, 8));
  }

  // Auto-generate a description suggestion from up to 4 photos. It becomes an
  // editable prefill; once the user edits it we stop overwriting their text.
  async function generateDescription(current: Photo[]) {
    if (current.length === 0) return;
    const id = ++descReq.current;
    setDescLoading(true);
    setDescError("");
    try {
      const fd = new FormData();
      current.slice(0, 4).forEach((p) => fd.append("photos", p.file));
      const r = await fetch(`${API}/describe`, {
        method: "POST", credentials: "include", body: fd,
      });
      if (!r.ok) throw new Error(await r.text());
      const { description: text } = await r.json();
      if (id === descReq.current && !descEdited) setDescription(text);
    } catch {
      if (id === descReq.current) setDescError("Couldn't auto-describe — write one below.");
    } finally {
      if (id === descReq.current) setDescLoading(false);
    }
  }

  // Regenerate when the photo set changes (unless the user has taken over).
  useEffect(() => {
    if (photos.length === 0) {
      setDescription("");
      setDescEdited(false);
      return;
    }
    if (!descEdited) generateDescription(photos);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [photos]);

  // simulated stage progress while the synchronous pipeline call runs
  useEffect(() => {
    if (!running) return;
    if (stageIdx >= PIPELINE.length - 1) return;
    const t = setTimeout(() => setStageIdx((i) => i + 1), PIPELINE[stageIdx].eta * 1000);
    return () => clearTimeout(t);
  }, [running, stageIdx]);

  const canNext =
    step === 0 ? photos.length > 0 && !descLoading && description.trim().length > 10 :
    step === 1 ? true :
    step === 2 ? true : true;

  async function submit() {
    setRunning(true);
    setStageIdx(0);
    setError("");
    const fd = new FormData();
    photos.forEach((p) => fd.append("photos", p.file));
    fd.append("description", description);
    if (brief.audience) fd.append("audience", brief.audience);
    if (brief.offer) fd.append("offer", brief.offer);
    if (brief.cta) fd.append("cta", brief.cta);
    if (brief.tone) fd.append("tone", brief.tone);
    if (style) fd.append("style", style);
    fd.append("target_duration_s", String(duration));
    try {
      const r = await fetch(`${API}/projects`, { method: "POST", credentials: "include", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const project = await r.json();
      setStageIdx(PIPELINE.length);
      window.location.href = `/projects/${project.project_id}`;
    } catch (e) {
      setError(String(e));
      setRunning(false);
    }
  }

  if (running) {
    return (
      <div className="mx-auto flex max-w-md flex-col justify-center gap-6 p-8 pt-24">
        <h1 className="text-xl font-semibold tracking-tight">Building your storyboard</h1>
        <Card className="space-y-4 p-6">
          {PIPELINE.map((s, i) => (
            <div key={s.key} className="flex items-center gap-3 text-sm">
              {i < stageIdx ? (
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500/15 text-xs text-emerald-600 dark:text-emerald-400">✓</span>
              ) : i === stageIdx ? (
                <span className="h-6 w-6 animate-spin rounded-full border-2 border-line border-t-accent" />
              ) : (
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-line text-xs text-muted">{i + 1}</span>
              )}
              <span className={cn(i <= stageIdx ? "font-medium" : "text-muted")}>{s.label}</span>
              {i === stageIdx && <span className="ml-auto text-xs text-muted">working…</span>}
            </div>
          ))}
          <div className="flex items-center gap-3 text-sm text-muted/60">
            <span className="flex h-6 w-6 items-center justify-center rounded-full border border-line text-xs">5</span>
            Images · Videos · Render <span className="ml-auto text-xs">next, in the editor</span>
          </div>
        </Card>
        {error && <p className="text-sm text-red-500">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Create New Advertisement</h1>
        <p className="mt-1 text-sm text-muted">Four steps, then the storyboard engine takes over.</p>
      </div>

      {/* stepper */}
      <ol className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <li key={label} className="flex flex-1 items-center gap-2">
            <button
              onClick={() => i < step && setStep(i)}
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                i < step ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" :
                i === step ? "bg-accent text-accent-ink" : "border border-line text-muted"
              )}
            >
              {i < step ? "✓" : i + 1}
            </button>
            <span className={cn("hidden text-xs sm:block", i === step ? "font-medium" : "text-muted")}>
              {label}
            </span>
            {i < STEPS.length - 1 && <span className="h-px flex-1 bg-line" />}
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className="space-y-4">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
            onClick={() => fileInput.current?.click()}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-14 text-center transition-colors",
              dragOver ? "border-accent bg-accent/5" : "border-line hover:border-muted"
            )}
          >
            <span className="text-3xl">📸</span>
            <p className="text-sm font-medium">Drag product photos here</p>
            <p className="text-xs text-muted">or click to browse · up to 8 · every angle helps identity</p>
            <input ref={fileInput} type="file" accept="image/*" multiple hidden
                   onChange={(e) => e.target.files && addFiles(e.target.files)} />
          </div>
          {photos.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Reference images</p>
              <div className="flex flex-wrap gap-3">
                {photos.map((p, i) => (
                  <div key={i} className="group relative">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={p.url} alt="" className="h-24 w-24 rounded-lg border border-line object-cover" />
                    <button
                      onClick={() => setPhotos(photos.filter((_, j) => j !== i))}
                      className="absolute -top-1.5 -right-1.5 hidden h-5 w-5 items-center justify-center rounded-full bg-red-500 text-[10px] text-white group-hover:flex"
                    >✕</button>
                  </div>
                ))}
                <button onClick={() => fileInput.current?.click()}
                        className="flex h-24 w-24 items-center justify-center rounded-lg border border-dashed border-line text-xl text-muted hover:border-muted">
                  +
                </button>
              </div>
            </div>
          )}
          {/* Description is generated from the photos — hidden until they exist */}
          {photos.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">
                  Product description
                  <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[9px] font-medium text-accent">
                    {descLoading ? "generating…" : "✦ AI suggestion — edit if needed"}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => { setDescEdited(false); generateDescription(photos); }}
                  disabled={descLoading}
                  className="text-xs text-accent hover:underline disabled:opacity-50"
                >
                  Regenerate
                </button>
              </div>
              <textarea
                rows={3}
                value={descLoading && !description ? "" : description}
                onChange={(e) => { setDescription(e.target.value); setDescEdited(true); }}
                placeholder={descLoading ? "Reading your photos…" : "Describe the product…"}
                className={cn(
                  "w-full rounded-xl border border-line bg-surface p-3 text-sm focus:border-accent focus:outline-none",
                  descLoading && "animate-pulse text-muted"
                )}
              />
              {descError && <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">{descError}</p>}
            </div>
          )}
        </div>
      )}

      {step === 1 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {(
            [["audience", "Audience", "e.g. busy parents who care about ingredients"],
             ["offer", "Offer", "e.g. 20% off launch week"],
             ["cta", "Call to action", "e.g. Shop now"],
             ["tone", "Tone", "e.g. confident but friendly"]] as const
          ).map(([k, label, ph]) => (
            <label key={k} className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-muted">
                {label} <span className="font-normal normal-case">(optional — AI fills the rest)</span>
              </span>
              <input
                value={brief[k]} onChange={(e) => setBrief({ ...brief, [k]: e.target.value })}
                placeholder={ph}
                className="w-full rounded-xl border border-line bg-surface p-3 text-sm focus:border-accent focus:outline-none"
              />
            </label>
          ))}
          <label className="block sm:col-span-2">
            <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-muted">
              Target duration — {duration}s
            </span>
            <input type="range" min={10} max={60} step={5} value={duration}
                   onChange={(e) => setDuration(Number(e.target.value))}
                   className="w-full accent-[var(--accent)]" />
          </label>
        </div>
      )}

      {step === 2 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <button
            onClick={() => setStyle("")}
            className={cn("rounded-xl border p-4 text-left transition-all",
              style === "" ? "border-accent ring-1 ring-accent" : "border-line hover:border-muted")}
          >
            <div className="mb-3 flex h-16 items-center justify-center rounded-lg bg-line/40 text-2xl">✦</div>
            <p className="text-sm font-semibold">Let AI choose</p>
            <p className="mt-0.5 text-xs text-muted">The strategy stage picks the best fit for the product.</p>
          </button>
          {STYLE_CARDS.map((s) => (
            <button
              key={s.id}
              onClick={() => setStyle(s.id)}
              className={cn("rounded-xl border p-4 text-left transition-all",
                style === s.id ? "border-accent ring-1 ring-accent" : "border-line hover:border-muted")}
            >
              <div className="mb-3 flex h-16 overflow-hidden rounded-lg">
                {s.swatch.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
              </div>
              <p className="text-sm font-semibold">{s.name}</p>
              <p className="mt-0.5 text-xs text-muted">{s.blurb}</p>
            </button>
          ))}
        </div>
      )}

      {step === 3 && (
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
          ].map(([k, v]) => (
            <div key={k} className="flex gap-4 p-4 text-sm">
              <span className="w-28 shrink-0 font-medium text-muted">{k}</span>
              <span className="min-w-0 break-words">{v}</span>
            </div>
          ))}
        </Card>
      )}

      {error && <p className="text-sm text-red-500">{error}</p>}

      <div className="flex justify-between">
        <Button variant="outline" disabled={step === 0} onClick={() => setStep(step - 1)}>
          ← Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button variant="accent" disabled={!canNext} onClick={() => setStep(step + 1)}>
            Continue →
          </Button>
        ) : (
          <Button variant="accent" size="lg" onClick={submit}>
            ✦ Generate storyboard
          </Button>
        )}
      </div>
    </div>
  );
}
