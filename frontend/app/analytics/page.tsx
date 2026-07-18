"use client";

import { ComingSoon } from "@/components/coming-soon";
import { Card, StatCard } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { usePageHeader } from "@/components/header-store";

const BARS = [40, 65, 45, 80, 55, 90, 70, 60, 85, 50, 75, 95];

export default function Analytics() {
  usePageHeader({ breadcrumb: [{ label: "Analytics" }] });
  return (
    <ComingSoon icon="analytics" title="Analytics"
      tagline="Track spend, generation volume, QC pass-rate and cost-per-video over time — the numbers behind your ad pipeline.">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Spend (30d)" value="$—" sub="per day trend" accent="text-accent-2" icon={<Icon name="analytics" size={18} />} />
        <StatCard label="Videos" value="—" sub="completed ads" accent="text-info" icon={<Icon name="video" size={18} />} />
        <StatCard label="QC pass-rate" value="—%" sub="first attempt" accent="text-success" icon={<Icon name="check" size={18} />} />
        <StatCard label="Cost / video" value="$—" sub="rolling avg" accent="text-warn" icon={<Icon name="film" size={18} />} />
      </div>
      <Card className="mt-4 p-5">
        <p className="text-overline">Spend over time</p>
        <div className="mt-4 flex h-40 items-end gap-2">
          {BARS.map((h, i) => (
            <span key={i} className="flex-1 rounded-t bg-gradient-to-t from-accent/30 to-accent/70" style={{ height: `${h}%` }} />
          ))}
        </div>
      </Card>
    </ComingSoon>
  );
}
