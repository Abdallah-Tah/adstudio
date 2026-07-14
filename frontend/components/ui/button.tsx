import { cn } from "@/lib/utils";

export function Button({
  className,
  variant = "default",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "destructive" | "ghost";
}) {
  const variants = {
    default: "bg-neutral-900 text-white hover:bg-neutral-700",
    outline: "border border-neutral-300 bg-white hover:bg-neutral-100",
    destructive: "bg-red-600 text-white hover:bg-red-500",
    ghost: "hover:bg-neutral-100",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        className
      )}
      {...props}
    />
  );
}
