import { cn } from "@/lib/utils";

type ToneDef = { cls: string; dot: string; label?: string };

// One source of truth for status colour. `soft` backgrounds + a matching dot.
const TONES: Record<string, ToneDef> = {
  // neutral / draft
  draft: { cls: "bg-line/60 text-muted", dot: "bg-muted-2" },
  missing: { cls: "bg-line/60 text-muted", dot: "bg-muted-2" },
  // info (blue)
  video_ready: { cls: "bg-info-soft text-info", dot: "bg-info" },
  ready: { cls: "bg-info-soft text-info", dot: "bg-info" },
  info: { cls: "bg-info-soft text-info", dot: "bg-info" },
  // AI / storyboard (violet)
  storyboard: { cls: "bg-accent-soft text-accent-2", dot: "bg-accent" },
  ai: { cls: "bg-accent-soft text-accent-2", dot: "bg-accent" },
  // success (emerald)
  image_ready: { cls: "bg-success-soft text-success", dot: "bg-success" },
  images_ready: { cls: "bg-success-soft text-success", dot: "bg-success" },
  succeeded: { cls: "bg-success-soft text-success", dot: "bg-success" },
  approved: { cls: "bg-success-soft text-success", dot: "bg-success" },
  completed: { cls: "bg-success-soft text-success", dot: "bg-success" },
  connected: { cls: "bg-success-soft text-success", dot: "bg-success" },
  // in-progress (amber, animated dot)
  rendering: { cls: "bg-warn-soft text-warn", dot: "bg-warn animate-pulse" },
  queued: { cls: "bg-warn-soft text-warn", dot: "bg-warn animate-pulse" },
  running: { cls: "bg-warn-soft text-warn", dot: "bg-warn animate-pulse" },
  generating: { cls: "bg-warn-soft text-warn", dot: "bg-warn animate-pulse" },
  // warning (orange)
  stale: { cls: "bg-orange-500/12 text-orange-600 dark:text-orange-400", dot: "bg-orange-500" },
  // failure (red)
  failed: { cls: "bg-danger-soft text-danger", dot: "bg-danger" },
  qc_rejected: { cls: "bg-danger-soft text-danger", dot: "bg-danger" },
  qc_failed: { cls: "bg-danger-soft text-danger", dot: "bg-danger" },
};

export function Badge({
  tone,
  children,
  dot = false,
  size = "md",
  className,
}: {
  tone: string;
  children: React.ReactNode;
  dot?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  const t = TONES[tone] ?? TONES.draft;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full font-medium capitalize",
        size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs",
        t.cls,
        className
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", t.dot)} />}
      {children}
    </span>
  );
}
