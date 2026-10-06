"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";
import { LogOutIcon, PlusIcon } from "./icons";
import { Spinner } from "./ui";

const NAV = [
  { href: "/", label: "Projects", match: (p: string) => p === "/" || p.startsWith("/projects") },
  { href: "/knowledge", label: "Knowledge base", match: (p: string) => p.startsWith("/knowledge") },
  { href: "/settings", label: "AI settings", match: (p: string) => p.startsWith("/settings") },
];

export function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <span className="flex items-center justify-center rounded-xl bg-brand-600" style={{ width: size, height: size }}>
      <svg width={size * 0.56} height={size * 0.56} viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="6" cy="6" r="2.5" />
        <circle cx="18" cy="6" r="2.5" />
        <circle cx="12" cy="18" r="2.5" />
        <path d="M8 7.5l3 8M16 7.5l-3 8" />
      </svg>
    </span>
  );
}

function AccountMenu({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const initials = user.full_name.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-label={`Account: ${user.full_name}`}
        aria-expanded={open}
        className="flex h-10 w-10 items-center justify-center rounded-full bg-[#0F8F7E] text-sm font-bold text-white transition hover:ring-4 hover:ring-[#D5F0EA]"
      >
        {initials}
      </button>
      {open && (
        <div className="absolute top-12 right-0 z-40 w-72 rounded-3xl border border-line bg-white p-2 shadow-pop">
          <div className="px-4 py-3">
            <p className="font-semibold">{user.full_name}</p>
            <p className="text-sm text-ink-4">{user.email}</p>
            <p className="mt-1 text-sm text-ink-3">{user.org_name}</p>
          </div>
          <button type="button" onClick={onLogout} className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-[15px] font-medium hover:bg-chip">
            <LogOutIcon size={18} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell({ children, onNewProject }: { children: ReactNode; onNewProject?: () => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api<User>("/auth/me").then(setUser).catch(() => {});
  }, []);

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-ink-4">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line bg-white/95 backdrop-blur">
        <div className="flex h-[72px] items-center gap-4 px-4 sm:gap-7 sm:px-8">
          <Link href="/" className="flex shrink-0 items-center gap-2.5">
            <LogoMark />
            <span className="hidden text-lg font-bold tracking-tight sm:inline">CRM Architect</span>
          </Link>
          <nav className="flex gap-1">
            {NAV.map(({ href, label, match }) => {
              const active = match(pathname);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`rounded-full px-4 py-2 text-[15px] transition ${
                    active ? "bg-brand-100 font-semibold text-brand-900" : "font-medium text-ink-3 hover:bg-chip hover:text-ink"
                  }`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <div className="flex-1" />
          <button type="button" className="btn-primary hidden sm:inline-flex" onClick={() => (onNewProject ? onNewProject() : router.push("/?new=1"))}>
            <PlusIcon size={18} strokeWidth={2.2} /> New project
          </button>
          <AccountMenu user={user} onLogout={logout} />
        </div>
      </header>
      <main className="mx-auto max-w-[1280px] px-4 py-8 sm:px-8">{children}</main>
    </div>
  );
}
