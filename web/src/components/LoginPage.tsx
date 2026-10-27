import { useState } from "react";
import { isMockMode } from "../api";
import { login, loginAsGuest } from "../workspaceApi";
import type { SessionUser } from "../types";
import { Spinner } from "./ui";

export function LoginPage({ onLoggedIn }: { onLoggedIn: (user: SessionUser) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const demo = isMockMode();

  async function skip() {
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await loginAsGuest());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't skip sign-in");
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await login(email.trim(), password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-grain px-4 py-10">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl border border-ink-200/80 bg-white/80 shadow-panel backdrop-blur md:grid-cols-2">
        <div className="relative hidden flex-col justify-between bg-gradient-to-br from-ink-900 via-ink-800 to-sea-600 p-10 text-white md:flex">
          <div>
            <p className="font-display text-4xl font-semibold tracking-tight">Recall</p>
            <p className="mt-3 max-w-xs text-sm leading-relaxed text-white/70">
              Walk into every meeting already knowing the person — your email, calendar and chats, merged into one spoken brief.
            </p>
          </div>
          <ul className="space-y-3 text-sm text-white/80">
            {["One contact card per person, across every app", "Ask out loud, get a sourced brief", "Your data stays yours — pull only what you choose"].map((t) => (
              <li key={t} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ember-400" />
                {t}
              </li>
            ))}
          </ul>
        </div>

        <form onSubmit={submit} className="flex flex-col justify-center p-8 md:p-10">
          <p className="font-display text-3xl font-semibold text-ink-950 md:hidden">Recall</p>
          <h1 className="mt-2 font-display text-2xl font-semibold text-ink-950 md:mt-0">Sign in</h1>
          <p className="mt-1 text-sm text-ink-500">Welcome back. Use your Recall account.</p>

          <label className="mt-8 block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-500">Email</span>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full rounded-xl border border-ink-200 bg-white px-3 py-3 text-sm outline-none ring-sea-500/40 focus:border-sea-500 focus:ring-2"
            />
          </label>
          <label className="mt-4 block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-500">Password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              className="w-full rounded-xl border border-ink-200 bg-white px-3 py-3 text-sm outline-none ring-sea-500/40 focus:border-sea-500 focus:ring-2"
            />
          </label>

          {error ? <p className="mt-3 rounded-lg bg-ember-400/10 px-3 py-2 text-sm text-ember-600">{error}</p> : null}

          <button
            type="submit"
            disabled={busy || !password}
            className="mt-6 flex items-center justify-center gap-2 rounded-xl bg-ink-900 px-4 py-3 text-sm font-semibold text-white transition hover:bg-ink-800 disabled:opacity-40"
          >
            {busy ? <Spinner /> : null}
            Sign in
          </button>
          <button
            type="button"
            onClick={() => void skip()}
            disabled={busy}
            className="mt-3 rounded-xl border border-ink-200 bg-white px-4 py-3 text-sm font-semibold text-ink-800 transition hover:border-ink-400 hover:bg-ink-50 disabled:opacity-40"
          >
            Skip sign-in · try the demo
          </button>
          <p className="mt-4 text-center text-xs text-ink-400">
            {demo ? "Demo mode: any password works." : "Team access: the API token also works as a password."}
          </p>
        </form>
      </div>
    </div>
  );
}
