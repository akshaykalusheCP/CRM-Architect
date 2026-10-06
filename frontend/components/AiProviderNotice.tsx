"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AlertIcon } from "./icons";

type Settings = {
  active_provider: string | null;
  server_default: { provider: string; label: string };
  providers: { key: string; label: string; model: string }[];
};

export type ActiveProvider = { key: string; label: string; model: string; isDefault: boolean };

/** The AI provider the next analysis will use (workspace choice, else the server default). */
export function useActiveProvider(): ActiveProvider | null {
  const [s, setS] = useState<Settings | null>(null);
  useEffect(() => {
    api<Settings>("/settings/llm").then(setS).catch(() => {});
  }, []);
  if (!s) return null;
  const key = s.active_provider ?? s.server_default.provider;
  const p = s.providers.find((x) => x.key === key);
  return { key, label: p?.label ?? key, model: p?.model ?? "", isDefault: !s.active_provider };
}

/** Rough duration users should expect for one analysis run. */
export function runEstimate(p: ActiveProvider | null): string {
  if (!p || p.key === "mock") return "Takes a few seconds";
  if (p.key === "groq") return "About 8 minutes on Groq's free tier";
  return "Usually a few minutes";
}

/** Shows which AI the next analysis will use, and warns loudly when it is the offline demo. */
export function AiProviderNotice() {
  const p = useActiveProvider();
  if (!p) return null;
  if (p.key === "mock") {
    return (
      <div className="mt-4 flex gap-3 rounded-2xl bg-[#FDE8E8] p-4 text-sm text-[#9B1C1C]">
        <AlertIcon size={18} className="mt-0.5 shrink-0" />
        <p>
          <span className="font-semibold">No AI configured.</span> Offline demo mode only copies spreadsheet columns into a rough
          draft. <Link href="/settings" className="font-semibold underline">Set up an AI provider</Link>
        </p>
      </div>
    );
  }
  return (
    <p className="mt-3 text-center text-[13px] text-ink-4">
      Uses <span className="font-semibold text-ink-2">{p.label}</span>
      {p.model && <> · {p.model}</>}
      {p.isDefault && " (server default)"} ·{" "}
      <Link href="/settings" className="font-semibold text-brand-600 hover:underline">Change</Link>
    </p>
  );
}
