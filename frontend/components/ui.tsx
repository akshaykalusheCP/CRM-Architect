"use client";

import { useEffect, type ReactNode } from "react";
import { AlertIcon, XIcon } from "./icons";

const TONES = {
  gray: "bg-chip text-ink-2",
  blue: "bg-brand-100 text-brand-900",
  green: "bg-[#E3F4E8] text-[#0D5C27]",
  amber: "bg-[#FEF1C7] text-[#7A4A00]",
  orange: "bg-[#FFF1E3] text-[#8A3B00]",
  red: "bg-[#FDE8E8] text-[#9B1C1C]",
  purple: "bg-[#EEE8FB] text-[#4B2D99]",
  teal: "bg-[#DDF3EC] text-[#0B5E53]",
  sky: "bg-[#E1EFFC] text-[#0B4A8A]",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "gray", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={`tag ${TONES[tone]} ${className}`}>{children}</span>;
}

const STATUS: Record<string, { tone: Tone; label: string }> = {
  draft: { tone: "gray", label: "Draft" },
  analyzing: { tone: "blue", label: "Analysing" },
  needs_input: { tone: "amber", label: "Needs answers" },
  ready: { tone: "green", label: "Ready to build" },
  error: { tone: "red", label: "Needs attention" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: "gray" as Tone, label: status };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

export function ErrorBox({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <div className="flex items-start gap-2.5 rounded-2xl bg-[#FDE8E8] px-4 py-3 text-sm text-[#9B1C1C]">
      <AlertIcon size={17} className="mt-0.5 shrink-0" />
      <span className="min-w-0 break-words">{message}</span>
    </div>
  );
}

export function Empty({ title, icon, children }: { title: string; icon?: ReactNode; children?: ReactNode }) {
  return (
    <div className="rounded-[28px] border-2 border-dashed border-[#C9D2E0] px-6 py-16 text-center">
      {icon && (
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-line bg-white text-brand-600">
          {icon}
        </div>
      )}
      <p className="text-[17px] font-semibold text-ink">{title}</p>
      {children && <div className="mx-auto mt-1.5 max-w-md text-[15px] text-ink-3">{children}</div>}
    </div>
  );
}

export function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.75 ? "bg-[#1E8E3E]" : value >= 0.5 ? "bg-[#B26A00]" : "bg-[#C0392B]";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-4" title={`Confidence ${pct}%`}>
      <span className="h-1.5 w-10 overflow-hidden rounded-full bg-chip">
        <span className={`block h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </span>
      {pct}%
    </span>
  );
}

const AVATAR_TONES = [
  "bg-[#FDE7D3] text-[#8A3B00]",
  "bg-[#D5F0EA] text-[#0B5E53]",
  "bg-[#E8E1FA] text-[#4B2D99]",
  "bg-[#DCE7FF] text-[#0B2E78]",
  "bg-[#FCE1EC] text-[#8C1D4B]",
  "bg-[#E4F1D4] text-[#3A5A0E]",
];

export function Avatar({ name, size = "md" }: { name: string; size?: "sm" | "md" | "lg" }) {
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase())
      .join("") || "?";
  const hash = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  const dims = { sm: "h-9 w-9 text-sm", md: "h-12 w-12 text-[17px]", lg: "h-14 w-14 text-[19px]" }[size];
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-full font-bold ${AVATAR_TONES[hash % AVATAR_TONES.length]} ${dims}`}
    >
      {initials}
    </span>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[34px] leading-tight font-bold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1.5 text-[17px] text-ink-3">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 px-4 py-12">
      <div className="absolute inset-0" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`relative w-full rounded-[28px] bg-white shadow-pop ${wide ? "max-w-3xl" : "max-w-xl"}`}
      >
        <div className="flex items-center justify-between px-7 pt-6 pb-2">
          <h2 className="text-[22px] font-bold">{title}</h2>
          <button className="btn-ghost -mr-3 h-10 w-10 px-0" onClick={onClose} aria-label="Close">
            <XIcon />
          </button>
        </div>
        <div className="px-7 pt-3 pb-7">{children}</div>
      </div>
    </div>
  );
}

export function ProgressBar({ value, tone = "brand" }: { value: number; tone?: "brand" | "green" }) {
  const color = tone === "green" ? "bg-[#1E8E3E]" : "bg-brand-600";
  return (
    <div className="h-2 overflow-hidden rounded-full bg-chip">
      <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  );
}

/** Ring showing done/total, used for question progress. */
export function ProgressRing({ done, total, size = 52, color = "#1E8E3E" }: { done: number; total: number; size?: number; color?: string }) {
  const r = size / 2 - 5;
  const c = 2 * Math.PI * r;
  const frac = total ? done / total : 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#E3E7EE" strokeWidth="6" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray={`${frac * c} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}
