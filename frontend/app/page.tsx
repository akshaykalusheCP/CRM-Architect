"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ArrowRightIcon, PlusIcon, QuestionIcon, SearchIcon } from "@/components/icons";
import { Avatar, ErrorBox, Modal, Spinner, StatusBadge } from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";
import type { Platform, Project, User } from "@/lib/types";

type Filter = "all" | "needs_input" | "ready" | "draft";

/** Four journey segments: done (green), current (blue tint), upcoming (grey). */
function journey(p: Project): { segments: ("done" | "current" | "todo")[]; label: string; cta: string } {
  if (!p.current_version) {
    return { segments: ["current", "todo", "todo", "todo"], label: "Business input · add a description or files", cta: "Add input" };
  }
  if (p.status === "needs_input") {
    return { segments: ["done", "current", "done", "todo"], label: "Clarifications · questions waiting", cta: "Answer questions" };
  }
  if (p.status === "analyzing") {
    return { segments: ["done", "done", "current", "todo"], label: "CRM design · AI is working", cta: "View progress" };
  }
  return { segments: ["done", "done", "done", "current"], label: "Build · ready to generate", cta: "Open design" };
}

const SEG = { done: "bg-[#1E8E3E]", current: "bg-brand-400", todo: "bg-line" };

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function NewProjectForm({ platforms, onCancel }: { platforms: Platform[]; onCancel: () => void }) {
  const router = useRouter();
  const [form, setForm] = useState({ name: "", client_name: "", industry: "", target_platform: "salesforce", description: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const p = await api<Project>("/projects", { json: { ...form, industry: form.industry || null } });
      router.push(`/projects/${p.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create project");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="client">Client</label>
          <input id="client" className="input" required value={form.client_name} onChange={set("client_name")} placeholder="SunPeak Solar" autoFocus />
        </div>
        <div>
          <label className="label" htmlFor="industry">Industry</label>
          <input id="industry" className="input" value={form.industry} onChange={set("industry")} placeholder="Renewable energy" />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="pname">Project name</label>
        <input id="pname" className="input" required value={form.name} onChange={set("name")} placeholder="CRM rollout 2026" />
      </div>
      <fieldset>
        <legend className="label">Target CRM</legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {platforms.map((p) => {
            const active = form.target_platform === p.key;
            return (
              <label
                key={p.key}
                className={`cursor-pointer rounded-2xl border px-4 py-3 transition ${
                  active ? "border-2 border-brand-600 bg-brand-50" : "border-line-strong bg-white hover:bg-chip"
                }`}
              >
                <input type="radio" name="platform" className="sr-only" checked={active} onChange={() => setForm({ ...form, target_platform: p.key })} />
                <span className={`block text-[15px] font-semibold ${active ? "text-brand-900" : ""}`}>{p.label}</span>
                <span className="text-[13px] text-ink-4">{p.available ? "Full build" : "Design only"}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <div>
        <label className="label" htmlFor="desc">How does the business work?</label>
        <textarea
          id="desc"
          className="input min-h-32 leading-relaxed"
          value={form.description}
          onChange={set("description")}
          placeholder="What they sell, to whom, where leads come from, the steps from enquiry to delivery, the teams involved, what is painful today"
        />
        <p className="mt-1.5 text-sm text-ink-4">Rough notes are fine. You can add spreadsheets and documents next.</p>
      </div>
      <ErrorBox message={error} />
      <div className="flex justify-end gap-2 pt-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={busy}>{busy && <Spinner />} Create project</button>
      </div>
    </form>
  );
}

function ProjectsHome() {
  const router = useRouter();
  const params = useSearchParams();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const creating = params.get("new") === "1";
  const setCreating = (open: boolean) => router.replace(open ? "/?new=1" : "/", { scroll: false });

  useEffect(() => {
    Promise.all([api<Project[]>("/projects"), api<Platform[]>("/platforms"), api<User>("/auth/me")])
      .then(([p, pl, u]) => {
        setProjects(p);
        setPlatforms(pl);
        setUser(u);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load projects"));
  }, []);

  const counts = useMemo(() => {
    const list = projects ?? [];
    return {
      all: list.length,
      needs_input: list.filter((p) => p.status === "needs_input").length,
      ready: list.filter((p) => p.status === "ready").length,
      draft: list.filter((p) => !p.current_version).length,
    };
  }, [projects]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (projects ?? []).filter((p) => {
      if (filter === "needs_input" && p.status !== "needs_input") return false;
      if (filter === "ready" && p.status !== "ready") return false;
      if (filter === "draft" && p.current_version) return false;
      return !q || [p.name, p.client_name, p.industry ?? ""].some((v) => v.toLowerCase().includes(q));
    });
  }, [projects, filter, query]);

  const waiting = (projects ?? []).find((p) => p.status === "needs_input");
  const platformLabel = (key: string) => platforms.find((x) => x.key === key)?.label ?? key;
  const firstName = user?.full_name.split(/\s+/)[0] ?? "";

  const filters: [Filter, string][] = [
    ["all", `All · ${counts.all}`],
    ["needs_input", `Needs answers · ${counts.needs_input}`],
    ["ready", `Ready to build · ${counts.ready}`],
    ["draft", `Draft · ${counts.draft}`],
  ];

  return (
    <AppShell onNewProject={() => setCreating(true)}>
      <div className="mx-auto max-w-[1200px]">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[34px] leading-tight font-bold tracking-tight">
              {greeting()}{firstName ? `, ${firstName}` : ""}
            </h1>
            <p className="mt-1.5 text-[17px] text-ink-3">
              {projects === null
                ? "Loading your projects…"
                : counts.needs_input
                  ? `${counts.needs_input} project${counts.needs_input > 1 ? "s are" : " is"} waiting on client answers.`
                  : projects.length
                    ? "Everything is up to date."
                    : "Start by creating your first client project."}
            </p>
          </div>
          {!!projects?.length && (
            <div className="flex flex-wrap gap-2">
              {filters.map(([key, label]) => (
                <button key={key} type="button" onClick={() => setFilter(key)} className={`chip ${filter === key ? "chip-active" : ""}`}>
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>

        <ErrorBox message={error} />

        {waiting && filter === "all" && !query && (
          <section className="mb-7 flex flex-wrap items-center gap-6 rounded-[28px] bg-brand-100 px-8 py-7">
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-white text-brand-600">
              <QuestionIcon size={26} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-bold tracking-[0.06em] text-brand-900 uppercase">Pick up where you left off</p>
              <p className="mt-1 text-[21px] font-semibold text-brand-900">{waiting.client_name} has clarifying questions waiting</p>
              <p className="mt-1 text-[15px] text-[#27407A]">
                Answering them lets the next refinement fit the design to how they actually work.
              </p>
            </div>
            <Link href={`/projects/${waiting.id}`} className="btn-primary h-12 px-6">
              Answer questions <ArrowRightIcon size={18} strokeWidth={2.2} />
            </Link>
          </section>
        )}

        {projects === null && !error ? (
          <div className="flex justify-center py-20 text-ink-4"><Spinner className="h-6 w-6" /></div>
        ) : (
          <>
            {!!projects?.length && (
              <label className="mb-5 flex h-12 max-w-md items-center gap-3 rounded-full bg-white px-5 text-ink-4 ring-1 ring-line focus-within:ring-2 focus-within:ring-brand-600">
                <SearchIcon size={18} />
                <input
                  type="search"
                  aria-label="Search projects and clients"
                  placeholder="Search projects and clients"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="h-full flex-1 bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-4"
                />
              </label>
            )}
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {visible.map((p) => {
                const j = journey(p);
                return (
                  <Link
                    key={p.id}
                    href={`/projects/${p.id}`}
                    className="group flex flex-col gap-5 rounded-3xl border border-line bg-white p-6 transition hover:border-brand-200 hover:shadow-pop"
                  >
                    <div className="flex items-start gap-3.5">
                      <Avatar name={p.client_name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-lg font-bold">{p.name}</p>
                        <p className="truncate text-sm text-ink-3">
                          {p.client_name}
                          {p.industry ? ` · ${p.industry}` : ""}
                        </p>
                      </div>
                      <StatusBadge status={p.status} />
                    </div>
                    <div className="space-y-2">
                      <div className="flex gap-1.5">
                        {j.segments.map((s, i) => (
                          <span key={i} className={`h-2 flex-1 rounded-full ${SEG[s]}`} />
                        ))}
                      </div>
                      <p className="text-sm text-ink-3">{j.label}</p>
                    </div>
                    <div className="mt-auto flex items-center justify-between border-t border-[#EEF1F5] pt-4 text-sm">
                      <span className="text-ink-4">
                        {platformLabel(p.target_platform)} · {p.current_version ? `Design v${p.current_version}` : "No design yet"} ·{" "}
                        {timeAgo(p.updated_at)}
                      </span>
                      <span className="flex items-center gap-1 font-semibold text-brand-600">
                        {j.cta}
                        <ArrowRightIcon size={15} className="transition group-hover:translate-x-0.5" />
                      </span>
                    </div>
                  </Link>
                );
              })}
              {filter === "all" && !query && (
                <button
                  type="button"
                  onClick={() => setCreating(true)}
                  className="flex min-h-[214px] flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed border-[#C9D2E0] text-base font-semibold text-ink-3 transition hover:border-brand-400 hover:bg-white hover:text-brand-700"
                >
                  <span className="flex h-12 w-12 items-center justify-center rounded-full border border-line bg-white text-brand-600">
                    <PlusIcon size={22} strokeWidth={2.2} />
                  </span>
                  Start a client project
                </button>
              )}
            </div>
            {!!projects?.length && visible.length === 0 && (
              <p className="py-12 text-center text-ink-3">No projects match this filter.</p>
            )}
          </>
        )}
      </div>

      <Modal open={creating} title="New client project" onClose={() => setCreating(false)} wide>
        <NewProjectForm platforms={platforms} onCancel={() => setCreating(false)} />
      </Modal>
    </AppShell>
  );
}

export default function Page() {
  return (
    <Suspense>
      <ProjectsHome />
    </Suspense>
  );
}
