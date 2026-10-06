"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, timeAgo } from "@/lib/api";
import type { Export, Platform, Project } from "@/lib/types";
import { AlertIcon, BoxIcon, CheckIcon, DocIcon, DownloadIcon, ListIcon } from "./icons";
import { ErrorBox, Spinner } from "./ui";

function ExportCard({ e, projectId }: { e: Export; projectId: string }) {
  const [open, setOpen] = useState(false);
  const warnings = e.report.warnings ?? [];
  const steps = e.report.manual_steps ?? [];
  const counts = e.report.counts ?? {};
  const isDoc = e.kind === "design_doc";
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-4 p-4">
        <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${isDoc ? "bg-sky-50 text-sky-600" : "bg-brand-50 text-brand-600"}`}>
          {isDoc ? <DocIcon /> : <BoxIcon />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-ink">{e.filename}</p>
          <p className="text-xs text-ink-4">Design version {e.version} · {timeAgo(e.created_at)}</p>
        </div>
        <a className="btn-secondary" href={`/api/projects/${projectId}/exports/${e.id}/download`}>
          <DownloadIcon size={16} /> Download
        </a>
      </div>
      {(Object.keys(counts).length > 0 || warnings.length > 0 || steps.length > 0) && (
        <div className="border-t border-[#EEF1F5] bg-canvas px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-ink-3">
            {Object.entries(counts).map(([k, v]) => (
              <span key={k}><span className="font-semibold text-ink">{v}</span> {k.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase()}</span>
            ))}
            {(warnings.length > 0 || steps.length > 0) && (
              <button className="ml-auto font-medium text-brand-600 hover:underline" onClick={() => setOpen(!open)}>
                {open ? "Hide" : "Show"} {warnings.length} warnings · {steps.length} manual steps
              </button>
            )}
          </div>
          {open && (
            <div className="mt-4 grid gap-6 pb-1 text-sm md:grid-cols-2">
              {warnings.length > 0 && (
                <div>
                  <p className="mb-2 flex items-center gap-1.5 font-medium text-amber-800"><AlertIcon size={15} /> Warnings</p>
                  <ul className="space-y-1.5 text-ink-2">{warnings.map((w, i) => <li key={i} className="leading-snug">{w}</li>)}</ul>
                </div>
              )}
              {steps.length > 0 && (
                <div>
                  <p className="mb-2 flex items-center gap-1.5 font-medium text-ink"><ListIcon size={15} /> After deploying</p>
                  <ul className="space-y-1.5 text-ink-2">
                    {steps.map((s, i) => (
                      <li key={i} className="flex gap-2 leading-snug">
                        <span className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border border-line-strong bg-white" />
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BuildOption({
  icon,
  title,
  description,
  bullets,
  action,
}: {
  icon: ReactNode;
  title: string;
  description: ReactNode;
  bullets: string[];
  action: ReactNode;
}) {
  return (
    <div className="card flex flex-col p-6">
      <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-brand-50 text-brand-600">{icon}</span>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-1 text-sm text-ink-4">{description}</p>
      <ul className="mt-4 mb-6 space-y-1.5 text-sm text-ink-3">
        {bullets.map((b) => (
          <li key={b} className="flex items-start gap-2">
            <CheckIcon size={15} className="mt-0.5 shrink-0 text-emerald-500" />
            {b}
          </li>
        ))}
      </ul>
      <div className="mt-auto">{action}</div>
    </div>
  );
}

export function ExportTab({ project }: { project: Project }) {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [exports, setExports] = useState<Export[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api<Export[]>(`/projects/${project.id}/exports`).then(setExports), [project.id]);
  useEffect(() => {
    api<Platform[]>("/platforms").then(setPlatforms).catch(() => {});
    load().catch(() => {});
  }, [load]);

  async function generate(kind: string) {
    setBusy(kind);
    setError(null);
    try {
      await api(`/projects/${project.id}/exports`, { json: { kind } });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }

  const target = platforms.find((p) => p.key === project.target_platform);
  const v = project.current_version;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2">
        <BuildOption
          icon={<BoxIcon />}
          title={`${target?.label ?? project.target_platform} build package`}
          description={target?.available ? "A deployable project for the target CRM, generated from the design." : "Metadata generation for this CRM is coming soon."}
          bullets={
            target?.available
              ? ["Objects, fields, layouts and tabs", "Roles, permission sets, pipeline stages", "Automations as draft Flows", "Data migration templates and load order"]
              : ["Use the solution design document meanwhile"]
          }
          action={
            target?.available ? (
              <button className="btn-primary w-full" disabled={!!busy} onClick={() => generate(target.key)}>
                {busy === target.key ? <Spinner /> : <BoxIcon size={16} />} Generate package from v{v}
              </button>
            ) : (
              <button className="btn-secondary w-full" disabled>Coming soon</button>
            )
          }
        />
        <BuildOption
          icon={<DocIcon />}
          title="Solution design document"
          description="A platform-neutral write-up for client sign-off."
          bullets={["Business profile, data model and pipelines", "Automations, roles and integrations", "Migration mapping, assumptions, open questions"]}
          action={
            <button className="btn-secondary w-full" disabled={!!busy} onClick={() => generate("design_doc")}>
              {busy === "design_doc" ? <Spinner /> : <DocIcon size={16} />} Generate document from v{v}
            </button>
          }
        />
      </div>
      <ErrorBox message={error} />
      {exports.length > 0 && (
        <div className="space-y-3">
          <p className="eyebrow">Generated files</p>
          {exports.map((e) => <ExportCard key={e.id} e={e} projectId={project.id} />)}
        </div>
      )}
    </div>
  );
}
