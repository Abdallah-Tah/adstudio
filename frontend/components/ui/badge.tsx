import { cn } from "@/lib/utils";

const tones: Record<string, string> = {
  draft: "bg-line/60 text-muted",
  storyboard: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  image_ready: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  images_ready: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  video_ready: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  rendering: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  queued: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  running: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  succeeded: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  qc_rejected: "bg-red-500/15 text-red-600 dark:text-red-400",
  stale: "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  connected: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  missing: "bg-line/60 text-muted",
};

export function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        tones[tone] ?? tones.draft
      )}
    >
      {children}
    </span>
  );
}
