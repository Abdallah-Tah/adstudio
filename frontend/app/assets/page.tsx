"use client";

import { ComingSoon } from "@/components/coming-soon";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { usePageHeader } from "@/components/header-store";

export default function Assets() {
  usePageHeader({ breadcrumb: [{ label: "Assets" }] });
  return (
    <ComingSoon icon="assets" title="Assets"
      tagline="One library for every generated image, video and voiceover — searchable, reusable across projects.">
      <div className="mb-4 flex gap-2">
        {["All", "Images", "Videos", "Voiceovers", "Music"].map((t, i) => (
          <span key={t} className={i === 0 ? "rounded-lg bg-ink px-3 py-1.5 text-xs font-medium text-bg" : "rounded-lg border border-line px-3 py-1.5 text-xs text-muted"}>
            {t}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 10 }).map((_, i) => (
          <Card key={i} className="overflow-hidden">
            <div className="flex aspect-[9/16] items-center justify-center bg-surface-2 text-muted-2">
              <Icon name={i % 3 === 0 ? "video" : "image"} size={22} />
            </div>
          </Card>
        ))}
      </div>
    </ComingSoon>
  );
}
