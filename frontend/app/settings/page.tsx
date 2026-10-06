"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { AlertIcon, CheckIcon, KeyIcon, SparklesIcon } from "@/components/icons";
import { Avatar, Badge, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";

type ProviderState = {
  key: string;
  label: string;
  needs_key: boolean;
  key_url: string | null;
  configured: boolean;
  key_hint: string | null;
  model: string;
  default_model: string;
  suggested_models: string[];
  supports_effort: boolean;
  effort: string | null;
  updated_at: string | null;
};

type LLMSettings = {
  active_provider: string | null;
  server_default: { provider: string; label: string };
  efforts: string[];
  can_edit: boolean;
  providers: ProviderState[];
};

type TestResult = { ok: boolean; message: string };

const EFFORT_HELP: Record<string, string> = {
  low: "fastest, cheapest",
  medium: "balanced",
  high: "recommended",
  xhigh: "deeper analysis",
  max: "most thorough, slowest",
};

function ProviderCard({
  p,
  active,
  settings,
  onSaved,
}: {
  p: ProviderState;
  active: boolean;
  settings: LLMSettings;
  onSaved: (s: LLMSettings) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(p.model);
  const [effort, setEffort] = useState(p.effort ?? "high");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const editable = settings.can_edit;
  const dirty = apiKey.trim() !== "" || model !== p.model || (p.supports_effort && effort !== p.effort);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Request failed");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    const body: Record<string, string> = { model };
    if (apiKey.trim()) body.api_key = apiKey.trim();
    if (p.supports_effort) body.effort = effort;
    const s = await run("save", () => api<LLMSettings>(`/settings/llm/providers/${p.key}`, { method: "PUT", json: body }));
    if (s) {
      setApiKey("");
      setTest(null);
      onSaved(s);
    }
  }

  async function testConnection() {
    setTest(null);
    const r = await run("test", () =>
      api<TestResult>("/settings/llm/test", { json: { provider: p.key, api_key: apiKey.trim() || null, model } }),
    );
    if (r) setTest(r);
  }

  async function activate() {
    const s = await run("activate", () => api<LLMSettings>("/settings/llm/active", { method: "PUT", json: { provider: p.key } }));
    if (s) onSaved(s);
  }

  async function removeKey() {
    if (!confirm(`Remove the saved ${p.label} API key?`)) return;
    const s = await run("remove", () => api<LLMSettings>(`/settings/llm/providers/${p.key}/key`, { method: "DELETE" }));
    if (s) onSaved(s);
  }

  return (
    <div className={`card p-5 ${active ? "border-brand-500 ring-2 ring-brand-100" : ""}`}>
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Avatar name={p.label} />
          <div>
            <h2 className="flex flex-wrap items-center gap-2 font-semibold">
              {p.label}
              {active && <Badge tone="blue">In use</Badge>}
              {p.needs_key && (p.configured ? <Badge tone="green">Key {p.key_hint}</Badge> : <Badge>No key</Badge>)}
            </h2>
            {p.updated_at ? <p className="text-xs text-ink-4">Updated {timeAgo(p.updated_at)}</p> : <p className="text-xs text-ink-4">{p.needs_key ? "Not configured" : "No key needed"}</p>}
          </div>
        </div>
        {editable && !active && (
          <button className="btn-secondary" disabled={!!busy || (p.needs_key && !p.configured)} onClick={activate}
            title={p.needs_key && !p.configured ? "Save an API key first" : undefined}>
            {busy === "activate" && <Spinner />} Use this provider
          </button>
        )}
      </div>

      {p.needs_key ? (
        <div className="space-y-3">
          <div>
            <label className="label" htmlFor={`key-${p.key}`}>API key</label>
            <input
              id={`key-${p.key}`}
              type="password"
              autoComplete="off"
              className="input font-mono"
              disabled={!editable}
              placeholder={p.configured ? `Saved (${p.key_hint}); enter a new key to replace it` : "Paste API key"}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            {p.key_url && (
              <a className="mt-1 inline-block text-xs text-brand-600 hover:underline" href={p.key_url} target="_blank" rel="noreferrer">
                Get a key
              </a>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor={`model-${p.key}`}>Model</label>
              <input id={`model-${p.key}`} className="input" list={`models-${p.key}`} disabled={!editable}
                value={model} onChange={(e) => setModel(e.target.value)} />
              <datalist id={`models-${p.key}`}>
                {p.suggested_models.map((m) => <option key={m} value={m} />)}
              </datalist>
            </div>
            {p.supports_effort && (
              <div>
                <label className="label" htmlFor={`effort-${p.key}`}>Analysis depth</label>
                <select id={`effort-${p.key}`} className="input" disabled={!editable} value={effort} onChange={(e) => setEffort(e.target.value)}>
                  {settings.efforts.map((e) => <option key={e} value={e}>{e} · {EFFORT_HELP[e]}</option>)}
                </select>
              </div>
            )}
          </div>
        </div>
      ) : (
        <p className="text-sm text-ink-4">
          Builds a rough design from spreadsheet columns without calling any AI. Useful for demos and trying the workflow.
        </p>
      )}

      <ErrorBox message={error} />
      {test && (
        <div className={`mt-3 flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm ${test.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
          {test.ok ? <CheckIcon size={16} className="mt-0.5 shrink-0" /> : <AlertIcon size={16} className="mt-0.5 shrink-0" />}
          <span className="min-w-0 break-words">{test.message}</span>
        </div>
      )}

      {editable && (
        <div className="mt-4 flex flex-wrap gap-2">
          {p.needs_key && (
            <button className="btn-primary" disabled={!!busy || !dirty} onClick={save}>
              {busy === "save" && <Spinner />} Save
            </button>
          )}
          <button className="btn-secondary" disabled={!!busy || (p.needs_key && !p.configured && !apiKey.trim())} onClick={testConnection}>
            {busy === "test" ? <Spinner /> : <KeyIcon size={16} />} Test connection
          </button>
          {p.needs_key && p.configured && (
            <button className="btn-ghost text-red-600" disabled={!!busy} onClick={removeKey}>Remove key</button>
          )}
        </div>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<LLMSettings>("/settings/llm").then(setSettings).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load"));
  }, []);

  async function useServerDefault() {
    try {
      setSettings(await api<LLMSettings>("/settings/llm/active", { method: "PUT", json: { provider: null } }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update");
    }
  }

  return (
    <AppShell>
      <PageHeader
        title="AI settings"
        description="Choose which AI analyses your clients' businesses. Keys are encrypted at rest and never shown again after saving."
      />
      <ErrorBox message={error} />
      {!settings ? (
        !error && <div className="flex justify-center py-12 text-ink-4"><Spinner className="h-6 w-6" /></div>
      ) : (
        <>
          {!settings.can_edit && (
            <div className="mb-4 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
              Only workspace owners and admins can change these settings.
            </div>
          )}
          <div className="card mb-6 flex flex-wrap items-center justify-between gap-3 p-4">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-600"><SparklesIcon /></span>
              <div>
                <p className="eyebrow">Currently using</p>
                <p className="font-semibold text-ink">
                  {settings.active_provider
                    ? settings.providers.find((p) => p.key === settings.active_provider)?.label
                    : `Server default (${settings.server_default.label})`}
                </p>
              </div>
            </div>
            {settings.can_edit && settings.active_provider && (
              <button className="btn-ghost" onClick={useServerDefault}>Switch to server default</button>
            )}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {settings.providers.map((p) => (
              <ProviderCard
                key={`${p.key}-${p.updated_at ?? ""}-${p.model}-${p.effort}`}
                p={p}
                active={settings.active_provider === p.key}
                settings={settings}
                onSaved={setSettings}
              />
            ))}
          </div>
        </>
      )}
    </AppShell>
  );
}
