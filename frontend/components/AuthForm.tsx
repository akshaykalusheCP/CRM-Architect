"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { CheckIcon } from "./icons";
import { ErrorBox, Spinner } from "./ui";

const POINTS = [
  "Reads messy spreadsheets, SOPs and notes",
  "Designs objects, pipelines, automations and access",
  "Asks the questions a senior consultant would",
  "Generates a deployable Salesforce package",
];

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const [form, setForm] = useState({ org_name: "", full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = mode === "login" ? { email: form.email, password: form.password } : form;
      await api(`/auth/${mode}`, { json: body });
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden p-4 lg:block">
        <div className="relative flex h-full flex-col justify-between overflow-hidden rounded-[36px] bg-brand-600 p-12 text-white">
          <div className="pointer-events-none absolute -top-24 -right-24 h-80 w-80 rounded-full bg-white/10" />
          <div className="pointer-events-none absolute -bottom-28 -left-16 h-72 w-72 rounded-full bg-[#0F8F7E]/50" />
          <div className="relative flex items-center gap-2.5">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#1F5EDB" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="M8 7.5l3 8M16 7.5l-3 8" />
              </svg>
            </span>
            <span className="text-lg font-bold">CRM Architect</span>
          </div>
          <div className="relative max-w-md">
            <h2 className="text-[34px] leading-tight font-bold tracking-tight">From scattered business data to a CRM design in minutes.</h2>
            <ul className="mt-8 space-y-3.5">
              {POINTS.map((p) => (
                <li key={p} className="flex items-center gap-3 text-[17px] text-white/90">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/20">
                    <CheckIcon size={15} strokeWidth={2.6} />
                  </span>
                  {p}
                </li>
              ))}
            </ul>
          </div>
          <p className="relative text-sm text-white/80">Salesforce today · Zoho, Odoo and HubSpot next</p>
        </div>
      </div>

      <div className="flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            <h1 className="text-[30px] font-bold tracking-tight">{mode === "login" ? "Welcome back" : "Create your workspace"}</h1>
            <p className="mt-1 text-sm text-ink-4">
              {mode === "login" ? "Sign in to continue to your projects." : "Set up your consultancy's workspace."}
            </p>
          </div>
          <form onSubmit={submit} className="space-y-4">
            {mode === "register" && (
              <>
                <div>
                  <label className="label" htmlFor="org">Company</label>
                  <input id="org" className="input" required minLength={2} value={form.org_name} onChange={set("org_name")} />
                </div>
                <div>
                  <label className="label" htmlFor="name">Your name</label>
                  <input id="name" className="input" required value={form.full_name} onChange={set("full_name")} />
                </div>
              </>
            )}
            <div>
              <label className="label" htmlFor="email">Work email</label>
              <input id="email" type="email" className="input" required autoComplete="email" value={form.email} onChange={set("email")} />
            </div>
            <div>
              <label className="label" htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                className="input"
                required
                minLength={mode === "register" ? 10 : 1}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={form.password}
                onChange={set("password")}
              />
              {mode === "register" && <p className="mt-1.5 text-xs text-ink-4">At least 10 characters.</p>}
            </div>
            <ErrorBox message={error} />
            <button className="btn-primary h-12 w-full" disabled={busy}>
              {busy && <Spinner />}
              {mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>
          <p className="mt-6 text-center text-sm text-ink-3">
            {mode === "login" ? (
              <>New here? <Link className="font-medium text-brand-600 hover:underline" href="/register">Create a workspace</Link></>
            ) : (
              <>Already registered? <Link className="font-medium text-brand-600 hover:underline" href="/login">Sign in</Link></>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}
