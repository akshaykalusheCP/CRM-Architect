"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";
import { ErrorBox, Spinner } from "./ui";

type Entry = {
  id: string;
  title: string;
  content: string;
  status: "pending" | "approved" | "rejected";
  scope: "project" | "organization";
  source_version: number | null;
};

export function KnowledgeFeedback({ projectId, version }: { projectId: string; version: number }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [canReview, setCanReview] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = `/projects/${projectId}/knowledge`;

  useEffect(() => {
    api<Entry[]>(path).then(setEntries).catch(() => {});
    api<User>("/auth/me").then((user) => setCanReview(user.role === "owner" || user.role === "admin")).catch(() => {});
  }, [path]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api(path, { method: "POST", json: { title, content, source_version: version } });
      setEntries(await api<Entry[]>(path));
      setTitle("");
      setContent("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save feedback");
    } finally {
      setBusy(false);
    }
  }

  async function review(id: string, decision: "approve" | "reject", scope: "project" | "organization" = "project") {
    setBusy(true);
    setError(null);
    try {
      await api(`${path}/${id}/review`, { method: "POST", json: { decision, scope } });
      setEntries(await api<Entry[]>(path));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not review feedback");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4 rounded-[28px] border border-line bg-white p-6 sm:p-8">
      <div>
        <h3 className="text-xl font-bold">Improve the knowledge base</h3>
        <p className="mt-1 text-sm text-ink-3">Record a correction or business rule. An owner or admin reviews it before it guides another analysis.</p>
      </div>
      <input className="input w-full" maxLength={200} placeholder="Short title, e.g. Dealers are Accounts" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className="input min-h-28 w-full" maxLength={2000} placeholder="Explain the rule and when it applies…" value={content} onChange={(e) => setContent(e.target.value)} />
      <button className="btn-primary" disabled={busy || title.trim().length < 3 || content.trim().length < 10} onClick={submit}>
        {busy && <Spinner />} Submit feedback
      </button>
      <ErrorBox message={error} />
      {entries.length > 0 && (
        <div className="space-y-3 border-t border-line pt-4">
          {entries.map((entry) => (
            <div key={entry.id} className="rounded-xl bg-canvas p-4">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-semibold">{entry.title}</p>
                <span className="text-xs text-ink-4">{entry.status} · {entry.scope} scope{entry.source_version ? ` · v${entry.source_version}` : ""}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm text-ink-2">{entry.content}</p>
              {canReview && entry.status === "pending" && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn-secondary" disabled={busy} onClick={() => review(entry.id, "approve")}>Approve for this project</button>
                  <button className="btn-secondary" disabled={busy} onClick={() => review(entry.id, "approve", "organization")}>Share with organization</button>
                  <button className="btn-ghost" disabled={busy} onClick={() => review(entry.id, "reject")}>Reject</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
