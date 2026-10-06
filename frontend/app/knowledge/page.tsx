"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { ErrorBox, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

type Field = { api_name: string; label: string | null; type: string | null; reference_to: string | null; values: string[] };
type Metadata = {
  object_api_name?: string;
  source_format?: string | null;
  fields?: Field[];
  metadata_type?: string;
  title?: string;
  area?: string;
  count?: number;
  items?: { api_name: string; label?: string; name?: string; title?: string; metadata_type: string; folder?: string; object?: string; triggerType?: string; processType?: string; reportType?: string; authenticationProtocol?: string; status?: string; columns?: string[]; component_count?: number }[];
  record_types?: { api_name: string; label: string | null; active: boolean }[];
  validation_rules?: { api_name: string; active: boolean; error_message: string | null }[];
  flows?: { api_name: string; label: string; trigger_type: string | null; status: string | null }[];
  analysis?: { relationship_targets: string[]; lifecycle_candidates?: string[]; migration_keys?: string[]; computed_fields?: string[]; warnings: string[] };
};
type Entry = {
  id: string; title: string; content: string; industry: string | null; kind: string;
  status: string; scope: "project" | "organization"; origin_project_id: string | null;
  source_name: string | null; structured: Metadata | null; created_at: string;
};
type Catalog = { can_manage: boolean; builtin: { id: string; title: string; content: string }[]; entries: Entry[]; industries: string[] };
type Section = "objects" | "automations" | "reports" | "integrations" | "access" | "experience" | "code" | "guidance";
const SECTIONS: { id: Section; title: string; description: string }[] = [
  { id: "objects", title: "Objects & fields", description: "Data model, fields, relationships and validation" },
  { id: "automations", title: "Automations", description: "Flows, approvals, assignment and duplicate rules" },
  { id: "reports", title: "Reports & dashboards", description: "Reports, dashboards and report types" },
  { id: "integrations", title: "Integrations", description: "Credentials, remote sites and auth providers" },
  { id: "access", title: "Access & sharing", description: "Permission sets, profiles and sharing rules" },
  { id: "experience", title: "Screens & actions", description: "Layouts, Lightning pages, apps and actions" },
  { id: "code", title: "Code & settings", description: "Apex and org configuration" },
  { id: "guidance", title: "Guidance & feedback", description: "Built-in guidance and approved project feedback" },
];

function entrySection(entry: Entry): Section {
  if (entry.kind === "salesforce_metadata") return "objects";
  if (entry.kind !== "salesforce_category") return "guidance";
  switch (entry.structured?.area) {
    case "automation": return "automations";
    case "reporting": return "reports";
    case "integration": return "integrations";
    case "security": return "access";
    case "experience": return "experience";
    default: return "code";
  }
}

function entryMatches(entry: Entry, query: string): boolean {
  if (!query) return true;
  const metadata = entry.structured;
  const terms = [entry.title, entry.content, metadata?.object_api_name,
    ...(metadata?.fields?.flatMap((field) => [field.api_name, field.label, field.type]) ?? []),
    ...(metadata?.items?.flatMap((item) => [item.api_name, item.label, item.name, item.title, item.object, item.folder]) ?? []),
  ];
  return terms.some((value) => value?.toLowerCase().includes(query));
}

function supportedMetadataPath(path: string): boolean {
  const value = path.replaceAll("\\", "/");
  return /(?:^|\/)objects\/(?:[^/]+\/)?[^/]+\.object(?:-meta\.xml)?$/.test(value)
    || /(?:^|\/)objects\/[^/]+\/(?:fields\/[^/]+\.field-meta\.xml|recordTypes\/[^/]+\.recordType-meta\.xml|validationRules\/[^/]+\.validationRule-meta\.xml)$/.test(value)
    || /(?:^|\/)flows\/[^/]+\.flow(?:-meta\.xml)?$/.test(value)
    || /(?:^|\/)(?:reports\/(?:[^/]+\/)?[^/]+\.report|dashboards\/(?:[^/]+\/)?[^/]+\.dashboard|reportTypes\/[^/]+\.reportType|namedCredentials\/[^/]+\.namedCredential|externalCredentials\/[^/]+\.externalCredential|remoteSiteSettings\/[^/]+\.remoteSite|authproviders\/[^/]+\.authprovider|permissionsets\/[^/]+\.permissionset|profiles\/[^/]+\.profile|layouts\/[^/]+\.layout|flexipages\/[^/]+\.flexipage|quickActions\/[^/]+\.quickAction|applications\/[^/]+\.app|tabs\/[^/]+\.tab|classes\/[^/]+\.cls-meta\.xml|triggers\/[^/]+\.trigger-meta\.xml|settings\/[^/]+\.settings|flowDefinitions\/[^/]+\.flowDefinition|duplicateRules\/[^/]+\.duplicateRule|assignmentRules\/[^/]+\.assignmentRules|autoResponseRules\/[^/]+\.autoResponseRules|escalationRules\/[^/]+\.escalationRules|workflowRules\/[^/]+\.workflow|approvalProcesses\/[^/]+\.approvalProcess|sharingRules\/[^/]+\.sharingRules|customMetadata\/[^/]+\.md)(?:-meta\.xml)?$/.test(value);
}

function importGroupKey(entry: Entry): string {
  return [entry.source_name, entry.industry, entry.created_at.slice(0, 16), entry.status].join("|");
}

export default function KnowledgePage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [industry, setIndustry] = useState("");
  const [filter, setFilter] = useState("all");
  const [section, setSection] = useState<Section | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [browseObjects, setBrowseObjects] = useState(false);
  const [visibleLimit, setVisibleLimit] = useState(20);
  const [file, setFile] = useState<File | null>(null);
  const [folderFiles, setFolderFiles] = useState<File[]>([]);
  const folderInput = useRef<HTMLInputElement | null>(null);
  const zipInput = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [draft, setDraft] = useState({ title: "", content: "", industry: "" });

  const refresh = useCallback(async () => setCatalog(await api<Catalog>("/knowledge")), []);
  useEffect(() => { refresh().catch(() => setError("Could not load knowledge base")); }, [refresh]);
  const setFolderInput = useCallback((input: HTMLInputElement | null) => {
    folderInput.current = input;
    if (input) {
      input.setAttribute("webkitdirectory", "");
      (input as HTMLInputElement & { webkitdirectory: boolean }).webkitdirectory = true;
    }
  }, []);

  async function upload() {
    if ((!file && !folderFiles.length) || !industry.trim()) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const form = new FormData();
      form.append("industry", industry.trim());
      const supported = folderFiles.filter((item) => supportedMetadataPath(item.webkitRelativePath || item.name));
      let result: { imported: number };
      if (folderFiles.length) {
        if (!supported.length) throw new Error("No supported Salesforce metadata found in this folder");
        for (const item of supported) {
          const path = item.webkitRelativePath || item.name;
          form.append("paths", path);
          form.append("files", item, item.name);
        }
        result = await api<{ imported: number }>("/knowledge/import-folder", { method: "POST", form });
      } else {
        form.append("file", file!);
        result = await api<{ imported: number }>("/knowledge/import", { method: "POST", form });
      }
      await refresh();
      setFilter(industry.trim());
      setFile(null);
      setFolderFiles([]);
      if (folderInput.current) folderInput.current.value = "";
      if (zipInput.current) zipInput.current.value = "";
      setNotice(result.imported ? `Imported ${result.imported} new entries. Review the import below.` : "This source is already up to date. No new entries need review.");
    } catch (e) { setError(e instanceof Error ? e.message : "Import failed"); }
    finally { setBusy(false); }
  }

  async function review(id: string, decision: "approve" | "reject", scope: "project" | "organization" = "project") {
    setBusy(true); setError(null);
    try {
      await api(`/knowledge/${id}/review`, { method: "POST", json: { decision, scope } });
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Review failed"); }
    finally { setBusy(false); }
  }

  async function reviewGroup(items: Entry[], decision: "approve" | "reject") {
    setBusy(true); setError(null);
    try {
      const result = await api<{ reviewed: number }>("/knowledge/review-bulk", {
        method: "POST", json: { ids: items.map((item) => item.id), decision },
      });
      setExpandedGroup(null);
      setNotice(`${result.reviewed} entries ${decision === "approve" ? "approved" : "rejected"}.`);
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Could not review import"); }
    finally { setBusy(false); }
  }

  async function save(id: string) {
    setBusy(true); setError(null);
    try {
      await api(`/knowledge/${id}`, { method: "PATCH", json: draft });
      setEditing(null);
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Save failed"); }
    finally { setBusy(false); }
  }

  const entries = catalog?.entries.filter((e) => filter === "all" || e.industry?.toLowerCase() === filter.toLowerCase()) ?? [];
  const pendingGroups = new Map<string, Entry[]>();
  for (const entry of entries.filter((item) => item.kind.startsWith("salesforce_") && item.status === "pending")) {
    const key = importGroupKey(entry);
    pendingGroups.set(key, [...(pendingGroups.get(key) ?? []), entry]);
  }
  const sectionEntries = entries.filter((entry) => section !== null && entrySection(entry) === section && (statusFilter === "all" || entry.status === statusFilter) && entryMatches(entry, search.trim().toLowerCase()));
  const sectionCounts = Object.fromEntries(SECTIONS.map((item) => [item.id, entries.filter((entry) => entrySection(entry) === item.id).reduce((sum, entry) => sum + (entry.structured?.count ?? 1), 0) + (item.id === "guidance" ? catalog?.builtin.length ?? 0 : 0)]));
  const fieldCount = entries.filter((entry) => entry.kind === "salesforce_metadata").reduce((sum, entry) => sum + (entry.structured?.fields?.length ?? 0), 0);
  const relationshipCount = entries.filter((entry) => entry.kind === "salesforce_metadata").reduce((sum, entry) => sum + (entry.structured?.analysis?.relationship_targets.length ?? 0), 0);
  const recordTypeCount = entries.filter((entry) => entry.kind === "salesforce_metadata").reduce((sum, entry) => sum + (entry.structured?.record_types?.length ?? 0), 0);
  const validationCount = entries.filter((entry) => entry.kind === "salesforce_metadata").reduce((sum, entry) => sum + (entry.structured?.validation_rules?.length ?? 0), 0);
  const showObjectResults = section !== "objects" || browseObjects || search.trim().length > 0;
  const visibleEntries = showObjectResults ? sectionEntries.slice(0, visibleLimit) : [];
  const industries = catalog?.industries ?? [];
  const supportedFolderFiles = folderFiles.filter((item) => supportedMetadataPath(item.webkitRelativePath || item.name));

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <p className="eyebrow">Workspace knowledge</p>
          <h1 className="mt-1 text-3xl font-bold">Knowledge base</h1>
          <p className="mt-2 text-ink-3">Bring in Salesforce configuration, review it once, then explore it by topic.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {[["1", "Import", "Choose a ZIP or Salesforce project folder"], ["2", "Review", "Approve the import with one action"], ["3", "Explore", "Browse parsed configuration by topic"], ["4", "Use", "Apply approved knowledge to CRM projects"], ["5", "Improve", "Project feedback returns here for review"]].map(([number, title, description]) => (
            <div key={number} className="card flex items-start gap-3 p-4">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-100 font-bold text-brand-700">{number}</span>
              <div><p className="font-semibold">{title}</p><p className="mt-1 text-sm text-ink-3">{description}</p></div>
            </div>
          ))}
        </div>
        {catalog?.can_manage && (
          <section className="card space-y-4 p-6">
            <div>
              <h2 className="text-xl font-bold">1. Import Salesforce metadata</h2>
              <p className="mt-1 text-sm text-ink-3">Choose an industry and upload a ZIP or the complete Salesforce project folder.</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto_auto] sm:items-end">
              <label className="text-sm font-medium">Industry
                <input className="input mt-1 w-full" list="knowledge-industries" placeholder="Healthcare" maxLength={120} value={industry} onChange={(e) => setIndustry(e.target.value)} />
                <datalist id="knowledge-industries">{industries.map((value) => <option key={value} value={value} />)}</datalist>
              </label>
              <input ref={zipInput} className="sr-only" type="file" accept=".zip" aria-label="Choose metadata ZIP" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setFolderFiles([]); if (folderInput.current) folderInput.current.value = ""; }} />
              <input ref={setFolderInput} className="sr-only" type="file" multiple aria-label="Choose metadata folder" onChange={(e) => { setFolderFiles(Array.from(e.target.files ?? [])); setFile(null); if (zipInput.current) zipInput.current.value = ""; }} />
              <button type="button" className="btn-secondary" onClick={() => zipInput.current?.click()}>Select ZIP</button>
              <button type="button" className="btn-secondary" onClick={() => folderInput.current?.click()}>Select folder</button>
              <button className="btn-primary" disabled={busy || (!file && !supportedFolderFiles.length) || industry.trim().length < 2} onClick={upload}>
                {busy && <Spinner />} Import
              </button>
            </div>
            {file && <p className="text-sm text-ink-3">Selected ZIP: {file.name}</p>}
            {!!folderFiles.length && <p className="text-sm text-ink-3">Selected {folderFiles.length} files. {supportedFolderFiles.length} supported metadata XML files will be imported; the rest will be skipped.</p>}
            <p className="text-xs text-ink-4">In the folder picker, select the top-level project folder and confirm it. The app reads supported configuration files inside it.</p>
            <details className="text-sm text-ink-3">
              <summary className="cursor-pointer font-semibold text-brand-600">How do I get a Salesforce ZIP?</summary>
              <p className="mt-2">Use the ZIP produced by a Salesforce Metadata API retrieve. Salesforce CLI can create one with <code>sf project retrieve start --manifest manifest/package.xml --target-org YOUR_ORG --target-metadata-dir output</code>. For SFDX source, select the project folder directly; the browser will include its nested <code>force-app</code> files.</p>
              <p className="mt-1">The ZIP can contain supported Salesforce configuration XML. Imported configuration is reviewed as a group. Customer records are not imported.</p>
            </details>
          </section>
        )}
        <ErrorBox message={error} />
        {notice && <p className="rounded-xl bg-[#E3F4E8] px-4 py-3 text-sm text-[#0D5C27]">{notice}</p>}
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-xl font-bold">2. Review imports</h2>
            <span className="text-sm text-ink-3">{[...pendingGroups].length} awaiting review</span>
          </div>
          {[...pendingGroups].length === 0 && <div className="card p-5 text-sm text-ink-3">No imports need review.</div>}
          {[...pendingGroups].map(([key, items]) => {
            const first = items[0];
            const objectCount = items.filter((item) => item.kind === "salesforce_metadata").length;
            const fieldCount = items.reduce((sum, item) => sum + (item.structured?.fields?.length ?? 0), 0);
            const categories = items.filter((item) => item.kind === "salesforce_category");
            return (
              <section key={key} className="card space-y-3 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-bold">{first.source_name || "Salesforce metadata import"}</h3>
                    <p className="mt-1 text-sm text-ink-3">{first.industry || "All industries"} · {objectCount} objects · {fieldCount} fields · {categories.reduce((sum, item) => sum + (item.structured?.count ?? 0), 0)} other metadata items</p>
                    <p className="mt-2 text-xs text-ink-4">{categories.map((item) => item.structured?.title || item.title).join(" · ") || "Objects and fields"}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-secondary" onClick={() => setExpandedGroup(expandedGroup === key ? null : key)}>{expandedGroup === key ? "Hide contents" : "See contents"}</button>
                    {catalog?.can_manage && <>
                      <button className="btn-primary" disabled={busy} onClick={() => reviewGroup(items, "approve")}>Approve import</button>
                      <button className="btn-ghost" disabled={busy} onClick={() => reviewGroup(items, "reject")}>Reject all</button>
                    </>}
                  </div>
                </div>
                {expandedGroup === key && <div className="flex flex-wrap gap-2 border-t border-line pt-3 text-sm text-ink-2">
                  {objectCount > 0 && <span className="rounded-full bg-chip px-3 py-1">Objects & fields: {objectCount} objects, {fieldCount} fields</span>}
                  {categories.map((item) => <span key={item.id} className="rounded-full bg-chip px-3 py-1">{item.structured?.title || item.title}: {item.structured?.count ?? 0}</span>)}
                </div>}
              </section>
            );
          })}
        </section>
        <section className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-xl font-bold">3. Explore knowledge</h2><p className="mt-1 text-sm text-ink-3">Choose a topic to see exactly what was imported.</p></div>
            <select className="input max-w-56" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by industry">
              <option value="all">All industries</option>
              {industries.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {SECTIONS.map((item) => <button key={item.id} type="button" onClick={() => { setSection(item.id); setSearch(""); setBrowseObjects(false); setVisibleLimit(20); }} aria-pressed={section === item.id} className={`rounded-2xl border p-4 text-left transition ${section === item.id ? "border-brand-600 bg-brand-50 ring-2 ring-brand-100" : "border-line bg-white hover:border-brand-200"}`}>
              <span className="flex items-center justify-between gap-2"><strong>{item.title}</strong><span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-brand-700">{sectionCounts[item.id] ?? 0}</span></span>
              <span className="mt-1 block text-sm text-ink-3">{item.id === "objects" ? `${fieldCount} fields · ${item.description}` : item.description}</span>
            </button>)}
          </div>
          {section === null && <div className="card p-6">
            <h3 className="font-bold">Choose a topic above</h3>
            <p className="mt-1 text-sm text-ink-3">Your import contains {sectionCounts.objects ?? 0} objects and {fieldCount} fields. Open a topic for a summary, then search or browse its records when needed.</p>
            {entries.some((entry) => entry.kind === "salesforce_metadata") && !entries.some((entry) => entry.kind === "salesforce_category") && <p className="mt-3 rounded-xl bg-[#FEF1C7] p-3 text-sm text-[#7A4A00]">This import was created before other metadata types were supported. Import the same folder again to add automations, reports and integrations. Unchanged objects will be skipped.</p>}
          </div>}
          {section === "objects" && <div className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div><h3 className="font-bold">Data model overview</h3><p className="mt-1 text-sm text-ink-3">Search for a specific object or field. The full object list stays hidden until you ask for it.</p></div>
              <button type="button" className="btn-secondary" onClick={() => { setBrowseObjects(!browseObjects); setVisibleLimit(20); }}>{browseObjects ? "Hide objects" : "Browse objects"}</button>
            </div>
            <div className="mt-4 grid gap-2 text-sm sm:grid-cols-4">
              {[["Objects", sectionCounts.objects ?? 0], ["Fields", fieldCount], ["Relationships", relationshipCount], ["Record types", recordTypeCount], ["Validation rules", validationCount]].map(([label, count]) => <div key={label} className="rounded-xl bg-canvas px-4 py-3"><strong className="block text-lg">{count}</strong><span className="text-ink-3">{label}</span></div>)}
            </div>
          </div>}
          {section && <div className="flex flex-wrap gap-3">
            <input className="input min-w-60 flex-1" aria-label="Search knowledge" placeholder={section === "objects" ? "Search object or field name..." : `Search ${SECTIONS.find((item) => item.id === section)?.title.toLowerCase()}...`} value={search} onChange={(event) => { setSearch(event.target.value); setVisibleLimit(20); }} />
            <select className="input max-w-48" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter by review status">
              <option value="all">All statuses</option><option value="approved">Approved</option><option value="pending">Pending</option><option value="rejected">Rejected</option>
            </select>
          </div>}
          {section === "guidance" && catalog?.builtin.map((item) => <article key={item.id} className="card p-5"><span className="text-xs font-semibold uppercase text-brand-600">Built-in · all industries</span><h3 className="mt-1 font-bold">{item.title}</h3><p className="mt-2 text-sm text-ink-2">{item.content}</p></article>)}
          {section && sectionEntries.length === 0 && <div className="card p-6 text-sm text-ink-3">No {SECTIONS.find((item) => item.id === section)?.title.toLowerCase()} found for these filters. {section !== "guidance" && <span>Import the same Salesforce folder again to add this metadata. Existing objects will not be duplicated.</span>}</div>}
          <div className="grid gap-3">
          {visibleEntries.map((entry) => (
            <article key={entry.id} className="card space-y-3 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-bold">{entry.title}</h3>
                <span className="text-xs text-ink-4">{entry.industry || "All industries"} · {entry.status} · {entry.scope} · {entry.kind.startsWith("salesforce_") ? "Salesforce metadata" : "Feedback"}</span>
              </div>
              {entry.origin_project_id && <Link className="text-xs text-brand-600 hover:underline" href={`/projects/${entry.origin_project_id}`}>View source project →</Link>}
              {entry.source_name && <p className="text-xs text-ink-4">Source: {entry.source_name}</p>}
              <details open={editing === entry.id || undefined} className="rounded-xl bg-canvas p-3 text-sm">
                <summary className="cursor-pointer font-semibold">View {entry.structured?.count ? `${entry.structured.count} ${entry.structured.title?.toLowerCase() || "items"}` : entry.structured?.object_api_name ? `${entry.structured.fields?.length ?? 0} fields and configuration` : "knowledge"}</summary>
              {editing === entry.id ? (
                <div className="space-y-2">
                  <input className="input w-full" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} aria-label="Title" />
                  <input className="input w-full" value={draft.industry} onChange={(e) => setDraft({ ...draft, industry: e.target.value })} aria-label="Industry" />
                  <textarea className="input min-h-32 w-full" value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} aria-label="Knowledge summary" />
                  <div className="flex gap-2"><button className="btn-primary" disabled={busy} onClick={() => save(entry.id)}>Save for review</button><button className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button></div>
                </div>
              ) : <p className="text-sm text-ink-2">{entry.content}</p>}
              {entry.structured?.items && (
                <details className="rounded-xl bg-canvas p-3 text-sm">
                  <summary className="cursor-pointer font-semibold">Parsed {entry.structured.count} {entry.structured.metadata_type}</summary>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {entry.structured.items.filter((item) => !search || entry.title.toLowerCase().includes(search.trim().toLowerCase()) || [item.api_name, item.label, item.name, item.title, item.object, item.folder].some((value) => value?.toLowerCase().includes(search.trim().toLowerCase()))).map((item, index) => <div key={`${item.api_name}-${index}`} className="rounded-lg bg-white px-3 py-2">
                      <span className="font-medium">{item.label || item.title || item.name || item.api_name}</span>
                      <span className="ml-2 text-xs text-ink-4">{item.api_name}{item.folder ? ` · ${item.folder}` : ""}{item.object ? ` · ${item.object}` : ""}{item.triggerType ? ` · ${item.triggerType}` : ""}{item.processType ? ` · ${item.processType}` : ""}{item.reportType ? ` · ${item.reportType}` : ""}{item.authenticationProtocol ? ` · ${item.authenticationProtocol}` : ""}{item.status ? ` · ${item.status}` : ""}{item.component_count !== undefined ? ` · ${item.component_count} components` : ""}</span>
                      {!!item.columns?.length && <p className="text-xs text-ink-3">Columns: {item.columns.join(", ")}</p>}
                    </div>)}
                  </div>
                </details>
              )}
              {entry.structured?.fields && (
                <details className="rounded-xl bg-canvas p-3 text-sm">
                  <summary className="cursor-pointer font-semibold">
                    Parsed configuration · {entry.structured.fields.length} fields · {entry.structured.record_types?.length ?? 0} record types · {entry.structured.flows?.length ?? 0} flows
                  </summary>
                  <p className="mt-3 text-xs text-ink-3">Source format: {entry.structured.source_format || "unknown"}</p>
                  {!!entry.structured.analysis?.relationship_targets.length && <p className="mt-2 text-sm"><strong>Relationships:</strong> {entry.structured.analysis.relationship_targets.join(", ")}</p>}
                  {!!entry.structured.analysis?.lifecycle_candidates?.length && <p className="mt-2 text-sm"><strong>Possible lifecycle fields:</strong> {entry.structured.analysis.lifecycle_candidates.join(", ")}</p>}
                  {!!entry.structured.analysis?.migration_keys?.length && <p className="mt-2 text-sm"><strong>Migration keys:</strong> {entry.structured.analysis.migration_keys.join(", ")}</p>}
                  {!!entry.structured.analysis?.computed_fields?.length && <p className="mt-2 text-sm"><strong>Calculated fields:</strong> {entry.structured.analysis.computed_fields.join(", ")}</p>}
                  {!!entry.structured.record_types?.length && <p className="mt-2 text-sm"><strong>Record types:</strong> {entry.structured.record_types.map((r) => r.label || r.api_name).join(", ")}</p>}
                  {!!entry.structured.validation_rules?.length && <p className="mt-2 text-sm"><strong>Validation rules:</strong> {entry.structured.validation_rules.map((r) => r.api_name).join(", ")}</p>}
                  {!!entry.structured.flows?.length && <p className="mt-2 text-sm"><strong>Record-triggered Flows:</strong> {entry.structured.flows.map((f) => `${f.label} (${f.trigger_type || "trigger unknown"})`).join(", ")}</p>}
                  {!!entry.structured.analysis?.warnings.length && <div className="mt-2 rounded-lg bg-[#FEF1C7] p-3 text-sm text-[#7A4A00]">{entry.structured.analysis.warnings.join(" ")}</div>}
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {entry.structured.fields.filter((field) => !search || entry.title.toLowerCase().includes(search.trim().toLowerCase()) || [field.api_name, field.label, field.type].some((value) => value?.toLowerCase().includes(search.trim().toLowerCase()))).map((field) => (
                      <div key={field.api_name} className="rounded-lg bg-white px-3 py-2">
                        <span className="font-medium">{field.label || field.api_name}</span>
                        <span className="ml-2 text-xs text-ink-4">{field.api_name} · {field.type || "unknown"}{field.reference_to ? ` → ${field.reference_to}` : ""}</span>
                        {field.values.length > 0 && <p className="text-xs text-ink-3">{field.values.join(", ")}</p>}
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {catalog?.can_manage && (
                <div className="flex flex-wrap gap-2">
                  {editing !== entry.id && <button className="btn-secondary" disabled={busy} onClick={() => { setEditing(entry.id); setDraft({ title: entry.title, content: entry.content, industry: entry.industry || "" }); }}>Edit</button>}
                  {entry.status === "pending" && <>
                    <button className="btn-primary" disabled={busy} onClick={() => review(entry.id, "approve")}>{entry.scope === "project" ? "Approve for project" : "Approve"}</button>
                    {entry.scope === "project" && <button className="btn-secondary" disabled={busy} onClick={() => review(entry.id, "approve", "organization")}>Share with organization</button>}
                    <button className="btn-ghost" disabled={busy} onClick={() => review(entry.id, "reject")}>Reject</button>
                  </>}
                </div>
              )}
              </details>
            </article>
          ))}
          {visibleEntries.length > 0 && <p className="text-sm text-ink-3">Showing {visibleEntries.length} of {sectionEntries.length} matching entries.</p>}
          {visibleEntries.length < sectionEntries.length && showObjectResults && <button type="button" className="btn-secondary justify-self-start" onClick={() => setVisibleLimit(visibleLimit + 20)}>Show 20 more</button>}
          {!catalog && <div className="flex justify-center py-16"><Spinner /></div>}
        </div>
        </section>
        <div className="card flex flex-wrap items-center justify-between gap-4 p-5">
          <div><h2 className="font-bold">Use this knowledge in a project</h2><p className="mt-1 text-sm text-ink-3">Approved entries inform future CRM designs for the matching industry. Feedback from a project can be reviewed here.</p></div>
          <Link href="/" className="btn-primary">Open projects</Link>
        </div>
      </div>
    </AppShell>
  );
}
