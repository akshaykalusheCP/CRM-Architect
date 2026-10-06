"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AnalysisProgress } from "@/components/AnalysisProgress";
import { AppShell } from "@/components/AppShell";
import { DesignTab } from "@/components/DesignTab";
import { ExportTab } from "@/components/ExportTab";
import { BoxIcon, ChatQuestionIcon, DatabaseIcon, DocIcon } from "@/components/icons";
import { IntakeTab } from "@/components/IntakeTab";
import { QuestionsTab } from "@/components/QuestionsTab";
import { StepRail, type Step } from "@/components/StepRail";
import { Avatar, ErrorBox, Spinner, StatusBadge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Job, Project, Question, SourceFile } from "@/lib/types";

type Tab = "intake" | "questions" | "design" | "export";

const PLATFORM_LABEL: Record<string, string> = { salesforce: "Salesforce", zoho: "Zoho CRM", odoo: "Odoo", hubspot: "HubSpot" };

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [sources, setSources] = useState<SourceFile[]>([]);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tab, setTab] = useState<Tab>("intake");
  const [error, setError] = useState<string | null>(null);
  const [designKey, setDesignKey] = useState(0);
  const [initialTabSet, setInitialTabSet] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [p, s, q, j] = await Promise.all([
        api<Project>(`/projects/${id}`),
        api<SourceFile[]>(`/projects/${id}/sources`),
        api<Question[]>(`/projects/${id}/questions`),
        api<Job[]>(`/projects/${id}/jobs?limit=10`),
      ]);
      setProject((prev) => {
        if (prev && p.current_version !== prev.current_version) setDesignKey((k) => k + 1);
        return p;
      });
      setSources(s);
      setQuestions(q);
      setJobs(j);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load project");
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Open the project where the user's attention is needed.
  useEffect(() => {
    if (!project || initialTabSet) return;
    const open = questions.filter((q) => q.status === "open").length;
    setTab(!project.current_version ? "intake" : open ? "questions" : "design");
    setInitialTabSet(true);
  }, [project, questions, initialTabSet]);

  const analysis = jobs.find((j) => j.type === "analyze");
  const analysing = !!analysis && (analysis.status === "queued" || analysis.status === "running");
  const processing = sources.some((s) => s.status === "uploaded" || s.status === "processing");

  useEffect(() => {
    if (!analysing && !processing) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [analysing, processing, refresh]);

  // When an analysis finishes, move the user to the next thing to do.
  const [lastJob, setLastJob] = useState<string | null>(null);
  useEffect(() => {
    if (!analysis) return;
    if (analysis.status === "succeeded" && lastJob === analysis.id + ":running") {
      const open = questions.filter((q) => q.status === "open").length;
      setTab(open ? "questions" : "design");
    }
    setLastJob(analysis.id + ":" + (analysing ? "running" : analysis.status));
  }, [analysis, analysing, questions, lastJob]);

  async function startAnalysis() {
    setError(null);
    try {
      await api(`/projects/${id}/analyze`, { method: "POST" });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not start analysis");
    }
  }

  if (!project) {
    return (
      <AppShell>
        {error ? <ErrorBox message={error} /> : <div className="flex justify-center py-20 text-ink-4"><Spinner className="h-6 w-6" /></div>}
      </AppShell>
    );
  }

  const open = questions.filter((q) => q.status === "open").length;
  const handled = questions.filter((q) => q.status === "answered" || q.status === "dismissed").length;
  const totalQ = open + handled;
  const hasInput = project.description.trim().length > 0 || sources.length > 0;
  const v = project.current_version;

  const steps: Step<Tab>[] = [
    {
      key: "intake",
      title: "Business input",
      detail: [project.description.trim() ? "Narrative" : null, `${sources.length} file${sources.length === 1 ? "" : "s"}`]
        .filter(Boolean)
        .join(" · "),
      state: hasInput && v ? "done" : "todo",
      icon: DocIcon,
    },
    {
      key: "questions",
      title: "Clarifications",
      detail: totalQ ? (open ? `${open} still open` : "All handled") : "After the first analysis",
      state: totalQ && open === 0 ? "done" : totalQ ? "partial" : "todo",
      icon: ChatQuestionIcon,
      progress: totalQ ? { done: handled, total: totalQ, label: `${handled} of ${totalQ} answered` } : undefined,
      disabled: !v && !totalQ,
    },
    {
      key: "design",
      title: "CRM design",
      detail: v ? `Version ${v}${open ? " · refine after answers" : ""}` : "Not generated yet",
      state: v ? "done" : "todo",
      icon: DatabaseIcon,
      disabled: !v,
    },
    {
      key: "export",
      title: "Build",
      detail: `${PLATFORM_LABEL[project.target_platform] ?? project.target_platform} package`,
      state: "todo",
      icon: BoxIcon,
      disabled: !v,
    },
  ];

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-4">
          <Avatar name={project.client_name} size="lg" />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-ink-4">
              <Link href="/" className="hover:text-ink">Projects</Link> / {project.name}
            </p>
            <h1 className="mt-0.5 text-[26px] leading-tight font-bold tracking-tight sm:truncate sm:text-[28px]">
              {project.name}{" "}
              <span className="hidden text-xl font-medium text-ink-3 sm:inline">
                · {project.client_name}
                {project.industry ? ` · ${project.industry}` : ""} · {PLATFORM_LABEL[project.target_platform] ?? project.target_platform}
              </span>
            </h1>
          </div>
          <StatusBadge status={project.status} />
        </div>

        <StepRail steps={steps} active={tab} onSelect={setTab} />

        {(analysing || analysis?.status === "failed" || error) && (
          <div className="space-y-3">
            {analysing && analysis && <AnalysisProgress job={analysis} />}
            {analysis?.status === "failed" && (
              <div className="space-y-1.5">
                <ErrorBox message={`Last analysis failed: ${analysis.error}`} />
                {/api key|credentials|settings|limit|rate/i.test(analysis.error ?? "") && (
                  <Link href="/settings" className="ml-1 text-sm font-semibold text-brand-600 hover:underline">Open AI settings →</Link>
                )}
              </div>
            )}
            <ErrorBox message={error} />
          </div>
        )}

        {tab === "intake" && (
          <IntakeTab project={project} sources={sources} busy={analysing} processing={processing} onChange={refresh} onAnalyze={startAnalysis} />
        )}
        {tab === "questions" && (
          <QuestionsTab project={project} questions={questions} busy={analysing} onChange={refresh} onAnalyze={startAnalysis} />
        )}
        {tab === "design" && v && <DesignTab key={designKey} project={project} onSaved={refresh} onBuild={() => setTab("export")} />}
        {tab === "export" && v && <ExportTab project={project} />}
      </div>
    </AppShell>
  );
}
