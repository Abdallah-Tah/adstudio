import { cn } from "@/lib/utils";

type Variant =
  | "default"
  | "accent"
  | "ai"
  | "outline"
  | "subtle"
  | "success"
  | "destructive"
  | "ghost";
type Size = "xs" | "sm" | "md" | "lg" | "icon" | "icon-sm";

export function Button({
  className,
  variant = "default",
  size = "md",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
}) {
  const variants: Record<Variant, string> = {
    default: "bg-ink text-bg hover:opacity-90 active:opacity-80",
    accent: "bg-accent text-accent-ink shadow-sm hover:brightness-110 active:brightness-95",
    // AI / generate — the one gradient in the app, reserved for model actions
    ai: "bg-gradient-to-br from-violet-600 to-fuchsia-600 text-white shadow-sm shadow-violet-600/20 hover:brightness-110 active:brightness-95",
    outline: "border border-line bg-surface hover:bg-surface-hover hover:border-line-strong",
    subtle: "bg-surface-hover text-ink-2 hover:bg-line/60",
    success: "bg-success text-white hover:brightness-110",
    destructive: "bg-danger text-white hover:brightness-110",
    ghost: "text-ink-2 hover:bg-surface-hover hover:text-ink",
  };
  const sizes: Record<Size, string> = {
    xs: "h-7 gap-1 px-2 text-xs",
    sm: "h-8 gap-1.5 px-2.5 text-xs",
    md: "h-9 gap-1.5 px-3.5 text-sm",
    lg: "h-11 gap-2 px-5 text-sm",
    icon: "h-9 w-9",
    "icon-sm": "h-8 w-8",
  };
  return (
    <button
      className={cn(
        "focus-ring inline-flex select-none items-center justify-center rounded-lg font-medium transition-all disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    />
  );
}
