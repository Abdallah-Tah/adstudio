import { cn } from "@/lib/utils";

export function Avatar({
  email,
  size = "md",
  className,
}: {
  email?: string | null;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const initials =
    (email?.[0] ?? "A").toUpperCase() + (email?.split("@")[0]?.[1] ?? "").toUpperCase();
  const sizes = {
    sm: "h-7 w-7 text-[11px]",
    md: "h-8 w-8 text-xs",
    lg: "h-10 w-10 text-sm",
  };
  return (
    <span
      title={email ?? undefined}
      className={cn(
        "inline-flex items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-500 font-semibold text-white",
        sizes[size],
        className
      )}
    >
      {initials}
    </span>
  );
}
