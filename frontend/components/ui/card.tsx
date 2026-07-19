import { cn } from "@/lib/utils";

export function Card({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  variant?: "default" | "interactive" | "elevated" | "flat";
}) {
  const variants = {
    default: "border border-line bg-surface shadow-sm",
    interactive: "border border-line bg-surface shadow-sm card-hover cursor-pointer",
    elevated: "border border-line bg-surface shadow-md",
    flat: "border border-line bg-surface-2",
  };
  return (
    <div
      className={cn("rounded-xl", variants[variant], className)}
      {...props}
    />
  );
}

// A labelled metric card for dashboards.
export function StatCard({
  label,
  value,
  sub,
  icon,
  accent = "text-muted",
  className,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  icon?: React.ReactNode;
  accent?: string;
  className?: string;
}) {
  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-start justify-between">
        <p className="text-overline">{label}</p>
        {icon && <span className={cn("opacity-80", accent)}>{icon}</span>}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight">{value}</div>
      {sub && <p className="mt-1 text-xs text-muted">{sub}</p>}
    </Card>
  );
}
