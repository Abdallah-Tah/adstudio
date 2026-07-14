import { cn } from "@/lib/utils";

const tones: Record<string, string> = {
  draft: "bg-neutral-100 text-neutral-600",
  image_ready: "bg-emerald-100 text-emerald-700",
  video_ready: "bg-blue-100 text-blue-700",
  queued: "bg-amber-100 text-amber-700",
  running: "bg-amber-100 text-amber-700",
  succeeded: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  qc_rejected: "bg-red-100 text-red-700",
  stale: "bg-orange-100 text-orange-700",
};

export function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        tones[tone] ?? tones.draft
      )}
    >
      {children}
    </span>
  );
}
