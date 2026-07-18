"use client";

import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icon";

// A polished "planned feature" scaffold: real page chrome + a preview of the
// intended layout, honestly labelled. No fake data, no backend calls.
export function ComingSoon({
  icon,
  title,
  tagline,
  ships = "Ships with backend v4",
  children,
}: {
  icon: string;
  title: string;
  tagline: string;
  ships?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6 md:p-8 lg:p-10">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-soft text-accent-2">
            <Icon name={icon} size={22} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-h1">{title}</h1>
              <Badge tone="storyboard" size="sm">Preview</Badge>
            </div>
            <p className="mt-1 max-w-xl text-secondary">{tagline}</p>
          </div>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-muted">
          <Icon name="lock" size={13} /> {ships}
        </span>
      </div>
      <div className="relative">
        <div className="pointer-events-none select-none opacity-90">{children}</div>
      </div>
    </div>
  );
}
