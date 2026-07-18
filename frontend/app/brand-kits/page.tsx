"use client";

import { ComingSoon } from "@/components/coming-soon";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { usePageHeader } from "@/components/header-store";

const SWATCHES = ["#7c3aed", "#18181b", "#059669", "#f59e0b", "#ffffff"];

export default function BrandKits() {
  usePageHeader({ breadcrumb: [{ label: "Brand Kits" }] });
  return (
    <ComingSoon icon="brand" title="Brand Kits"
      tagline="Design a reusable identity — logo, colors, type, voice, CTA and watermark — that every project inherits automatically.">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="space-y-4 p-5 lg:col-span-2">
          <Field label="Logo">
            <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-line bg-surface-2 text-muted">
              <Icon name="image" size={22} />
            </div>
          </Field>
          <Field label="Brand colors">
            <div className="flex gap-2">
              {SWATCHES.map((c) => (
                <span key={c} className="h-9 w-9 rounded-lg border border-line" style={{ background: c }} />
              ))}
              <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-dashed border-line text-muted">
                <Icon name="create" size={14} />
              </span>
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Typography"><Box>Inter · Display / Body</Box></Field>
            <Field label="Default CTA"><Box>Shop Now</Box></Field>
          </div>
        </Card>
        <Card className="space-y-4 p-5">
          <Field label="Brand voice"><Box>Premium · trustworthy · warm</Box></Field>
          <Field label="Watermark"><Box>Bottom-right · 60% opacity</Box></Field>
          <Field label="Applies to">
            <p className="text-sm text-muted">Every new ad inherits these defaults; per-project overrides stay available.</p>
          </Field>
        </Card>
      </div>
    </ComingSoon>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-muted">{label}</p>
      {children}
    </div>
  );
}
function Box({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-line bg-bg px-3 py-2 text-sm">{children}</div>;
}
