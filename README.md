# CRM Architect

An AI solution architect for CRM implementations. A consultant feeds in a client's business narrative,
spreadsheets and documents. The app then:

1. **Profiles the data.** It finds real header rows in messy sheets, infers column types, flags duplicates,
   detects relationships between files and extracts text from PDFs and DOCX.
2. **Designs the CRM.** The LLM produces a **canonical, platform-neutral business model**: entities, fields,
   lookups, pipelines, automations, roles, integrations, reports, a migration mapping and explicit assumptions.
   Every element records its evidence and a confidence score.
3. **Asks instead of guessing.** High-impact gaps become clarifying questions. Answers feed a refinement run
   that produces a new, versioned design.
4. **Validates deterministically.** Broken references, cycles, stage and picklist drift and similar problems are
   caught in code, auto-fixed where safe, and sent back to the LLM for one repair pass.
5. **Builds for the platform.** Platform adapters turn the canonical model into deployable configuration.
   **Salesforce** ships first; Zoho, Odoo and HubSpot plug into the same interface.
6. **Learns from reviewed feedback.** Built-in Salesforce guidance and approved project notes are selected
   for each analysis. Feedback starts pending; owners/admins approve it for one project or explicitly share
   it across the organisation. Pending and rejected notes never enter AI prompts.

```
narrative + files + answers ──► profiler ──► LLM (structured output) ──► validator/normaliser ──► model vN
                                                    ▲                           │
                                                    └──── repair errors ────────┘
model vN ──► Salesforce adapter ──► SFDX project (.zip)      model vN ──► Solution Design Document (.md)
```

## What the Salesforce package contains

- **Objects:** standard objects are reused where an entity matches (Account, Contact, Lead, Opportunity,
  Case, Product2, …). Custom objects get their own tab, a layout and an OWD sharing model.
- **Fields:** standard fields are mapped rather than duplicated. Custom fields follow platform rules:
  - master-detail is downgraded to a lookup where Salesforce disallows it, and there's a two-per-object limit;
  - uniqueness is kept only on types that support it;
  - checkbox defaults are set;
  - text over 255 characters becomes a long text area.
- **Opportunity stages, lead statuses and case statuses** are generated as `StandardValueSet`s, with won,
  closed and converted flags and probabilities.
- **Roles** carry the hierarchy. **Permission sets** carry object CRUD, field-level security and tab
  visibility. Required and master-detail fields are left out of FLS, as Salesforce requires.
- **Record-triggered Flows**, deployed as **Draft**:
  - field updates run as before-save flows;
  - tasks, e-mails and child records run as after-save flows.
  Anything that can't be generated safely (scheduled paths, notifications, callouts, assignment) becomes a
  manual step instead of broken metadata.
- **Migration kit:** a `Legacy_Id__c` external-id field for upserts, CSV templates in dependency order, and a
  column-to-field mapping with transforms.
- `README.md` inside the zip, with deploy commands, warnings and a manual-steps checklist.

Deploy with:

```bash
sf project deploy validate --source-dir force-app --target-org <alias>   # check only
sf project deploy start    --source-dir force-app --target-org <alias>
```

## Stack

| Layer | Tech |
|---|---|
| API | FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic 2 |
| Jobs | arq on Redis (LLM runs take minutes; failures are recorded on the job) |
| DB | PostgreSQL (SQLite works for tests and local hacking) |
| LLM | Pluggable `LLMProvider`: Anthropic (default, `claude-opus-5`, adaptive thinking, strict JSON-schema output, server-side refusal fallback), OpenAI, xAI Grok, Groq, and an offline `mock` |
| Web | Next.js 16 (App Router), Tailwind 4; `/api/*` is proxied, so auth cookies stay same-origin |

Security:

- httpOnly JWT session cookie plus a double-submit CSRF token; bearer tokens work for API clients.
- Argon2 password hashing and timing-safe login.
- Every query is scoped to the caller's organisation.
- Upload type and size limits, and path-safe storage keys.
- PII sample values are masked before anything is sent to the LLM.

## AI provider settings

Workspace owners and admins can configure the AI from **AI settings** in the app:
- pick the provider (Claude, OpenAI, xAI Grok, Groq or the offline demo mode);
- paste an API key and choose the model;
- for Claude, set the analysis depth;
- test the connection before switching.

Keys are encrypted at rest with `ENCRYPTION_KEY` (Fernet) and only a 4-character hint is shown after
saving. If no provider is chosen in the app, the server default from the environment (`LLM_PROVIDER` and the
matching key) is used.

## Knowledge and feedback

The **Knowledge base** screen shows built-in guidance, approved feedback, and imported Salesforce metadata.
Owners/admins can upload a Salesforce DX or Metadata API ZIP, select an industry, inspect parsed objects and
fields, edit their summaries, and approve the import together. The importer also extracts relationships, record types,
validation rules, external IDs, and possible lifecycle fields from object metadata. Separate category summaries cover
all Flows, reports, dashboards, report types, named/external credentials, remote sites, auth providers, permission sets,
profiles, layouts, Lightning pages, quick actions, apps, tabs, Apex classes/triggers, and org settings.
It reads configuration, not customer records. Salesforce CLI retrieves a Metadata API ZIP with
`sf project retrieve start --manifest manifest/package.xml --target-org <alias> --target-metadata-dir output`;
for Salesforce DX source format, select the project folder in the browser or ZIP the retrieved `force-app`
folder. Folder selection uploads only supported metadata XML and shows how many other files were skipped.
Unchanged entries from the same source and industry are skipped on reimport. Imported entries are grouped by source and can be approved or rejected together, while individual review
remains available. Only approved entries enter future analyses; industry
metadata is selected for projects with the same industry. The CRM design screen also accepts feedback tied to
a design version. Review status and scope are stored in `knowledge_entries`; each analysis records the IDs it
used. Retrieval uses keywords and needs no embedding service or vector database. Run `alembic upgrade head`
to create the new tables/columns. Shared knowledge stays within the same organisation.

## Run it

### Docker (everything)

```bash
cp .env.example .env          # set SECRET_KEY and ANTHROPIC_API_KEY (or LLM_PROVIDER=mock to try offline)
docker compose up --build     # http://localhost:3000
```

### Local development

```bash
docker compose up -d db redis

cd backend
uv sync
cp ../.env.example .env        # set ENV=development, DATABASE_URL, and a provider key (or LLM_PROVIDER=mock)
uv run alembic upgrade head
uv run uvicorn app.main:app --reload          # API on :8000, docs at /docs
uv run arq app.worker.WorkerSettings          # in another shell (or set JOB_BACKEND=inline)

cd ../frontend
npm install
npm run dev                                    # http://localhost:3000
```

### Tests

```bash
cd backend && uv run pytest && uv run ruff check app tests
cd frontend && npx tsc --noEmit && npm run build
```

## Project layout

```
backend/app/
  domain/business_model.py   canonical model (the contract between AI, UI and adapters)
  domain/validation.py       deterministic checks + safe normalisation
  ingestion/                 spreadsheet profiler, document text extraction, cross-file relationships
  llm/                       provider interface, Anthropic / OpenAI / mock, strict-schema conversion
  pipeline/                  prompts + discovery/refine/repair loop
  adapters/salesforce/       planner (platform rules), metadata XML, flows, generator
  exports/design_doc.py      platform-neutral solution design document
  services/                  jobs, analysis orchestration, sources, storage
  api/                       routes, schemas, auth/CSRF/tenant dependencies
frontend/
  app/projects/[id]          workspace: intake → clarifications → design review/edit → build
```

## Adding a CRM platform

Implement `PlatformAdapter.generate(model, project_name) -> ExportArtifact` in `backend/app/adapters/<platform>/`
and register it in `adapters/registry.py`. The Salesforce adapter shows the pattern:

1. A **planner** maps canonical entities and fields to native objects and applies platform limits,
   recording a warning whenever it downgrades something.
2. **Renderers** emit the native configuration.
3. Anything that can't be generated safely is added to `manual_steps`.

## Roadmap

- Zoho (modules, fields, Blueprint), Odoo (generated module), HubSpot adapters.
- Direct deploy through OAuth connections, plus an audit and diff against an existing org.
- A migration runner: cleanse, dedupe and load the source files using the mapping.
- Vertical templates (real estate, manufacturing, education, …) to seed discovery.
- OCR for scanned PDFs, S3 storage, team invites, and an audit log.
# CRM-Architect
