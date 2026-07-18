"use client";

import { ComingSoon } from "@/components/coming-soon";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { usePageHeader } from "@/components/header-store";

const TEMPLATES = [
  ["Problem → Solution", "15s · 4 scenes"],
  ["Unboxing", "20s · 5 scenes"],
  ["Before / After", "12s · 3 scenes"],
  ["Founder story", "30s · 6 scenes"],
  ["Feature montage", "18s · 5 scenes"],
  ["Testimonial", "20s · 4 scenes"],
];

export default function Templates() {
  usePageHeader({ breadcrumb: [{ label: "Templates" }] });
  return (
    <ComingSoon icon="templates" title="Templates"
      tagline="Start from a proven ad structure instead of a blank storyboard. One click applies pacing, scene beats and caption styles.">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {TEMPLATES.map(([name, meta]) => (
          <Card key={name} className="overflow-hidden">
            <div className="flex aspect-video items-center justify-center bg-surface-2 text-muted-2">
              <Icon name="film" size={26} />
            </div>
            <div className="p-4">
              <p className="text-h3">{name}</p>
              <p className="text-caption">{meta}</p>
            </div>
          </Card>
        ))}
      </div>
    </ComingSoon>
  );
}
