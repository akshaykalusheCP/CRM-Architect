"use client";

import type { ComponentType, SVGProps } from "react";
import { CheckIcon, ChevronRightIcon } from "./icons";

export type StepState = "done" | "partial" | "todo";

export type Step<K extends string> = {
  key: K;
  title: string;
  detail: string;
  state: StepState;
  icon: ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;
  progress?: { done: number; total: number; label: string };
  disabled?: boolean;
};

function ProgressDial({ done, total }: { done: number; total: number }) {
  const r = 17;
  const c = 2 * Math.PI * r;
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden className="shrink-0">
      <circle cx="20" cy="20" r={r} fill="#FEF1C7" />
      <circle cx="20" cy="20" r={r} fill="none" stroke="#F3D9A0" strokeWidth="4" />
      <circle
        cx="20"
        cy="20"
        r={r}
        fill="none"
        stroke="#B26A00"
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray={`${total ? (done / total) * c : 0} ${c}`}
        transform="rotate(-90 20 20)"
      />
      <text x="20" y="24.5" textAnchor="middle" fontSize="11.5" fontWeight="700" fill="#7A4A00">
        {done}/{total}
      </text>
    </svg>
  );
}

/** Project journey: one rounded track; the open step expands and shows its own progress. */
export function StepRail<K extends string>({
  steps,
  active,
  onSelect,
}: {
  steps: Step<K>[];
  active: K;
  onSelect: (key: K) => void;
}) {
  return (
    <nav
      aria-label="Project steps"
      className="flex flex-col gap-1 rounded-[36px] border border-line bg-white p-2 md:flex-row md:items-center"
    >
      {steps.map((s, i) => {
        const current = s.key === active;
        const Icon = s.icon;
        return (
          <div key={s.key} className={`flex items-center gap-1 ${current ? "md:flex-[1.7]" : "md:flex-1"} md:basis-0`}>
            <button
              type="button"
              disabled={s.disabled}
              aria-current={current ? "step" : undefined}
              onClick={() => onSelect(s.key)}
              className={`flex min-w-0 flex-1 items-center gap-3 rounded-[28px] px-4 py-3 text-left transition disabled:cursor-not-allowed disabled:opacity-45 ${
                current ? "bg-brand-100 text-brand-900" : "hover:bg-chip"
              }`}
            >
              {current ? (
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white">
                  <Icon size={20} />
                </span>
              ) : s.state === "done" ? (
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#E3F4E8] text-[#1E8E3E]">
                  <CheckIcon size={20} strokeWidth={2.6} />
                </span>
              ) : s.state === "partial" && s.progress ? (
                <ProgressDial done={s.progress.done} total={s.progress.total} />
              ) : (
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-chip text-ink-3">
                  <Icon size={20} />
                </span>
              )}
              <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                <span className="flex items-baseline justify-between gap-3">
                  <span className={`truncate text-[15px] ${current ? "font-bold" : "font-semibold"}`}>{s.title}</span>
                  {current && s.progress && (
                    <span className="shrink-0 text-[13px] font-semibold">{s.progress.label}</span>
                  )}
                </span>
                {current && s.progress && s.progress.total > 0 ? (
                  <span className="flex gap-[3px]" aria-hidden>
                    {Array.from({ length: Math.min(s.progress.total, 16) }, (_, n) => {
                      const filled = n < Math.round((s.progress!.done / s.progress!.total) * Math.min(s.progress!.total, 16));
                      return <span key={n} className={`h-1.5 flex-1 rounded-full ${filled ? "bg-brand-600" : "bg-white"}`} />;
                    })}
                  </span>
                ) : (
                  <span className={`truncate text-[13px] ${current ? "font-medium" : "text-ink-3"}`}>{s.detail}</span>
                )}
              </span>
            </button>
            {i < steps.length - 1 && <ChevronRightIcon size={18} className="hidden shrink-0 text-[#9AA4B2] md:block" />}
          </div>
        );
      })}
    </nav>
  );
}
