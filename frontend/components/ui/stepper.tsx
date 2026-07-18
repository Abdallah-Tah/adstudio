import { cn } from "@/lib/utils";
import { Icon } from "./icon";

export type StepState = "done" | "active" | "pending" | "error";
export type Step = { label: string; state: StepState; hint?: string };

// Vertical generation pipeline: ✓ done · spinner active · ○ waiting · ✕ error.
export function Stepper({ steps, className }: { steps: Step[]; className?: string }) {
  return (
    <ol className={cn("space-y-0.5", className)}>
      {steps.map((s, i) => (
        <li key={i} className="flex items-center gap-2.5 py-1">
          <StepDot state={s.state} />
          <div className="min-w-0">
            <p
              className={cn(
                "truncate text-sm",
                s.state === "pending" && "text-muted",
                s.state === "active" && "font-medium text-ink",
                s.state === "done" && "text-ink-2",
                s.state === "error" && "font-medium text-danger"
              )}
            >
              {s.label}
            </p>
            {s.hint && <p className="truncate text-[11px] text-muted">{s.hint}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function StepDot({ state }: { state: StepState }) {
  if (state === "done")
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-success text-white">
        <Icon name="check" size={12} />
      </span>
    );
  if (state === "active")
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full border-2 border-accent border-t-transparent motion-safe:animate-spin" />
    );
  if (state === "error")
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-danger text-white text-[11px] font-bold">
        !
      </span>
    );
  return <span className="h-5 w-5 rounded-full border-2 border-line-strong" />;
}
