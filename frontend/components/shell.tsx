"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { getJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: "▦" },
  { href: "/create", label: "Create Ad", icon: "✦" },
  { href: "/providers", label: "Providers", icon: "⚡" },
] as const;

const SOON = ["Brand Kits", "Assets", "Templates", "Analytics", "Settings"];

function useTheme() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.theme = next ? "dark" : "light";
  }
  return { dark, toggle };
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { dark, toggle } = useTheme();
  const [spend, setSpend] = useState<number | null>(null);
  const [providersOk, setProvidersOk] = useState<[number, number] | null>(null);
  const inEditor = pathname.startsWith("/projects/");

  useEffect(() => {
    getJSON("/projects")
      .then((ps: { cost_cents: number }[]) =>
        setSpend(ps.reduce((a, p) => a + p.cost_cents, 0)))
      .catch(() => {});
    getJSON("/providers")
      .then((ps: { connected: boolean }[]) =>
        setProvidersOk([ps.filter((p) => p.connected).length, ps.length]))
      .catch(() => {});
  }, [pathname]);

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* sidebar */}
      <aside className="hidden w-52 shrink-0 flex-col border-r border-line bg-surface md:flex">
        <Link href="/" className="flex items-center gap-2 px-5 pt-5 pb-6">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-sm font-bold text-accent-ink">A</span>
          <span className="text-sm font-semibold tracking-tight">AI Ad Studio</span>
        </Link>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                pathname === n.href
                  ? "bg-line/50 font-medium"
                  : "text-muted hover:bg-line/30 hover:text-ink"
              )}
            >
              <span className="text-xs">{n.icon}</span>
              {n.label}
            </Link>
          ))}
          <div className="pt-4 pb-1 pl-3 text-[10px] font-semibold uppercase tracking-widest text-muted/60">
            Coming soon
          </div>
          {SOON.map((label) => (
            <div key={label} className="flex cursor-not-allowed items-center justify-between rounded-lg px-3 py-2 text-sm text-muted/50">
              {label}
              <span className="rounded-full bg-line/60 px-1.5 py-0.5 text-[9px] uppercase tracking-wide">soon</span>
            </div>
          ))}
        </nav>
        <div className="border-t border-line px-5 py-3 text-[10px] text-muted">
          v0.1 · single-user beta
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* header */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
          <Link href="/" className="flex items-center gap-2 md:hidden">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-xs font-bold text-accent-ink">A</span>
          </Link>
          <form action="/" className="hidden max-w-xs flex-1 sm:block">
            <input
              name="q"
              placeholder="Search projects…"
              className="w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm placeholder:text-muted/70 focus:border-accent focus:outline-none"
            />
          </form>
          <div className="flex-1" />
          {providersOk && (
            <Link href="/providers" className="hidden items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-xs text-muted hover:text-ink sm:flex">
              <span className={cn("h-1.5 w-1.5 rounded-full", providersOk[0] === providersOk[1] ? "bg-emerald-500" : "bg-amber-500")} />
              {providersOk[0]}/{providersOk[1]} providers
            </Link>
          )}
          {spend !== null && (
            <span className="rounded-lg border border-line px-2.5 py-1 text-xs text-muted" title="Total API spend">
              ${(spend / 100).toFixed(2)}
            </span>
          )}
          <button
            onClick={toggle}
            title="Toggle theme"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-line text-sm hover:bg-line/40"
          >
            {dark ? "☀" : "☾"}
          </button>
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-line/60 text-xs font-semibold" title="Abdallah">
            AB
          </span>
        </header>

        <main className={cn("min-h-0 flex-1", inEditor ? "overflow-hidden" : "overflow-y-auto")}>
          {children}
        </main>

        {/* mobile nav */}
        <nav className="flex shrink-0 border-t border-line bg-surface md:hidden">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]",
                pathname === n.href ? "text-accent" : "text-muted"
              )}
            >
              <span className="text-sm">{n.icon}</span>
              {n.label}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}
