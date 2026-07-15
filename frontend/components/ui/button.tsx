import { cn } from "@/lib/utils";

export function Button({
  className,
  variant = "default",
  size = "md",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "accent" | "outline" | "destructive" | "ghost";
  size?: "sm" | "md" | "lg";
}) {
  const variants = {
    default: "bg-ink text-bg hover:opacity-85",
    accent: "bg-accent text-accent-ink hover:opacity-90",
    outline: "border border-line bg-surface hover:bg-line/40",
    destructive: "bg-red-600 text-white hover:bg-red-500",
    ghost: "hover:bg-line/40",
  };
  const sizes = {
    sm: "px-2.5 py-1 text-xs",
    md: "px-3.5 py-1.5 text-sm",
    lg: "px-5 py-2.5 text-sm",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    />
  );
}
