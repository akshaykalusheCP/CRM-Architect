"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, timeAgo } from "@/lib/api";
import type { BusinessModel, Entity, Field, Issue, Project, Version, VersionSummary } from "@/lib/types";
import {
  AlertIcon,
  ArrowRightIcon,
  ChartIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  EditIcon,
  EntityKindIcon,
  PauseIcon,
  PlugIcon,
  TrashIcon,
  XIcon,
} from "./icons";
import { Badge, Confidence, ErrorBox, Spinner, type Tone } from "./ui";
import { KnowledgeFeedback } from "./KnowledgeFeedback";

type Section = "data" | "flows" | "access" | "integrations" | "migration" | "assumptions";
type Automation = BusinessModel["automations"][number];
type BuildPreview = { available: boolean; automations: Record<string, { status: "flow" | "partial" | "manual"; note: string }> };

const SOURCE_LABEL: Record<string, string> = {
  ai_discovery: "AI discovery",
  ai_refine: "AI refinement",
  manual_edit: "Manual edit",
};

const KIND_LABEL: Record<string, string> = {
  account: "Account",
  contact: "Contact",
  lead: "Lead",
  opportunity: "Opportunity",
  case: "Case",
  product: "Product",
  quote: "Quote",
  order: "Order",
  contract: "Contract",
  campaign: "Campaign",
  task: "Task",
  event: "Event",
  custom: "Custom object",
};

const TYPE_CHIP: Record<string, { label: string; tone: Tone }> = {
  lookup: { label: "Link", tone: "purple" },
  picklist: { label: "Pick list", tone: "amber" },
  multi_picklist: { label: "Multi-select", tone: "amber" },
  currency: { label: "Currency", tone: "teal" },
  number: { label: "Number", tone: "teal" },
  percent: { label: "Percent", tone: "teal" },
  date: { label: "Date", tone: "sky" },
  datetime: { label: "Date & time", tone: "sky" },
  boolean: { label: "Yes / no", tone: "gray" },
  email: { label: "Email", tone: "gray" },
  phone: { label: "Phone", tone: "gray" },
  url: { label: "Link URL", tone: "gray" },
  auto_number: { label: "Auto number", tone: "gray" },
  text: { label: "Text", tone: "gray" },
  textarea: { label: "Text area", tone: "gray" },
  long_text: { label: "Long text", tone: "gray" },
  rich_text: { label: "Rich text", tone: "gray" },
};

const pretty = (s: string) => s.replaceAll("_", " ");

function joinWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function Issues({ issues }: { issues: Issue[] }) {
  const [open, setOpen] = useState(false);
  const shown = issues.filter((i) => i.severity !== "info");
  if (!shown.length) return null;
  const errors = shown.filter((i) => i.severity === "error").length;
  return (
    <div className={`rounded-[24px] px-6 py-4 text-[15px] ${errors ? "bg-[#FDE8E8] text-[#9B1C1C]" : "bg-[#FFF4E3] text-[#7A4A00]"}`}>
      <button type="button" className="flex w-full items-center gap-2.5 text-left font-semibold" onClick={() => setOpen(!open)}>
        <AlertIcon size={18} />
        <span className="flex-1">
          {errors ? `${errors} error${errors > 1 ? "s" : ""} to fix before building` : `${shown.length} review note${shown.length > 1 ? "s" : ""}`}
        </span>
        <ChevronDownIcon size={18} className={`transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <ul className="mt-3 space-y-1.5 pl-7 text-sm">
          {shown.map((i, n) => (
            <li key={n}>{i.message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- data model */

function EntityCard({ e, model, selected, onSelect }: { e: Entity; model: BusinessModel; selected: boolean; onSelect: () => void }) {
  const parents = [...new Set(e.fields.filter((f) => f.reference_entity).map((f) => model.entities.find((x) => x.key === f.reference_entity)?.label))]
    .filter(Boolean) as string[];
  const hasPipeline = model.processes.some((p) => p.entity === e.key);
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`flex items-start gap-3.5 rounded-[22px] p-[18px] text-left transition ${
        selected ? "border-2 border-brand-600 bg-brand-50 p-[17px] text-brand-900" : "border border-line bg-white hover:border-brand-200"
      }`}
    >
      <span
        className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${selected ? "bg-brand-600 text-white" : "bg-[#E8EEFC] text-brand-600"}`}
      >
        <EntityKindIcon kind={e.kind} size={20} />
      </span>
      <span className="flex min-w-0 flex-col gap-1">
        <span className="text-base font-bold">{e.label}</span>
        <span className={`text-[13px] ${selected ? "text-[#27407A]" : "text-ink-3"}`}>
          {KIND_LABEL[e.kind] ?? e.kind} · {e.fields.length} fields{hasPipeline ? " · pipeline" : ""}
        </span>
        <span className={`line-clamp-2 text-[13px] ${selected ? "text-[#27407A]" : "text-ink-4"}`}>
          {parents.length ? `Belongs to ${joinWords(parents)}` : e.description}
        </span>
      </span>
    </button>
  );
}

function FieldRow({ f, model, stages }: { f: Field; model: BusinessModel; stages?: BusinessModel["processes"][number]["stages"] }) {
  const chip = TYPE_CHIP[f.type] ?? { label: pretty(f.type), tone: "gray" as Tone };
  const target = f.reference_entity ? model.entities.find((x) => x.key === f.reference_entity)?.label ?? f.reference_entity : null;
  const values = stages ? stages.map((s) => ({ label: s.label, category: s.category })) : f.options.map((o) => ({ label: o.label, category: "open" }));
  return (
    <div className={`flex flex-col gap-2.5 px-6 py-3 ${stages ? "bg-[#F8FAFD]" : ""}`}>
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 truncate text-[15px] font-semibold" title={f.description ?? undefined}>
          {f.label}
          {f.required && <span className="text-[#C0392B]" title="Required"> *</span>}
        </span>
        {target && <span className="text-[13px] text-ink-3">→ {target}</span>}
        {stages && <span className="text-[13px] text-ink-3">drives the pipeline</span>}
        {f.pii && <Badge tone="orange">PII</Badge>}
        <Badge tone={chip.tone} className="text-xs">{chip.label}</Badge>
      </div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {values.slice(0, 12).map((v) => (
            <span
              key={v.label}
              className={`rounded-full px-2.5 py-1 text-xs ${
                v.category === "won"
                  ? "bg-[#E3F4E8] font-semibold text-[#0D5C27]"
                  : v.category === "lost" || v.category === "closed"
                    ? "bg-chip font-semibold text-ink-2"
                    : "border border-line-strong bg-white"
              }`}
            >
              {v.label}
            </span>
          ))}
          {values.length > 12 && <span className="px-1 text-xs text-ink-4">+{values.length - 12}</span>}
        </div>
      )}
    </div>
  );
}

function EntityDetail({ e, model, platform }: { e: Entity; model: BusinessModel; platform: string }) {
  const parents = e.fields
    .filter((f) => f.reference_entity)
    .map((f) => model.entities.find((x) => x.key === f.reference_entity)?.label)
    .filter(Boolean) as string[];
  const children = model.entities.filter((x) => x.fields.some((f) => f.reference_entity === e.key));
  const stageFields = new Map(model.processes.filter((p) => p.entity === e.key).map((p) => [p.stage_field, p.stages]));
  const standard = e.kind !== "custom";
  return (
    <aside className="flex w-full shrink-0 flex-col overflow-hidden rounded-[28px] border border-line bg-white lg:sticky lg:top-24 lg:w-[460px]">
      <div className="flex flex-col gap-2.5 px-6 pt-6 pb-5">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-brand-600 text-white">
            <EntityKindIcon kind={e.kind} size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xl font-bold">{e.label}</p>
            <p className="text-[13px] text-ink-3">
              {platform === "salesforce"
                ? standard
                  ? `Uses the standard Salesforce ${KIND_LABEL[e.kind]}`
                  : "New custom object in Salesforce"
                : KIND_LABEL[e.kind]}
            </p>
          </div>
          <Confidence value={e.provenance.confidence} />
        </div>
        {e.description && <p className="text-sm leading-relaxed text-ink-2">{e.description}</p>}
      </div>
      <div className="border-t border-[#EEF1F5] pb-2">
        <p className="eyebrow px-6 pt-4 pb-1.5">{e.fields.length} fields</p>
        {e.fields.map((f) => (
          <FieldRow key={f.key} f={f} model={model} stages={stageFields.get(f.key)} />
        ))}
        {!e.fields.length && <p className="px-6 py-2 text-sm text-ink-4">Only the built-in name, owner and dates.</p>}
      </div>
      {(parents.length > 0 || children.length > 0) && (
        <div className="flex flex-col gap-2 border-t border-[#EEF1F5] px-6 pt-4 pb-6">
          <p className="eyebrow">Connected to</p>
          <div className="flex flex-wrap gap-2">
            {[...new Set(parents)].map((p) => (
              <span key={`b-${p}`} className="rounded-full bg-chip px-3 py-1.5 text-[13px]">Belongs to {p}</span>
            ))}
            {children.map((c) => (
              <span key={`h-${c.key}`} className="rounded-full bg-chip px-3 py-1.5 text-[13px]">Has many {c.plural_label}</span>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}

function DataModel({ model, platform }: { model: BusinessModel; platform: string }) {
  const [key, setKey] = useState(model.entities[0]?.key);
  const entity = model.entities.find((e) => e.key === key) ?? model.entities[0];
  return (
    <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
      <div className="grid min-w-0 flex-1 gap-3.5 sm:grid-cols-2">
        {model.entities.map((e) => (
          <EntityCard key={e.key} e={e} model={model} selected={e.key === entity?.key} onSelect={() => setKey(e.key)} />
        ))}
      </div>
      {entity && <EntityDetail e={entity} model={model} platform={platform} />}
    </div>
  );
}

/* ------------------------------------------------------ pipelines + automations */

function StagePill({ label, category }: { label: string; category: string }) {
  if (category === "won")
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-[#1E8E3E] px-3.5 py-2 text-sm font-semibold text-white">
        <CheckIcon size={14} strokeWidth={3} />
        {label}
      </span>
    );
  if (category === "lost")
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-chip px-3.5 py-2 text-sm font-semibold text-ink-2">
        <XIcon size={14} strokeWidth={2.6} />
        {label}
      </span>
    );
  if (category === "closed") return <span className="rounded-full bg-chip px-3.5 py-2 text-sm font-semibold text-ink-2">{label}</span>;
  if (/hold|pause|wait/i.test(label))
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-[#FEF1C7] px-3.5 py-2 text-sm font-semibold text-[#7A4A00]">
        <PauseIcon size={14} strokeWidth={2.6} />
        {label}
      </span>
    );
  return <span className="rounded-full bg-[#E8EEFC] px-3.5 py-2 text-sm font-semibold text-brand-900">{label}</span>;
}

function Pipelines({ model }: { model: BusinessModel }) {
  const label = (key: string) => model.entities.find((e) => e.key === key)?.label ?? key;
  if (!model.processes.length) return <p className="text-ink-3">No pipelines in this design.</p>;
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {model.processes.map((p) => {
        const open = p.stages.filter((s) => s.category === "open");
        const closed = p.stages.filter((s) => s.category !== "open");
        return (
          <section key={p.key} className="flex flex-col gap-4 rounded-[28px] border border-line bg-white px-6 py-6">
            <div>
              <p className="text-lg font-bold">{p.name}</p>
              <p className="text-sm text-ink-3">On {label(p.entity)} · {p.stages.length} stages</p>
              {p.description && <p className="mt-1.5 text-sm text-ink-2">{p.description}</p>}
            </div>
            <div className="flex flex-col gap-2.5">
              <p className="eyebrow">Open</p>
              <div className="flex flex-wrap items-center gap-1.5">
                {open.map((s, i) => (
                  <span key={s.key} className="flex items-center gap-1.5">
                    <StagePill label={s.label} category={s.category} />
                    {i < open.length - 1 && <ChevronRightIcon size={14} strokeWidth={2.4} className="text-[#9AA4B2]" />}
                  </span>
                ))}
              </div>
            </div>
            {closed.length > 0 && (
              <div className="flex flex-col gap-2.5 border-t border-dashed border-line-strong pt-4">
                <p className="eyebrow">Closed</p>
                <div className="flex flex-wrap gap-2">
                  {closed.map((s) => (
                    <StagePill key={s.key} label={s.label} category={s.category} />
                  ))}
                </div>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function describeTrigger(a: Automation, model: BusinessModel): string {
  const entity = model.entities.find((e) => e.key === a.entity);
  const name = entity?.label ?? a.entity;
  const fieldLabel = (key: string) => entity?.fields.find((f) => f.key === key)?.label ?? pretty(key);
  const t = a.trigger;
  let base: string;
  if (t.relative_date_field) {
    const days = t.offset_days ?? 0;
    base = `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} ${days < 0 ? "before" : "after"} ${fieldLabel(t.relative_date_field)}`;
  } else {
    base = {
      record_created: `${name} is created`,
      record_updated: `${name} is updated`,
      record_created_or_updated: `${name} is created or updated`,
      record_deleted: `${name} is deleted`,
      scheduled: t.schedule ? `Scheduled, ${t.schedule}` : "On a schedule",
    }[t.type] ?? pretty(t.type);
  }
  const conds = a.conditions.map((c) => {
    const f = fieldLabel(c.field);
    switch (c.operator) {
      case "equals": return `${f} is ${c.value}`;
      case "not_equals": return `${f} is not ${c.value}`;
      case "changed": return `${f} changes`;
      case "is_blank": return `${f} is empty`;
      case "is_not_blank": return `${f} is filled`;
      case "greater_than": return `${f} is over ${c.value}`;
      case "less_than": return `${f} is under ${c.value}`;
      default: return `${f} ${pretty(c.operator)} ${c.value ?? ""}`.trim();
    }
  });
  return conds.length ? `${base} and ${conds.join(a.condition_logic === "all" ? " and " : " or ")}` : base;
}

function describeAction(x: Automation["actions"][number], a: Automation, model: BusinessModel): string {
  const entity = model.entities.find((e) => e.key === a.entity);
  const fieldLabel = (key: string | null) => (key ? entity?.fields.find((f) => f.key === key)?.label ?? pretty(key) : "a field");
  const who = (r: string | null) =>
    !r || r === "owner" ? "the owner" : model.roles.find((ro) => ro.key === r)?.label ?? entity?.fields.find((f) => f.key === r)?.label ?? pretty(r);
  switch (x.type) {
    case "update_field": return `Set ${fieldLabel(x.target_field)}${x.value ? ` to ${x.value}` : ""}`;
    case "create_task": return x.subject || x.description || "Create a task";
    case "send_email": return `Email ${who(x.recipient)}`;
    case "notify_user": return `Notify ${who(x.recipient)}`;
    case "assign_owner": return x.description || "Assign owner";
    case "create_record": return `Create ${model.entities.find((e) => e.key === x.related_entity)?.label ?? "a record"}`;
    case "call_webhook": return "Call an external system";
    default: return x.description;
  }
}

function Automations({
  model,
  preview,
  issues,
  canEdit,
  onRemove,
}: {
  model: BusinessModel;
  preview: BuildPreview | null;
  issues: Issue[];
  canEdit: boolean;
  onRemove: (a: Automation) => Promise<void>;
}) {
  type AFilter = "all" | "flow" | "manual" | "review";
  const [filter, setFilter] = useState<AFilter>("all");
  const [removing, setRemoving] = useState<string | null>(null);
  const reviewNotes = (a: Automation) =>
    issues.filter((i) => i.severity !== "info" && i.path.startsWith(`automations.${a.key}`)).map((i) => i.message);
  const status = (a: Automation) => preview?.automations[a.key];
  const reviewCount = model.automations.filter((a) => reviewNotes(a).length).length;
  const matches = (a: Automation) => {
    const s = status(a)?.status;
    if (filter === "flow") return s === "flow" || s === "partial";
    if (filter === "manual") return s === "manual" || s === "partial";
    if (filter === "review") return reviewNotes(a).length > 0;
    return true;
  };
  const groups = model.entities
    .map((e) => ({ entity: e, items: model.automations.filter((a) => a.entity === e.key && matches(a)) }))
    .filter((g) => g.items.length);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="flex-1 text-[22px] font-bold">Automations</h3>
        <button type="button" onClick={() => setFilter("all")} className={`chip ${filter === "all" ? "chip-active" : ""}`}>All {model.automations.length}</button>
        {preview?.available && (
          <>
            <button type="button" onClick={() => setFilter("flow")} className={`chip ${filter === "flow" ? "chip-active" : ""}`}>Built as Flows</button>
            <button type="button" onClick={() => setFilter("manual")} className={`chip ${filter === "manual" ? "chip-active" : ""}`}>Manual setup</button>
          </>
        )}
        {reviewCount > 0 && (
          <button
            type="button"
            onClick={() => setFilter("review")}
            className={`chip ${filter === "review" ? "chip-active" : "border-[#F1C38E] bg-[#FFF4E8] font-semibold text-[#8A3B00]"}`}
          >
            Needs review · {reviewCount}
          </button>
        )}
      </div>

      {!model.automations.length ? (
        <p className="text-ink-3">No automations in this design.</p>
      ) : (
        <section className="overflow-hidden rounded-[28px] border border-line bg-white">
          {groups.map((g) => (
            <div key={g.entity.key}>
              <p className="eyebrow px-6 pt-4 pb-1">{g.entity.label}</p>
              {g.items.map((a) => {
                const notes = reviewNotes(a);
                const s = status(a);
                return (
                  <div
                    key={a.key}
                    className={`flex flex-col gap-3 border-b border-[#EEF1F5] px-6 py-4 last:border-0 lg:flex-row lg:items-center lg:gap-5 ${notes.length ? "bg-[#FFF8F0]" : ""}`}
                  >
                    <div className="lg:w-[280px] lg:shrink-0">
                      <p className="text-[15px] font-bold">{a.name}</p>
                      {notes.map((n) => (
                        <p key={n} className="mt-0.5 text-[13px] text-[#8A3B00]">{n}</p>
                      ))}
                    </div>
                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2.5" title={a.description}>
                      <span className="rounded-xl bg-chip px-3 py-1.5 text-sm">
                        <span className="mr-1.5 font-bold text-ink-3">When</span>
                        {describeTrigger(a, model)}
                      </span>
                      <ArrowRightIcon size={18} strokeWidth={2.2} className="text-[#9AA4B2]" />
                      {a.actions.map((x, i) => (
                        <span key={i} className="rounded-xl bg-[#E8EEFC] px-3 py-1.5 text-sm text-brand-900">
                          {i === 0 && <span className="mr-1.5 font-bold">Then</span>}
                          {describeAction(x, a, model)}
                        </span>
                      ))}
                    </div>
                    <div className="flex items-center gap-2">
                      {s && (
                        <Badge tone={s.status === "flow" ? "green" : "amber"} className="text-[13px] whitespace-nowrap">
                          {s.note}
                        </Badge>
                      )}
                      {canEdit && (
                        <button
                          type="button"
                          aria-label={`Remove automation ${a.name}`}
                          title="Remove from design"
                          disabled={removing === a.key}
                          onClick={async () => {
                            if (!confirm(`Remove "${a.name}" from the design? This saves a new version.`)) return;
                            setRemoving(a.key);
                            await onRemove(a).finally(() => setRemoving(null));
                          }}
                          className="flex h-9 w-9 items-center justify-center rounded-full text-ink-4 transition hover:bg-[#FDE8E8] hover:text-[#9B1C1C]"
                        >
                          {removing === a.key ? <Spinner /> : <TrashIcon size={16} />}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
          {!groups.length && <p className="px-6 py-6 text-ink-3">Nothing matches this filter.</p>}
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ other sections */

const ACCESS = [
  { label: "Admin", style: "bg-brand-600 text-white" },
  { label: "Full", style: "bg-brand-100 text-brand-900" },
  { label: "Edit", style: "bg-[#E3F4E8] text-[#0D5C27]" },
  { label: "Read", style: "bg-chip text-ink-2" },
];

function accessOf(p: BusinessModel["roles"][number]["permissions"][number]) {
  if (p.view_all && p.delete) return ACCESS[0];
  if (p.delete) return ACCESS[1];
  if (p.edit || p.create) return ACCESS[2];
  return ACCESS[3];
}

function Access({ model }: { model: BusinessModel }) {
  const byKey = Object.fromEntries(model.roles.map((r) => [r.key, r]));
  const depth = (key: string, seen = new Set<string>()): number => {
    const r = byKey[key];
    if (!r?.reports_to || seen.has(key)) return 0;
    seen.add(key);
    return 1 + depth(r.reports_to, seen);
  };
  const roles = [...model.roles].sort((a, b) => depth(a.key) - depth(b.key));
  if (!roles.length) return <p className="text-ink-3">No roles in this design.</p>;
  return (
    <div className="overflow-x-auto rounded-[28px] border border-line bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[13px] text-ink-3">
            <th className="sticky left-0 bg-white py-4 pr-4 pl-6 font-semibold">Role</th>
            {model.entities.map((e) => (
              <th key={e.key} className="px-2 py-4 text-center font-semibold whitespace-nowrap">{e.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roles.map((r) => {
            const perms = Object.fromEntries(r.permissions.map((p) => [p.entity, p]));
            return (
              <tr key={r.key} className="border-t border-[#EEF1F5]">
                <td className="sticky left-0 bg-white py-3.5 pr-4" style={{ paddingLeft: 24 + depth(r.key) * 18 }}>
                  <p className="font-semibold whitespace-nowrap">
                    {depth(r.key) > 0 && <span className="mr-1.5 text-[#9AA4B2]">↳</span>}
                    {r.label}
                  </p>
                  {r.description && <p className="max-w-[260px] text-[13px] text-ink-4">{r.description}</p>}
                </td>
                {model.entities.map((e) => {
                  const p = perms[e.key];
                  const a = p && p.read ? accessOf(p) : null;
                  return (
                    <td key={e.key} className="px-2 py-3.5 text-center">
                      {a ? <span className={`inline-block rounded-full px-2.5 py-1 text-xs font-semibold ${a.style}`}>{a.label}</span> : <span className="text-[#C3CAD6]">—</span>}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="border-t border-[#EEF1F5] px-6 py-3.5 text-[13px] text-ink-4">
        Read = view · Edit = create and edit · Full = also delete · Admin = see and change everyone&apos;s records
      </p>
    </div>
  );
}

function Integrations({ model }: { model: BusinessModel }) {
  const label = (key: string) => model.entities.find((e) => e.key === key)?.label ?? key;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="flex flex-col gap-3">
        <h3 className="text-[22px] font-bold">Integrations</h3>
        {model.integrations.map((i) => (
          <div key={i.key} className="flex gap-4 rounded-[24px] border border-line bg-white p-5">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#E1EFFC] text-[#0B4A8A]"><PlugIcon size={19} /></span>
            <div className="min-w-0">
              <p className="font-bold">{i.system}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <Badge className="text-xs">{pretty(i.direction)}</Badge>
                <Badge tone="blue" className="text-xs">{pretty(i.frequency)}</Badge>
              </div>
              <p className="mt-2 text-sm text-ink-2">{i.description}</p>
            </div>
          </div>
        ))}
        {!model.integrations.length && <p className="text-ink-3">No integrations identified.</p>}
      </div>
      <div className="flex flex-col gap-3">
        <h3 className="text-[22px] font-bold">Reports &amp; dashboards</h3>
        {model.reports.map((r) => (
          <div key={r.key} className="flex gap-4 rounded-[24px] border border-line bg-white p-5">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#DDF3EC] text-[#0B5E53]"><ChartIcon size={19} /></span>
            <div>
              <p className="font-bold">{r.name}</p>
              <p className="mt-0.5 text-sm text-ink-3">
                {pretty(r.kind)} on {label(r.entity)}
                {r.group_by.length ? ` · by ${r.group_by.map(pretty).join(", ")}` : ""}
              </p>
            </div>
          </div>
        ))}
        {!model.reports.length && <p className="text-ink-3">No reports defined.</p>}
      </div>
    </div>
  );
}

function Migration({ model }: { model: BusinessModel }) {
  const label = (key: string) => model.entities.find((e) => e.key === key)?.label ?? key;
  if (!model.data_mappings.length)
    return <p className="text-ink-3">Upload the client&apos;s spreadsheets to get a column-by-column migration mapping.</p>;
  return (
    <div className="overflow-x-auto rounded-[28px] border border-line bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[13px] text-ink-3">
            <th className="py-4 pr-4 pl-6 font-semibold">Source</th>
            <th className="pr-4 font-semibold">Column</th>
            <th className="pr-4 font-semibold">Goes to</th>
            <th className="pr-6 font-semibold">Clean-up</th>
          </tr>
        </thead>
        <tbody>
          {model.data_mappings.map((m, i) => (
            <tr key={i} className="border-t border-[#EEF1F5]">
              <td className="py-3 pr-4 pl-6 text-ink-3">{m.source}</td>
              <td className="pr-4 font-semibold">{m.column}</td>
              <td className="pr-4">
                <span className="rounded-full bg-chip px-2.5 py-1">{label(m.entity)} · {pretty(m.field)}</span>
              </td>
              <td className="pr-6 text-ink-2">{m.transform}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Assumptions({ model }: { model: BusinessModel }) {
  if (!model.assumptions.length) return <p className="text-ink-3">No assumptions recorded.</p>;
  return (
    <div className="divide-y divide-[#EEF1F5] rounded-[28px] border border-line bg-white">
      {model.assumptions.map((a, i) => (
        <div key={i} className="flex items-start gap-4 px-6 py-4">
          <Confidence value={a.confidence} />
          <div>
            <p className="text-[15px]">{a.statement}</p>
            {a.rationale && <p className="mt-0.5 text-sm text-ink-3">{a.rationale}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

function JsonEditor({ version, projectId, onSaved, onCancel }: { version: Version; projectId: string; onSaved: () => void; onCancel: () => void }) {
  const [text, setText] = useState(() => JSON.stringify(version.model, null, 2));
  const [note, setNote] = useState("");
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(save: boolean) {
    setError(null);
    let model: object;
    try {
      model = JSON.parse(text);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    setBusy(true);
    try {
      if (save) {
        await api(`/projects/${projectId}/model`, { method: "PUT", json: { model, base_version: version.version, note: note || "Manual edit" } });
        onSaved();
      } else {
        setIssues((await api<{ issues: Issue[] }>(`/projects/${projectId}/model/validate`, { json: model })).issues);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-[28px] border border-line bg-white p-6">
      <div>
        <h3 className="text-xl font-bold">Edit design</h3>
        <p className="text-[15px] text-ink-3">Saving creates a new version. Keep keys stable so answers and mappings still line up.</p>
      </div>
      <textarea
        className="min-h-[520px] rounded-2xl bg-ink p-5 font-mono text-xs leading-relaxed text-[#E6E9EF] outline-none focus:ring-4 focus:ring-brand-200"
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <input className="input" placeholder="What did you change? (shown in version history)" value={note} onChange={(e) => setNote(e.target.value)} />
      <ErrorBox message={error} />
      {issues && (issues.length ? <Issues issues={issues} /> : <p className="text-[15px] font-semibold text-[#0D5C27]">No issues found.</p>)}
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" disabled={busy} onClick={() => run(true)}>{busy && <Spinner />} Save as new version</button>
        <button className="btn-secondary" disabled={busy} onClick={() => run(false)}>Validate</button>
        <button className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ the tab */

export function DesignTab({ project, onSaved, onBuild }: { project: Project; onSaved: () => Promise<void>; onBuild: () => void }) {
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [selected, setSelected] = useState<number | null>(project.current_version);
  const [version, setVersion] = useState<Version | null>(null);
  const [preview, setPreview] = useState<BuildPreview | null>(null);
  const [section, setSection] = useState<Section>("data");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<VersionSummary[]>(`/projects/${project.id}/model/versions`).then(setVersions).catch(() => {});
  }, [project.id, project.current_version]);

  useEffect(() => {
    if (!selected) return;
    setVersion(null);
    setPreview(null);
    api<Version>(`/projects/${project.id}/model/versions/${selected}`)
      .then(setVersion)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load design"));
    api<BuildPreview>(`/projects/${project.id}/model/versions/${selected}/build-preview`).then(setPreview).catch(() => {});
  }, [project.id, selected]);

  const isLatest = selected === project.current_version;
  const usage = useMemo(
    () => version?.llm_usage as { input_tokens?: number; output_tokens?: number; model?: string; provider?: string } | null,
    [version],
  );

  if (error) return <ErrorBox message={error} />;
  if (!version) return <div className="flex justify-center py-20 text-ink-4"><Spinner className="h-6 w-6" /></div>;

  const m = version.model;
  const fieldCount = m.entities.reduce((n, e) => n + e.fields.length, 0);
  const counts: [number, string][] = [
    [m.entities.length, "entities"],
    [fieldCount, "fields"],
    [m.processes.length, "pipelines"],
    [m.automations.length, "automations"],
    [m.roles.length, "roles"],
    [m.integrations.length, "integrations"],
  ];
  const tabs: [Section, string][] = [
    ["data", "Data model"],
    ["flows", "Pipelines & automations"],
    ["access", "Access"],
    ["integrations", "Integrations & reports"],
    ["migration", "Migration"],
    ["assumptions", "Assumptions"],
  ];

  async function removeAutomation(a: Automation) {
    const model = { ...m, automations: m.automations.filter((x) => x.key !== a.key) };
    try {
      await api(`/projects/${project.id}/model`, {
        method: "PUT",
        json: { model, base_version: version!.version, note: `Removed automation "${a.name}"` },
      });
      await onSaved();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Could not remove the automation");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {usage?.provider === "mock" && (
        <div className="flex gap-3 rounded-[24px] bg-[#FDE8E8] p-5 text-[15px] text-[#9B1C1C]">
          <AlertIcon size={20} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">This design was generated without AI (offline demo mode)</p>
            <p className="mt-1">
              It only mirrors spreadsheet columns. Set up a provider in <a href="/settings" className="font-semibold underline">AI settings</a> and
              re-analyse from Business input.
            </p>
          </div>
        </div>
      )}

      {editing ? (
        <JsonEditor
          version={version}
          projectId={project.id}
          onCancel={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false);
            await onSaved();
          }}
        />
      ) : (
        <>
          <section className="flex flex-col gap-5 rounded-[28px] border border-line bg-white px-6 py-7 sm:px-8">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-2xl font-bold tracking-tight">CRM design</h2>
              <label className="relative">
                <span className="sr-only">Design version</span>
                <select
                  className="h-9 appearance-none rounded-full border border-line-strong bg-white pr-9 pl-4 text-sm font-semibold outline-none focus:ring-4 focus:ring-brand-100"
                  value={selected ?? ""}
                  onChange={(e) => {
                    setEditing(false);
                    setSelected(Number(e.target.value));
                  }}
                >
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      Version {v.version} · {SOURCE_LABEL[v.source] ?? v.source} · {timeAgo(v.created_at)}
                    </option>
                  ))}
                </select>
                <ChevronDownIcon size={16} className="pointer-events-none absolute top-2.5 right-3" />
              </label>
              {!isLatest && <Badge tone="amber">Older version</Badge>}
              {usage?.model && usage.provider !== "mock" && <span className="text-sm text-ink-4">Generated by {usage.model}</span>}
              <span className="flex-1" />
              {isLatest && (
                <button type="button" className="btn-secondary" onClick={() => setEditing(true)}>
                  <EditIcon size={17} /> Edit
                </button>
              )}
              <button type="button" className="btn-primary" onClick={onBuild}>
                Go to build <ArrowRightIcon size={18} strokeWidth={2.2} />
              </button>
            </div>
            <p className="max-w-5xl text-base leading-relaxed whitespace-pre-line text-ink-2">{version.summary}</p>
            <div className="flex flex-wrap gap-2">
              {counts.map(([n, label]) => (
                <span key={label} className="rounded-full bg-chip px-3.5 py-1.5 text-sm">
                  <span className="font-bold">{n}</span> {label}
                </span>
              ))}
            </div>
            {version.changes.length > 0 && (
              <details className="group rounded-2xl bg-canvas px-5 py-3.5">
                <summary className="cursor-pointer list-none text-sm font-semibold text-ink-2">
                  What changed in this version ({version.changes.length})
                </summary>
                <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-ink-2">
                  {version.changes.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </details>
            )}
            {m.profile.out_of_scope.length > 0 && (
              <p className="text-sm text-ink-3">
                <span className="font-semibold text-ink-2">Kept outside the CRM:</span> {m.profile.out_of_scope.join("; ")}
              </p>
            )}
          </section>

          <Issues issues={version.issues} />

          <div role="tablist" aria-label="Design sections" className="flex gap-1 overflow-x-auto border-b border-line">
            {tabs.map(([key, label]) => {
              const active = section === key;
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setSection(key)}
                  className={`relative h-[52px] shrink-0 px-4 text-[15px] transition ${active ? "font-bold text-brand-600" : "font-semibold text-ink-3 hover:text-ink"}`}
                >
                  {label}
                  {active && <span className="absolute inset-x-3 -bottom-px h-[3px] rounded-t-full bg-brand-600" />}
                </button>
              );
            })}
          </div>

          {section === "data" && <DataModel model={m} platform={project.target_platform} />}
          {section === "flows" && (
            <div className="flex flex-col gap-8">
              <div className="flex flex-col gap-4">
                <h3 className="text-[22px] font-bold">Pipelines</h3>
                <Pipelines model={m} />
              </div>
              <Automations model={m} preview={preview} issues={version.issues} canEdit={isLatest} onRemove={removeAutomation} />
            </div>
          )}
          {section === "access" && <Access model={m} />}
          {section === "integrations" && <Integrations model={m} />}
          {section === "migration" && <Migration model={m} />}
          {section === "assumptions" && <Assumptions model={m} />}
          <KnowledgeFeedback projectId={project.id} version={version.version} />
        </>
      )}
    </div>
  );
}
