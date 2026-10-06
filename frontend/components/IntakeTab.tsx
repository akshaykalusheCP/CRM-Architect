"use client";

import { useRef, useState } from "react";
import { api, ApiError, formatBytes } from "@/lib/api";
import type { Project, SheetProfile, SourceFile } from "@/lib/types";
import { AiProviderNotice } from "./AiProviderNotice";
import { CheckIcon, DocIcon, SheetIcon, SparklesIcon, TrashIcon, UploadIcon } from "./icons";
import { Badge, ErrorBox, Spinner } from "./ui";

const ACCEPT = ".csv,.tsv,.xlsx,.xlsm,.xls,.pdf,.docx,.txt,.md,.json";

function SheetSummary({ sheet }: { sheet: SheetProfile }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3 rounded-lg bg-canvas p-3 text-sm ring-1 ring-line">
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen(!open)}>
        <span>
          <span className="font-medium">{sheet.sheet}</span>
          <span className="text-ink-4"> · {sheet.row_count.toLocaleString()} rows · {sheet.column_count} columns</span>
          {sheet.header_row > 1 && <span className="text-ink-4"> · header found on row {sheet.header_row}</span>}
        </span>
        <span className="text-xs text-ink-4">{open ? "Hide" : "Columns"}</span>
      </button>
      {sheet.possible_duplicates.length > 0 && (
        <p className="mt-1 text-xs text-amber-700">
          Possible duplicates: {sheet.possible_duplicates.map((d) => `${d.duplicate_values} in ${d.column}`).join(", ")}
        </p>
      )}
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sheet.columns.map((c) => (
            <span key={c.name} className="rounded-md bg-white px-2 py-0.5 text-xs ring-1 ring-line" title={c.samples.join(", ")}>
              {c.name} <span className="text-ink-4">{c.inferred_type}</span>
              {c.fill_rate < 0.5 && <span className="text-amber-600"> {Math.round(c.fill_rate * 100)}%</span>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function IntakeTab({
  project,
  sources,
  busy,
  processing,
  onChange,
  onAnalyze,
}: {
  project: Project;
  sources: SourceFile[];
  busy: boolean;
  processing: boolean;
  onChange: () => Promise<void>;
  onAnalyze: () => Promise<void>;
}) {
  const [description, setDescription] = useState(project.description);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const dirty = description !== project.description;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api(`/projects/${project.id}`, { method: "PATCH", json: { description } });
      await onChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    setUploading(true);
    setError(null);
    try {
      await api(`/projects/${project.id}/sources`, { form });
      await onChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }

  async function remove(id: string) {
    await api(`/projects/${project.id}/sources/${id}`, { method: "DELETE" }).catch(() => {});
    await onChange();
  }

  async function analyze() {
    if (dirty) await save();
    await onAnalyze();
  }

  const canAnalyze = !busy && !processing && (description.trim().length > 0 || sources.length > 0);

  const tips = ["What they sell and to whom", "Where leads come from", "Steps from enquiry to delivery",
                "Teams and who approves what", "Exceptions and pain points"];

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="space-y-6 lg:col-span-3">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-[#EEF1F5] px-5 py-4">
            <div>
              <h2 className="font-semibold">Business narrative</h2>
              <p className="text-sm text-ink-4">Meeting notes or your own words. Rough is fine.</p>
            </div>
            <button className="btn-secondary" disabled={!dirty || saving} onClick={save}>
              {saving ? <Spinner /> : !dirty && <CheckIcon size={16} className="text-emerald-600" />}
              {dirty ? "Save" : "Saved"}
            </button>
          </div>
          <textarea
            className="block min-h-[420px] w-full resize-y border-0 px-5 py-4 text-[15px] leading-relaxed text-ink outline-none placeholder:text-ink-4"
            placeholder="e.g. We are a logistics company serving e-commerce brands. Leads come from our website and sales team…"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2 border-t border-[#EEF1F5] bg-canvas px-5 py-3">
            <span className="text-xs text-ink-4">Cover:</span>
            {tips.map((t) => (
              <span key={t} className="rounded-full bg-white px-2.5 py-0.5 text-xs text-ink-3 ring-1 ring-line">{t}</span>
            ))}
            <span className="ml-auto text-xs text-ink-4">{description.trim().split(/\s+/).filter(Boolean).length} words</span>
          </div>
        </section>
      </div>

      <div className="space-y-6 lg:col-span-2">
        <section className="card p-5">
          <button className="btn-primary w-full py-2.5 text-[15px]" disabled={!canAnalyze} onClick={analyze}>
            {busy ? <Spinner /> : <SparklesIcon size={18} />}
            {busy ? "Analysing…" : project.current_version ? "Re-analyse with latest input" : "Analyse & design CRM"}
          </button>
          <p className="mt-2 text-center text-xs text-ink-4">
            {processing
              ? "Waiting for files to finish reading…"
              : project.current_version
                ? "Creates a new design version; previous versions are kept."
                : "Produces a first design plus clarifying questions."}
          </p>
          <AiProviderNotice />
        </section>

        <section
          className={`rounded-xl border-2 border-dashed p-6 text-center transition ${
            dragging ? "border-brand-500 bg-brand-50" : "border-line-strong bg-white hover:border-brand-400"
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            upload(e.dataTransfer.files);
          }}
        >
          <span className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-brand-50 text-brand-600">
            {uploading ? <Spinner className="h-5 w-5" /> : <UploadIcon />}
          </span>
          <p className="font-medium text-ink">Drop the client&apos;s files here</p>
          <p className="mt-1 mb-4 text-sm text-ink-4">Excel/CSV exports, SOPs, quotations, forms · up to 25 MB each</p>
          <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => upload(e.target.files)} />
          <button className="btn-secondary" disabled={uploading} onClick={() => input.current?.click()}>
            Browse files
          </button>
        </section>

        <ErrorBox message={error} />

        {sources.length > 0 && (
          <section className="card divide-y divide-[#EEF1F5]">
            <p className="eyebrow px-4 py-3">{sources.length} source{sources.length === 1 ? "" : "s"}</p>
            {sources.map((s) => (
              <div key={s.id} className="group p-4">
                <div className="flex items-start gap-3">
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
                      s.kind === "tabular" ? "bg-emerald-50 text-emerald-600" : "bg-sky-50 text-sky-600"
                    }`}
                  >
                    {s.kind === "tabular" ? <SheetIcon /> : <DocIcon />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={s.filename}>{s.filename}</p>
                    <p className="text-xs text-ink-4">
                      {s.kind === "tabular" ? "Spreadsheet" : "Document"} · {formatBytes(s.size_bytes)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {s.status === "processed" && <Badge tone="green">Read</Badge>}
                    {(s.status === "uploaded" || s.status === "processing") && <Badge tone="blue">Reading…</Badge>}
                    {s.status === "failed" && <Badge tone="red">Failed</Badge>}
                    <button
                      className="rounded-md p-1.5 text-[#9AA4B2] transition hover:bg-red-50 hover:text-red-600"
                      onClick={() => remove(s.id)}
                      aria-label={`Remove ${s.filename}`}
                      title="Remove"
                    >
                      <TrashIcon size={15} />
                    </button>
                  </div>
                </div>
                {s.error && <p className="mt-2 text-xs text-red-600">{s.error}</p>}
                {s.profile?.type === "tabular" && s.profile.sheets.map((sh) => <SheetSummary key={sh.sheet} sheet={sh} />)}
                {s.profile?.type === "document" && (
                  <p className="mt-2 text-xs text-ink-4">
                    {s.profile.chars.toLocaleString()} characters extracted
                    {s.profile.truncated && <span className="text-amber-700"> · only the first part will be analysed</span>}
                  </p>
                )}
              </div>
            ))}
          </section>
        )}
      </div>
    </div>
  );
}
