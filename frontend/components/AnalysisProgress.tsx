"use client";

import { useEffect, useState } from "react";
import type { Job } from "@/lib/types";
import { CheckIcon, ClockIcon, SparklesIcon } from "./icons";
import { Spinner } from "./ui";

const STAGED_STEPS = [
  "Understanding the input",
  "Defining fields",
  "Pipelines and roles",
  "Automations and reports",
  "Reviewing the design",
];

function elapsed(from: string | null | undefined, now: number): string {
  if (!from) return "0:00";
  const s = Math.max(0, Math.floor((now - new Date(from).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Live view of a running analysis, including the staged steps used on rate-limited providers. */
export function AnalysisProgress({ job }: { job: Job }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const progress = job.progress ?? "Queued";
  const step = Number(/^Step (\d)/.exec(progress)?.[1] ?? 0);
  const waiting = /\(waiting (\d+)s/.exec(progress)?.[1];
  const detail = progress.replace(/^Step \d:\s*/, "").replace(/\s*\(waiting.*\)$/, "");

  return (
    <div className="relative overflow-hidden rounded-[28px] bg-brand-100 px-7 py-6 text-brand-900">
      <div className="absolute inset-x-0 top-0 h-1 overflow-hidden bg-brand-200">
        <div className="animate-shimmer h-full w-1/3 rounded-full bg-brand-600" />
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-white text-brand-600">
          <SparklesIcon size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-lg font-bold">AI is designing the CRM</p>
          <p className="text-[15px] text-[#27407A]">
            {detail}
            {waiting && <span> · pausing {waiting}s for the provider&apos;s rate limit</span>}
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3.5 py-1.5 text-sm font-semibold">
          <ClockIcon size={15} /> {elapsed(job.started_at ?? job.created_at, now)}
        </span>
      </div>
      {step > 0 && (
        <ol className="mt-5 flex flex-wrap gap-2">
          {STAGED_STEPS.map((label, i) => {
            const n = i + 1;
            const state = n < step ? "done" : n === step ? "active" : "todo";
            return (
              <li
                key={label}
                className={`flex items-center gap-2 rounded-full py-1.5 pr-3.5 pl-1.5 text-sm ${
                  state === "active" ? "bg-white font-semibold text-brand-900" : state === "done" ? "text-brand-900" : "text-[#5A6F9E]"
                }`}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                    state === "done" ? "bg-[#1E8E3E] text-white" : state === "active" ? "text-brand-600" : "bg-white/60"
                  }`}
                >
                  {state === "done" ? <CheckIcon size={13} strokeWidth={3} /> : state === "active" ? <Spinner className="h-4 w-4" /> : n}
                </span>
                {label}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
