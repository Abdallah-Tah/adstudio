import { cn } from "@/lib/utils";

// Professional empty-state block: soft icon, title, one line of guidance, and an
// optional action. Used across projects/scenes/assets/providers/brand-kits.
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  compact = false,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center animate-fade-in",
        compact ? "gap-2 py-8" : "gap-3 py-14",
        className
      )}
    >
      {icon && (
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-line bg-surface-2 text-muted">
          {icon}
        </div>
      )}
      <div className="space-y-1">
        <p className="text-h3">{title}</p>
        {description && (
          <p className="mx-auto max-w-xs text-sm text-muted">{description}</p>
        )}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
