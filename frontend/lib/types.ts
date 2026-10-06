export type User = { id: string; email: string; full_name: string; role: string; org_id: string; org_name: string };

export type ProjectStatus = "draft" | "analyzing" | "needs_input" | "ready" | "error";

export type Project = {
  id: string;
  name: string;
  client_name: string;
  industry: string | null;
  target_platform: string;
  description: string;
  status: ProjectStatus;
  current_version: number | null;
  created_at: string;
  updated_at: string;
};

export type ColumnProfile = {
  name: string;
  inferred_type: string;
  fill_rate: number;
  distinct: number;
  samples: string[];
  top_values?: Record<string, number>;
  candidate_key?: boolean;
};

export type SheetProfile = {
  sheet: string;
  header_row: number;
  row_count: number;
  column_count: number;
  truncated: boolean;
  columns: ColumnProfile[];
  possible_duplicates: { column: string; duplicate_values: number }[];
};

export type SourceFile = {
  id: string;
  filename: string;
  size_bytes: number;
  kind: "tabular" | "document";
  status: "uploaded" | "processing" | "processed" | "failed";
  profile: { type: "tabular"; sheets: SheetProfile[] } | { type: "document"; chars: number; truncated: boolean } | null;
  error: string | null;
  created_at: string;
};

export type Question = {
  id: string;
  key: string;
  text: string;
  why: string;
  category: string;
  priority: "high" | "medium" | "low";
  suggested_answers: string[];
  related: string[];
  status: "open" | "answered" | "dismissed" | "resolved";
  answer: string | null;
  answered_at: string | null;
  asked_in_version: number | null;
  applied_in_version: number | null;
};

export type Job = {
  id: string;
  type: "analyze" | "process_source";
  status: "queued" | "running" | "succeeded" | "failed";
  progress: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Issue = { severity: "error" | "warning" | "info"; code: string; path: string; message: string };

export type Provenance = { source: string; reference: string | null; confidence: number };

export type Field = {
  key: string;
  label: string;
  type: string;
  description: string | null;
  required: boolean;
  unique: boolean;
  external_id: boolean;
  options: { value: string; label: string; is_default: boolean }[];
  reference_entity: string | null;
  relationship: "lookup" | "master_detail" | null;
  pii: boolean;
  provenance: Provenance;
};

export type Entity = {
  key: string;
  label: string;
  plural_label: string;
  kind: string;
  description: string | null;
  fields: Field[];
  provenance: Provenance;
};

export type BusinessModel = {
  profile: {
    name: string;
    industry: string;
    sub_industry: string | null;
    description: string;
    sales_model: string;
    revenue_models: string[];
    regions: string[];
    currencies: string[];
    crm_user_count: number | null;
    compliance: string[];
    goals: string[];
    pain_points: string[];
    out_of_scope: string[];
  };
  entities: Entity[];
  processes: {
    key: string;
    name: string;
    entity: string;
    stage_field: string;
    description: string | null;
    stages: { key: string; label: string; category: string; probability: number | null }[];
  }[];
  automations: {
    key: string;
    name: string;
    entity: string;
    description: string;
    trigger: { type: string; schedule: string | null; relative_date_field: string | null; offset_days: number | null };
    conditions: { field: string; operator: string; value: string | null }[];
    condition_logic: string;
    actions: {
      type: string;
      description: string;
      target_field: string | null;
      value: string | null;
      subject: string | null;
      recipient: string | null;
      related_entity: string | null;
      due_in_days: number | null;
    }[];
  }[];
  roles: {
    key: string;
    label: string;
    description: string | null;
    reports_to: string | null;
    permissions: { entity: string; read: boolean; create: boolean; edit: boolean; delete: boolean; view_all: boolean }[];
  }[];
  integrations: { key: string; system: string; direction: string; frequency: string; entities: string[]; description: string }[];
  reports: { key: string; name: string; entity: string; kind: string; group_by: string[]; metrics: string[] }[];
  data_mappings: { source: string; column: string; entity: string; field: string; transform: string | null }[];
  assumptions: { statement: string; rationale: string | null; confidence: number }[];
};

export type Version = {
  version: number;
  source: string;
  summary: string;
  changes: string[];
  issues: Issue[];
  model: BusinessModel;
  llm_usage: Record<string, unknown> | null;
  created_at: string;
};

export type VersionSummary = {
  version: number;
  source: string;
  summary: string;
  created_at: string;
  error_count: number;
  warning_count: number;
};

export type Platform = { key: string; label: string; description: string; available: boolean };

export type Export = {
  id: string;
  version: number;
  kind: string;
  filename: string;
  report: { warnings?: string[]; manual_steps?: string[]; counts?: Record<string, number>; [k: string]: unknown };
  created_at: string;
};
