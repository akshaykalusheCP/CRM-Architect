"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { BusinessModel, Project, Question, Version } from "@/lib/types";
import { runEstimate, useActiveProvider } from "./AiProviderNotice";
import { ArrowRightIcon, ChatQuestionIcon, CheckIcon, InfoIcon, SparklesIcon } from "./icons";
import { Badge, Empty, ErrorBox, ProgressRing, Spinner, type Tone } from "./ui";

type Filter = "all" | "open" | "handled";

const GROUPS: { label: string; categories: string[] }[] = [
  { label: "Business", categories: ["business", "scope"] },
  { label: "Process & automation", categories: ["process", "automation"] },
  { label: "Data & records", categories: ["entity", "field", "data"] },
  { label: "Team & access", categories: ["security"] },
  { label: "Systems", categories: ["integration"] },
];

const CATEGORY_LABEL: Record<string, string> = {
  business: "Business",
  scope: "Scope",
  process: "Process",
  automation: "Automation",
  entity: "Records",
  field: "Fields",
  data: "Data",
  security: "Team & access",
  integration: "Systems",
};

const IMPACT: Record<Question["priority"], { tone: Tone; label: string }> = {
  high: { tone: "amber", label: "High impact" },
  medium: { tone: "gray", label: "Medium impact" },
  low: { tone: "gray", label: "Low impact" },
};

const pretty = (s: string) => s.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());

/** Split a saved answer back into the suggested option it started with and the extra detail. */
function splitAnswer(q: Question): { option: string | null; detail: string } {
  const answer = q.answer ?? "";
  const option = q.suggested_answers.find((s) => answer === s || answer.startsWith(`${s}. `));
  if (option) return { option, detail: answer.slice(option.length).replace(/^\.\s*/, "") };
  return { option: null, detail: answer };
}

function StatusDot({ q, active }: { q: Question; active: boolean }) {
  if (q.status === "answered")
    return (
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#1E8E3E] text-white">
        <CheckIcon size={12} strokeWidth={3.5} />
      </span>
    );
  if (q.status === "dismissed")
    return <span className="mt-0.5 h-5 w-5 shrink-0 rounded-full border-2 border-dashed border-[#9AA4B2]" />;
  return (
    <span
      className={`mt-0.5 h-5 w-5 shrink-0 rounded-full ${active ? "border-[6px] border-brand-600 bg-white" : "border-2 border-[#9AA4B2]"}`}
    />
  );
}

function QuestionPanel({
  q,
  index,
  total,
  labels,
  projectId,
  onSaved,
  onPrevious,
}: {
  q: Question;
  index: number;
  total: number;
  labels: Record<string, string>;
  projectId: string;
  onSaved: (advance: boolean) => Promise<void>;
  onPrevious: (() => void) | null;
}) {
  const initial = splitAnswer(q);
  const [option, setOption] = useState<string | null>(initial.option);
  const [detail, setDetail] = useState(initial.detail);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasOptions = q.suggested_answers.length > 0;
  const answer = [option, detail.trim()].filter(Boolean).join(". ");
  const impact = IMPACT[q.priority];

  async function patch(body: object, action: string, advance: boolean) {
    setBusy(action);
    setError(null);
    try {
      await api(`/projects/${projectId}/questions/${q.id}`, { method: "PATCH", json: body });
      await onSaved(advance);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="flex flex-col gap-7 rounded-[32px] border border-line bg-white px-6 py-8 sm:px-11 sm:py-10">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-sm font-semibold text-ink-3">Question {index} of {total}</span>
        <span className="h-1 w-1 rounded-full bg-[#9AA4B2]" />
        <Badge>{CATEGORY_LABEL[q.category] ?? pretty(q.category)}</Badge>
        <Badge tone={impact.tone}>{impact.label}</Badge>
        {q.status === "answered" && <Badge tone="green"><CheckIcon size={13} strokeWidth={3} /> Answered</Badge>}
        {q.status === "dismissed" && <Badge>Marked not relevant</Badge>}
      </div>

      <h2 className="max-w-3xl text-[28px] leading-[1.25] font-bold tracking-tight">{q.text}</h2>

      <div className="flex gap-3.5 rounded-[20px] bg-canvas px-5 py-4">
        <InfoIcon size={21} className="mt-0.5 shrink-0 text-brand-600" />
        <div className="space-y-2.5">
          <p className="text-[15px] leading-relaxed text-ink-2">{q.why}</p>
          {q.related.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-semibold text-ink-3">Affects</span>
              {q.related.map((r) => (
                <span key={r} className="rounded-full border border-line-strong bg-white px-3 py-1 text-[13px]">
                  {labels[r] ?? pretty(r)}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {hasOptions && (
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-3 text-[15px] font-semibold">Choose the closest answer</legend>
          {q.suggested_answers.map((s) => {
            const selected = option === s;
            return (
              <label
                key={s}
                className={`flex cursor-pointer items-center gap-4 rounded-[20px] px-5 py-4 transition ${
                  selected ? "border-2 border-brand-600 bg-brand-50" : "border border-line-strong bg-white hover:bg-chip"
                }`}
              >
                <input
                  type="radio"
                  name={`q-${q.id}`}
                  checked={selected}
                  onChange={() => setOption(s)}
                  onClick={() => selected && setOption(null)}
                  className="h-5 w-5 shrink-0 accent-brand-600"
                />
                <span className={`text-base ${selected ? "font-semibold text-brand-900" : "font-medium"}`}>{s}</span>
              </label>
            );
          })}
        </fieldset>
      )}

      <label className="flex flex-col gap-2">
        <span className="text-[15px] font-semibold">
          {hasOptions ? (
            <>Add detail <span className="font-normal text-ink-4">(optional)</span></>
          ) : (
            "Your answer"
          )}
        </span>
        <textarea
          rows={3}
          value={detail}
          onChange={(e) => setDetail(e.target.value)}
          placeholder={hasOptions ? "Anything specific: exceptions, who is involved, examples" : "Answer in the client's words"}
          className="input resize-none leading-relaxed"
        />
      </label>

      <ErrorBox message={error} />

      <div className="flex flex-wrap items-center gap-3">
        {q.status === "dismissed" ? (
          <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => patch({ status: "open" }, "reopen", false)}>
            Reopen question
          </button>
        ) : (
          <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => patch({ status: "dismissed" }, "dismiss", true)}>
            {busy === "dismiss" && <Spinner />} Not relevant
          </button>
        )}
        <span className="flex-1" />
        {onPrevious && (
          <button type="button" className="btn-secondary" onClick={onPrevious}>Previous</button>
        )}
        <button type="button" className="btn-primary h-12 px-7" disabled={!!busy || !answer} onClick={() => patch({ answer }, "save", true)}>
          {busy === "save" ? <Spinner /> : null}
          {q.status === "answered" ? "Update and next" : "Save and next"}
          <ArrowRightIcon size={18} strokeWidth={2.2} />
        </button>
      </div>
    </section>
  );
}

export function QuestionsTab({
  project,
  questions,
  busy,
  onChange,
  onAnalyze,
}: {
  project: Project;
  questions: Question[];
  busy: boolean;
  onChange: () => Promise<void>;
  onAnalyze: () => Promise<void>;
}) {
  const provider = useActiveProvider();
  const [filter, setFilter] = useState<Filter>("all");
  const [model, setModel] = useState<BusinessModel | null>(null);
  const visible = questions.filter((q) => q.status !== "resolved");
  const handled = visible.filter((q) => q.status === "answered" || q.status === "dismissed");
  const openQs = visible.filter((q) => q.status === "open");
  const pending = visible.filter((q) => q.status === "answered" && q.applied_in_version === null);

  // Questions in display order (grouped), so Previous / Next follow what the user sees.
  const ordered = useMemo(() => {
    const known = new Set(GROUPS.flatMap((g) => g.categories));
    const groups = [...GROUPS, { label: "Other", categories: [...new Set(visible.map((q) => q.category))].filter((c) => !known.has(c)) }];
    return groups
      .map((g) => ({ label: g.label, items: visible.filter((q) => g.categories.includes(q.category)) }))
      .filter((g) => g.items.length);
  }, [visible]);
  const flat = ordered.flatMap((g) => g.items);

  const [activeId, setActiveId] = useState<string | null>(null);
  useEffect(() => {
    if (activeId && flat.some((q) => q.id === activeId)) return;
    setActiveId((flat.find((q) => q.status === "open") ?? flat[0])?.id ?? null);
  }, [flat, activeId]);

  useEffect(() => {
    if (!project.current_version) return;
    api<Version>(`/projects/${project.id}/model`).then((v) => setModel(v.model)).catch(() => {});
  }, [project.id, project.current_version]);

  const labels = useMemo(() => {
    const out: Record<string, string> = {};
    if (!model) return out;
    for (const e of model.entities) out[e.key] = e.label;
    for (const p of model.processes) out[p.key] = p.name;
    for (const a of model.automations) out[a.key] = a.name;
    for (const r of model.roles) out[r.key] = r.label;
    for (const i of model.integrations) out[i.key] = i.system;
    return out;
  }, [model]);

  if (!visible.length) {
    return (
      <Empty title="No questions yet" icon={<ChatQuestionIcon />}>
        Run an analysis. The AI asks about anything it can&apos;t work out from your input.
      </Empty>
    );
  }

  const activeIndex = flat.findIndex((q) => q.id === activeId);
  const active = activeIndex >= 0 ? flat[activeIndex] : null;

  async function afterSave(advance: boolean) {
    await onChange();
    if (!advance || activeIndex < 0) return;
    // Next open question after this one, wrapping around.
    const rest = [...flat.slice(activeIndex + 1), ...flat.slice(0, activeIndex)];
    const next = rest.find((q) => q.status === "open");
    if (next) setActiveId(next.id);
  }

  const shown = (q: Question) =>
    filter === "all" || (filter === "open" ? q.status === "open" : q.status !== "open");

  return (
    <div className="pb-28">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <aside className="order-2 flex w-full shrink-0 flex-col gap-4 rounded-[28px] border border-line bg-white p-5 lg:sticky lg:top-24 lg:order-1 lg:w-[380px]">
          <div className="flex items-center gap-3.5 px-1 pt-1">
            <ProgressRing done={handled.length} total={visible.length} />
            <div>
              <p className="text-lg font-bold">{handled.length} of {visible.length} answered</p>
              <p className="text-sm text-ink-3">Skip anything that doesn&apos;t apply</p>
            </div>
          </div>
          <div className="flex gap-1.5">
            {([["all", `All ${visible.length}`], ["open", `Open ${openQs.length}`], ["handled", `Answered ${handled.length}`]] as const).map(
              ([key, label]) => (
                <button key={key} type="button" onClick={() => setFilter(key)} className={`chip h-8 px-3.5 text-[13px] ${filter === key ? "chip-active" : ""}`}>
                  {label}
                </button>
              ),
            )}
          </div>
          <div className="flex flex-col">
            {ordered.map((g) => {
              const items = g.items.filter(shown);
              if (!items.length) return null;
              return (
                <div key={g.label} className="flex flex-col gap-0.5">
                  <p className="eyebrow px-2 pt-3 pb-1.5">{g.label}</p>
                  {items.map((q) => {
                    const isActive = q.id === activeId;
                    return (
                      <button
                        key={q.id}
                        type="button"
                        onClick={() => setActiveId(q.id)}
                        className={`flex items-start gap-3 rounded-2xl p-3 text-left transition ${
                          isActive ? "bg-brand-100 text-brand-900" : q.status === "open" ? "hover:bg-chip" : "text-ink-3 hover:bg-chip"
                        }`}
                      >
                        <StatusDot q={q} active={isActive} />
                        <span className={`line-clamp-2 text-sm leading-snug ${isActive ? "font-semibold" : ""}`}>{q.text}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </aside>

        <div className="order-1 min-w-0 flex-1 lg:order-2">
          {active && (
            <QuestionPanel
              key={active.id + active.status + (active.answer ?? "")}
              q={active}
              index={activeIndex + 1}
              total={flat.length}
              labels={labels}
              projectId={project.id}
              onSaved={afterSave}
              onPrevious={activeIndex > 0 ? () => setActiveId(flat[activeIndex - 1].id) : null}
            />
          )}
        </div>
      </div>

      <div className="pointer-events-none fixed inset-x-0 bottom-6 z-20 flex justify-center px-4">
        <div className="pointer-events-auto flex max-w-full flex-wrap items-center gap-x-5 gap-y-2 rounded-full bg-ink py-2.5 pr-2.5 pl-7 text-white shadow-pop">
          {provider?.key === "mock" ? (
            <span className="text-[15px]">
              {pending.length ? `${pending.length} answer${pending.length === 1 ? "" : "s"} ready · ` : ""}No AI configured.{" "}
              <Link href="/settings" className="font-semibold underline">Set up a provider</Link>
            </span>
          ) : pending.length ? (
            <>
              <span className="text-[15px]">
                <span className="font-bold">{pending.length} answer{pending.length === 1 ? "" : "s"}</span> ready to apply to the design
              </span>
              <span className="hidden text-sm text-[#C3CAD6] sm:inline">{runEstimate(provider)}</span>
            </>
          ) : (
            <span className="text-[15px]">Answer questions, then refine the design with them</span>
          )}
          <button
            type="button"
            onClick={onAnalyze}
            disabled={busy || !pending.length}
            className="btn h-11 bg-white font-bold text-ink hover:bg-chip"
          >
            {busy ? <Spinner /> : <SparklesIcon size={18} />}
            {busy ? "Refining…" : "Refine design"}
          </button>
        </div>
      </div>
    </div>
  );
}
