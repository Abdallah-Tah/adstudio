"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API, authStatus } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Avatar } from "@/components/ui/avatar";
import { Icon } from "@/components/ui/icon";
import { usePageHeader } from "@/components/header-store";

export default function Settings() {
  usePageHeader({ breadcrumb: [{ label: "Settings" }] });
  const [email, setEmail] = useState<string | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    authStatus().then((s) => setEmail(s.email)).catch(() => {});
    setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  function setThemeMode(next: "light" | "dark") {
    setTheme(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    localStorage.theme = next;
  }

  async function logout() {
    await fetch(`${API}/auth/logout`, { method: "POST", credentials: "include" });
    window.location.href = "/login";
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8 lg:p-10">
      <div>
        <h1 className="text-h1">Settings</h1>
        <p className="mt-1 text-secondary">Manage your account and studio preferences.</p>
      </div>

      {/* account */}
      <Card className="p-5">
        <h2 className="mb-4 text-overline">Account</h2>
        <div className="flex items-center gap-3">
          <Avatar email={email} size="lg" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-h3">{email?.split("@")[0] ?? "Owner"}</p>
            <p className="truncate text-xs text-muted">{email}</p>
          </div>
          <Badge tone="storyboard">Beta · Owner</Badge>
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="ghost" onClick={logout}>
            <Icon name="logout" size={15} /> Log out
          </Button>
        </div>
      </Card>

      {/* appearance */}
      <Card className="p-5">
        <h2 className="mb-1 text-overline">Appearance</h2>
        <p className="mb-4 text-xs text-muted">Choose how the studio looks.</p>
        <div className="grid grid-cols-2 gap-3">
          {(["light", "dark"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setThemeMode(mode)}
              className={`focus-ring flex items-center gap-3 rounded-xl border p-3 text-left transition-all ${
                theme === mode ? "border-accent ring-1 ring-accent" : "border-line hover:border-line-strong"
              }`}
            >
              <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${mode === "dark" ? "bg-zinc-900 text-zinc-100" : "bg-zinc-100 text-zinc-900"}`}>
                <Icon name={mode === "dark" ? "moon" : "sun"} size={16} />
              </span>
              <span className="text-sm font-medium capitalize">{mode}</span>
              {theme === mode && <Icon name="check" size={15} className="ml-auto text-accent-2" />}
            </button>
          ))}
        </div>
      </Card>

      {/* providers pointer */}
      <Card className="flex items-center gap-4 p-5">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent-2">
          <Icon name="providers" size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-h3">Provider keys</p>
          <p className="text-xs text-muted">API keys now live on the Providers page — with status, models and pricing.</p>
        </div>
        <Link href="/providers"><Button variant="outline" size="sm">Manage <Icon name="chevronRight" size={14} /></Button></Link>
      </Card>
    </div>
  );
}
