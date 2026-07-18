"use client";

import { useEffect, useState } from "react";
import { API, authStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icon";

const HIGHLIGHTS = [
  ["sparkles", "Photos to storyboard", "Upload product shots, get a scene-by-scene ad plan in ~30 seconds."],
  ["film", "Generate & render", "Images, video and voiceover — assembled into a 9:16 ad."],
  ["analytics", "Know your spend", "Every stage is metered, down to the cost of each finished video."],
] as const;

export default function Login() {
  const [mode, setMode] = useState<"login" | "register" | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    authStatus()
      .then((s) => {
        if (s.authenticated) { window.location.href = "/"; return; }
        setMode(s.registered ? "login" : "register");
      })
      .catch(() => setMode("login"));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const r = await fetch(`${API}/auth/${mode}`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || "Something went wrong");
      }
      window.location.href = "/";
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      {/* brand panel */}
      <div className="relative hidden overflow-hidden bg-gradient-to-br from-violet-700 via-violet-600 to-fuchsia-700 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-white/10 blur-3xl" />
        <div className="absolute -bottom-32 -left-16 h-96 w-96 rounded-full bg-black/20 blur-3xl" />
        <div className="relative flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/15 text-base font-bold backdrop-blur">A</span>
          <span className="text-lg font-semibold tracking-tight">AI Ad Studio</span>
        </div>
        <div className="relative space-y-8">
          <div>
            <h2 className="text-3xl font-semibold leading-tight tracking-tight">
              Product photos in,<br />platform-ready ads out.
            </h2>
            <p className="mt-3 max-w-sm text-white/80">
              The AI creative suite that turns a handful of product shots into a finished vertical video ad.
            </p>
          </div>
          <ul className="space-y-5">
            {HIGHLIGHTS.map(([icon, title, body]) => (
              <li key={title} className="flex gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/15 backdrop-blur">
                  <Icon name={icon} size={17} />
                </span>
                <div>
                  <p className="text-sm font-semibold">{title}</p>
                  <p className="max-w-xs text-sm text-white/75">{body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-white/60">v0.1 · single-user beta</p>
      </div>

      {/* form panel */}
      <div className="flex items-center justify-center bg-bg p-6">
        <div className="w-full max-w-sm animate-slide-up space-y-6">
          <div className="flex items-center gap-2.5 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 text-base font-bold text-white">A</span>
            <span className="text-lg font-semibold tracking-tight">AI Ad Studio</span>
          </div>

          {mode === null ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <span className="h-4 w-4 rounded-full border-2 border-accent border-t-transparent motion-safe:animate-spin" />
              Loading…
            </div>
          ) : (
            <>
              <div>
                <h1 className="text-h1">{mode === "register" ? "Create your owner account" : "Welcome back"}</h1>
                <p className="mt-1 text-secondary">
                  {mode === "register"
                    ? "This is the first account — it becomes the studio owner."
                    : "Log in to your studio."}
                </p>
              </div>
              <form onSubmit={submit} className="space-y-4">
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-muted">Email</span>
                  <input type="email" required autoComplete="username" value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="focus-ring w-full rounded-xl border border-line bg-surface px-3 py-2.5 text-sm" />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-muted">Password</span>
                  <input type="password" required minLength={8}
                    autoComplete={mode === "register" ? "new-password" : "current-password"}
                    value={password} onChange={(e) => setPassword(e.target.value)}
                    className="focus-ring w-full rounded-xl border border-line bg-surface px-3 py-2.5 text-sm" />
                  {mode === "register" && <span className="mt-1 block text-[11px] text-muted">At least 8 characters.</span>}
                </label>
                {error && (
                  <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>
                )}
                <Button variant="ai" size="lg" className="w-full" disabled={busy}>
                  {busy ? "…" : mode === "register" ? "Create account" : "Log in"}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
