"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { API, authStatus, getJSON } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Icon } from "@/components/ui/icon";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { HeaderProvider, useHeaderData } from "@/components/header-store";

type NavItem = { href: string; label: string; icon: string; soon?: boolean; match?: string };

const NAV_MAIN: NavItem[] = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/#projects", label: "Projects", icon: "projects", match: "/" },
  { href: "/create", label: "Create Ad", icon: "create" },
];
const NAV_LIBRARY: NavItem[] = [
  { href: "/brand-kits", label: "Brand Kits", icon: "brand", soon: true },
  { href: "/assets", label: "Assets", icon: "assets", soon: true },
  { href: "/templates", label: "Templates", icon: "templates", soon: true },
  { href: "/analytics", label: "Analytics", icon: "analytics", soon: true },
];
const NAV_SYSTEM: NavItem[] = [
  { href: "/providers", label: "Providers", icon: "providers" },
  { href: "/settings", label: "Settings", icon: "settings" },
];

function useTheme() {
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);
  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.theme = next ? "dark" : "light";
  }
  return { dark, toggle };
}

async function logout() {
  await fetch(`${API}/auth/logout`, { method: "POST", credentials: "include" });
  window.location.href = "/login";
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    if (isLogin) { setAuthed(true); return; }
    authStatus()
      .then((s) => {
        setAuthed(s.authenticated);
        setEmail(s.email);
        if (!s.authenticated) window.location.href = "/login";
      })
      .catch(() => { window.location.href = "/login"; });
  }, [pathname, isLogin]);

  if (isLogin) return <>{children}</>;
  if (authed === null || authed === false) {
    return (
      <div className="flex h-dvh items-center justify-center gap-2 text-sm text-muted">
        <span className="h-4 w-4 rounded-full border-2 border-accent border-t-transparent motion-safe:animate-spin" />
        Loading studio…
      </div>
    );
  }

  return (
    <HeaderProvider>
      <div className="flex h-dvh overflow-hidden bg-bg">
        <Sidebar pathname={pathname} email={email} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header pathname={pathname} email={email} />
          <Main pathname={pathname}>{children}</Main>
          <MobileNav pathname={pathname} />
        </div>
      </div>
    </HeaderProvider>
  );
}

function Main({ pathname, children }: { pathname: string; children: React.ReactNode }) {
  const inEditor = pathname.startsWith("/projects/");
  return (
    <main className={cn("min-h-0 flex-1", inEditor ? "overflow-hidden" : "overflow-y-auto")}>
      {children}
    </main>
  );
}

/* ------------------------------- sidebar -------------------------------- */

function Sidebar({ pathname, email }: { pathname: string; email: string | null }) {
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => setCollapsed(localStorage.sidebar === "collapsed"), []);
  function toggle() {
    setCollapsed((c) => {
      localStorage.sidebar = !c ? "collapsed" : "expanded";
      return !c;
    });
  }

  return (
    <aside
      className={cn(
        "hidden shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-200 md:flex",
        collapsed ? "w-[68px]" : "w-60"
      )}
    >
      {/* brand */}
      <div className="flex h-14 items-center gap-2.5 px-4">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 text-sm font-bold text-white shadow-sm">
          A
        </span>
        {!collapsed && (
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">AI Ad Studio</p>
            <p className="truncate text-[10px] text-muted">Creative Suite</p>
          </div>
        )}
      </div>

      {/* workspace selector (placeholder) */}
      {!collapsed && (
        <div className="px-3 pb-2">
          <button className="focus-ring flex w-full items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-left transition-colors hover:bg-surface-hover">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-soft text-[11px] font-semibold text-accent-2">
              {(email?.[0] ?? "P").toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium">Personal workspace</span>
              <span className="block truncate text-[10px] text-muted">Beta plan</span>
            </span>
            <Icon name="chevron" size={14} className="text-muted" />
          </button>
        </div>
      )}

      {/* nav */}
      <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-2">
        <NavGroup items={NAV_MAIN} pathname={pathname} collapsed={collapsed} />
        <NavGroup items={NAV_LIBRARY} pathname={pathname} collapsed={collapsed} label="Library" />
        <NavGroup items={NAV_SYSTEM} pathname={pathname} collapsed={collapsed} label="System" />
      </nav>

      {/* user + collapse */}
      <div className="border-t border-line p-3">
        <div className={cn("flex items-center gap-2.5", collapsed && "justify-center")}>
          <Avatar email={email} size="md" />
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium">{email?.split("@")[0] ?? "Owner"}</p>
              <p className="truncate text-[10px] text-muted">Beta · Owner</p>
            </div>
          )}
          {!collapsed && (
            <button
              onClick={logout}
              title="Log out"
              className="focus-ring flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-hover hover:text-danger"
            >
              <Icon name="logout" size={15} />
            </button>
          )}
        </div>
        <button
          onClick={toggle}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="focus-ring mt-2 flex w-full items-center justify-center gap-1.5 rounded-md py-1.5 text-[11px] text-muted transition-colors hover:bg-surface-hover"
        >
          <Icon name={collapsed ? "chevronRight" : "chevron"} size={14} className={collapsed ? "" : "-rotate-90"} />
          {!collapsed && "Collapse"}
        </button>
      </div>
    </aside>
  );
}

function NavGroup({
  items,
  pathname,
  collapsed,
  label,
}: {
  items: NavItem[];
  pathname: string;
  collapsed: boolean;
  label?: string;
}) {
  return (
    <div className="space-y-0.5">
      {label && !collapsed && (
        <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-widest text-muted/60">
          {label}
        </p>
      )}
      {items.map((n) => {
        const active = (n.match ?? n.href) === pathname || (n.href !== "/" && pathname.startsWith(n.href));
        return (
          <Link
            key={n.href}
            href={n.href}
            title={collapsed ? n.label : undefined}
            className={cn(
              "focus-ring group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
              collapsed && "justify-center px-0",
              active
                ? "bg-accent-soft font-medium text-accent-2"
                : "text-muted hover:bg-surface-hover hover:text-ink"
            )}
          >
            <Icon name={n.icon} size={17} className={active ? "text-accent-2" : ""} />
            {!collapsed && <span className="flex-1 truncate">{n.label}</span>}
            {!collapsed && n.soon && (
              <span className="rounded-full bg-line/70 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-muted">
                soon
              </span>
            )}
          </Link>
        );
      })}
    </div>
  );
}

/* -------------------------------- header -------------------------------- */

function Header({ pathname, email }: { pathname: string; email: string | null }) {
  const { dark, toggle } = useTheme();
  const header = useHeaderData();
  const [spend, setSpend] = useState<number | null>(null);
  const [providersOk, setProvidersOk] = useState<[number, number] | null>(null);

  useEffect(() => {
    getJSON("/projects")
      .then((ps: { cost_cents: number }[]) => setSpend(ps.reduce((a, p) => a + p.cost_cents, 0)))
      .catch(() => {});
    getJSON("/providers")
      .then((ps: { connected: boolean }[]) =>
        setProvidersOk([ps.filter((p) => p.connected).length, ps.length]))
      .catch(() => {});
  }, [pathname]);

  const crumbs = header.breadcrumb.length ? header.breadcrumb : [{ label: routeTitle(pathname) }];

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/80 px-4 backdrop-blur">
      {/* breadcrumb + context */}
      <div className="flex min-w-0 items-center gap-2">
        <nav className="flex min-w-0 items-center gap-1.5 text-sm">
          {crumbs.map((c, i) => (
            <span key={i} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && <Icon name="chevronRight" size={13} className="text-muted-2" />}
              {c.href && i < crumbs.length - 1 ? (
                <Link href={c.href} className="truncate text-muted hover:text-ink">{c.label}</Link>
              ) : (
                <span className={cn("truncate", i === crumbs.length - 1 ? "font-semibold text-ink" : "text-muted")}>
                  {c.label}
                </span>
              )}
            </span>
          ))}
        </nav>
        {header.badges?.map((b, i) => (
          <Badge key={i} tone={b.tone} dot={b.dot} size="sm">{b.label}</Badge>
        ))}
      </div>

      {/* context metrics + progress */}
      <div className="ml-2 hidden items-center gap-3 lg:flex">
        {header.metrics?.map((m, i) => (
          <span key={i} className="flex items-center gap-1 text-xs text-muted">
            {m.label} <span className={cn("font-medium", m.accent ?? "text-ink")}>{m.value}</span>
          </span>
        ))}
        {typeof header.progress === "number" && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="h-1.5 w-20 overflow-hidden rounded-full bg-line">
              <span
                className="block h-full rounded-full bg-accent transition-all duration-500"
                style={{ width: `${Math.round(header.progress)}%` }}
              />
            </span>
            {Math.round(header.progress)}%
          </span>
        )}
      </div>

      <div className="flex-1" />

      {/* search */}
      <form action="/" className="hidden max-w-[200px] flex-1 items-center sm:flex">
        <div className="relative w-full">
          <Icon name="search" size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input
            name="q"
            placeholder="Search…"
            className="focus-ring w-full rounded-lg border border-line bg-bg py-1.5 pl-8 pr-3 text-sm placeholder:text-muted/70"
          />
        </div>
      </form>

      {/* global chips */}
      {providersOk && (
        <Link
          href="/providers"
          title="Configured providers"
          className="hidden items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-xs text-muted hover:text-ink xl:flex"
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", providersOk[0] === providersOk[1] ? "bg-success" : "bg-warn")} />
          {providersOk[0]}/{providersOk[1]}
        </Link>
      )}
      {spend !== null && (
        <span className="hidden items-center rounded-lg border border-line px-2.5 py-1.5 text-xs text-muted sm:flex" title="Total API spend">
          ${(spend / 100).toFixed(2)}
        </span>
      )}

      <Notifications />

      <button
        onClick={toggle}
        title="Toggle theme"
        className="focus-ring flex h-8 w-8 items-center justify-center rounded-lg border border-line text-muted hover:text-ink"
      >
        <Icon name={dark ? "sun" : "moon"} size={15} />
      </button>

      <UserMenu email={email} />
    </header>
  );
}

function Notifications() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false));
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        title="Notifications"
        className="focus-ring relative flex h-8 w-8 items-center justify-center rounded-lg border border-line text-muted hover:text-ink"
      >
        <Icon name="bell" size={15} />
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-20 w-64 animate-slide-up rounded-xl border border-line bg-surface p-2 shadow-lg">
          <p className="px-2 py-1.5 text-xs font-semibold">Notifications</p>
          <div className="px-2 py-6 text-center text-xs text-muted">You&apos;re all caught up.</div>
        </div>
      )}
    </div>
  );
}

function UserMenu({ email }: { email: string | null }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false));
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} className="focus-ring rounded-full">
        <Avatar email={email} size="md" />
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-20 w-56 animate-slide-up overflow-hidden rounded-xl border border-line bg-surface shadow-lg">
          <div className="flex items-center gap-2.5 border-b border-line px-3 py-3">
            <Avatar email={email} size="md" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{email?.split("@")[0]}</p>
              <p className="truncate text-[11px] text-muted">{email}</p>
            </div>
          </div>
          <div className="p-1.5">
            <div className="flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs">
              <span className="text-muted">Plan</span>
              <Badge tone="storyboard" size="sm">Beta</Badge>
            </div>
            <Link href="/settings" onClick={() => setOpen(false)} className="flex items-center gap-2 rounded-md px-2.5 py-2 text-sm text-ink-2 hover:bg-surface-hover">
              <Icon name="settings" size={15} /> Settings
            </Link>
            <button onClick={logout} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-sm text-danger hover:bg-danger-soft">
              <Icon name="logout" size={15} /> Log out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function MobileNav({ pathname }: { pathname: string }) {
  const items = [...NAV_MAIN, NAV_SYSTEM[0]];
  return (
    <nav className="flex shrink-0 border-t border-line bg-surface md:hidden">
      {items.map((n) => {
        const active = (n.match ?? n.href) === pathname;
        return (
          <Link
            key={n.href}
            href={n.href}
            className={cn(
              "flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]",
              active ? "text-accent-2" : "text-muted"
            )}
          >
            <Icon name={n.icon} size={18} />
            {n.label}
          </Link>
        );
      })}
    </nav>
  );
}

/* ------------------------------- helpers -------------------------------- */

function routeTitle(pathname: string): string {
  if (pathname === "/") return "Dashboard";
  const seg = pathname.split("/").filter(Boolean)[0] ?? "";
  return seg.replace(/-/g, " ").replace(/^\w/, (c) => c.toUpperCase()) || "Dashboard";
}

function useClickOutside(ref: React.RefObject<HTMLElement | null>, onOut: () => void) {
  useEffect(() => {
    function h(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onOut();
    }
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [ref, onOut]);
}
