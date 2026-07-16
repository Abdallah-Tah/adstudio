"use client";

import { useEffect, useState } from "react";
import { API, authStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

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
    setBusy(true);
    setError("");
    try {
      const r = await fetch(`${API}/auth/${mode}`, {
        method: "POST",
        credentials: "include",
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
    <div className="flex min-h-dvh items-center justify-center bg-bg p-6">
      <Card className="w-full max-w-sm space-y-6 p-8">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-accent-ink">A</span>
          <span className="text-base font-semibold tracking-tight">AI Ad Studio</span>
        </div>
        {mode === null ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : (
          <>
            <div>
              <h1 className="text-lg font-semibold">
                {mode === "register" ? "Create your owner account" : "Welcome back"}
              </h1>
              <p className="mt-1 text-sm text-muted">
                {mode === "register"
                  ? "This is the first account — it becomes the studio owner."
                  : "Log in to your studio."}
              </p>
            </div>
            <form onSubmit={submit} className="space-y-3">
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-muted">Email</span>
                <input
                  type="email" required autoComplete="username"
                  value={email} onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm focus:border-accent focus:outline-none"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-muted">Password</span>
                <input
                  type="password" required minLength={8}
                  autoComplete={mode === "register" ? "new-password" : "current-password"}
                  value={password} onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm focus:border-accent focus:outline-none"
                />
                {mode === "register" && (
                  <span className="mt-1 block text-[11px] text-muted">At least 8 characters.</span>
                )}
              </label>
              {error && <p className="text-sm text-red-500">{error}</p>}
              <Button variant="accent" size="lg" className="w-full" disabled={busy}>
                {busy ? "…" : mode === "register" ? "Create account" : "Log in"}
              </Button>
            </form>
          </>
        )}
      </Card>
    </div>
  );
}
