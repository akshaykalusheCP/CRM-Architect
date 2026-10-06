"""Staged discovery for providers with small per-minute token limits (e.g. Groq free tier).

The complete design schema alone is ~4k tokens, so on small budgets the design is built in steps, each
with a compact output schema and a compact view of the design so far. Independent steps run in parallel
waves; a pacer keeps actual token use (what providers such as Groq charge) inside the per-minute budget.

  1. outline   - summary, business profile, entity list, clarifying questions
                 (targeted refine: a plan of what the new answers change)
  2. fields    - fields per entity batch, each with a description and its evidence (source column)
  3. pipelines and roles
  4. automations, integrations and reports
  5. review    - business-logic critique and follow-up questions
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.business_model import (
    Action,
    ActionType,
    Assumption,
    Automation,
    BusinessModel,
    BusinessProfile,
    ClarifyingQuestion,
    Condition,
    ConditionOperator,
    DataMapping,
    DiscoveryResult,
    Entity,
    EntityKind,
    Field_,
    FieldType,
    Integration,
    Permission,
    PicklistOption,
    Process,
    Provenance,
    Report,
    Role,
    Trigger,
    TriggerType,
)
from app.domain.validation import Issue, normalize_model, prune_invalid, strip_placeholders, validate_model
from app.llm.base import (
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvider,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTruncatedError,
    LLMUsage,
    T,
)
from app.llm.schema import strict_json_schema
from app.pipeline.prompts import SYSTEM_PROMPT
from app.pipeline.review import ReviewStage, compact_automation, run_review
from app.services.knowledge import render_knowledge

logger = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]

CHARS_PER_TOKEN = 3.3  # conservative; JSON schemas measured ~3.8 chars/token on Groq
SAFETY_MARGIN = 300
MIN_OUTPUT_TOKENS = 1_200
MAX_OUTPUT_TOKENS = 6_000
EXPECTED_OUTPUT_TOKENS = 2_500  # typical step output incl. reasoning; used to pace in-flight calls
RATE_LIMIT_RETRIES = 4
MAX_RATE_LIMIT_WAIT = 120  # seconds; longer waits signal a daily quota
FIELD_BATCH = 3
NO_PLACEHOLDERS = (
    "Write plain values only: never template syntax such as {{field}} or ${var}; if a value is dynamic, "
    "describe it in words in the description instead."
)


class _Stage(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class EntityOutline(_Stage):
    key: str = Field(description="snake_case, singular")
    label: str
    plural_label: str
    kind: EntityKind
    description: str
    name_field_label: str = "Name"
    name_is_auto_number: bool = False
    name_auto_number_format: str | None = None
    provenance: Provenance


class OutlineStage(_Stage):
    summary: str = Field(description="Short narrative of the business and the proposed CRM design.")
    profile: BusinessProfile
    entities: list[EntityOutline]
    questions: list[ClarifyingQuestion] = Field(description="At most 8, most important first.")
    changes: list[str] = Field(description="When refining: what changed and why. Otherwise empty.")


class FieldLite(_Stage):
    """Compact field shape for small budgets (a full Field_ costs ~3x the output tokens)."""

    key: str = Field(description="snake_case")
    label: str
    type: FieldType
    description: str = Field(description="One short sentence: what the field holds or why the business needs it")
    required: bool
    options: list[str] = Field(description="Picklist values; empty for other types")
    reference_entity: str | None = Field(description="Entity key, lookup fields only")
    master_detail: bool = Field(description="Lookup only: child cannot exist without the parent")
    unique: bool
    pii: bool
    evidence: str = Field(
        description="Where it comes from: 'file > sheet > column' for a spreadsheet column (exactly as listed), "
        "'answer: <question key>', 'narrative', 'document: <filename>' or 'best practice'"
    )

    def to_field(self, fallback: Provenance, sheet_names: set[str] | frozenset[str] = frozenset()) -> Field_:
        is_lookup = self.type == FieldType.lookup
        return Field_(
            key=self.key,
            label=self.label,
            type=self.type,
            description=self.description or None,
            required=self.required,
            unique=self.unique,
            pii=self.pii,
            options=[PicklistOption(value=o, label=o) for o in dict.fromkeys(self.options) if o.strip()],
            reference_entity=self.reference_entity if is_lookup else None,
            relationship=("master_detail" if self.master_detail else "lookup") if is_lookup else None,
            provenance=evidence_provenance(self.evidence, fallback, sheet_names),
        )


def evidence_provenance(
    evidence: str | None, fallback: Provenance, sheet_names: set[str] | frozenset[str] = frozenset()
) -> Provenance:
    ev = (evidence or "").strip()
    low = ev.lower()
    if not ev:
        return fallback
    parts = [p.strip() for p in ev.split(">")]
    if len(parts) >= 3:
        # Only a column of an uploaded sheet counts as file evidence; anything else is the model's inference.
        if f"{parts[0]} > {parts[1]}" in sheet_names:
            return Provenance(source="file", reference=ev, confidence=0.85)
        return Provenance(source="inferred", reference=ev[:120], confidence=0.55)
    if low.startswith("answer"):
        return Provenance(source="answer", reference=ev.split(":", 1)[-1].strip() or None, confidence=0.9)
    if low.startswith("document"):
        return Provenance(source="document", reference=ev.split(":", 1)[-1].strip() or None, confidence=0.8)
    if low.startswith(("narrative", "client")):
        return Provenance(source="client_input", confidence=0.8)
    if "best" in low or "practice" in low:
        return Provenance(source="best_practice", confidence=0.65)
    return Provenance(source="inferred", reference=ev[:120], confidence=0.6)


class EntityFields(_Stage):
    entity: str = Field(description="Entity key")
    name_from_column: str | None = Field(
        description="'file > sheet > column' holding the record name, when a spreadsheet has one"
    )
    fields: list[FieldLite]


class FieldsStage(_Stage):
    entities: list[EntityFields]


class ProcessStage(_Stage):
    processes: list[Process]


class ConditionLite(_Stage):
    field: str = Field(description="Field key on the automation's entity")
    operator: ConditionOperator
    value: str | None


class ActionLite(_Stage):
    type: ActionType
    description: str
    target_field: str | None = Field(description="update_field only: field key")
    value: str | None = Field(description="update_field only: value to set")
    related_entity: str | None = Field(description="create_record only: entity key")
    recipient: str | None = Field(description="send_email/notify_user: 'owner', a role key or an email field key")
    due_in_days: int | None = Field(description="create_task only")


class AutomationLite(_Stage):
    """Compact automation shape (the full Automation costs ~3x the output tokens)."""

    key: str
    name: str
    entity: str = Field(description="Entity key")
    description: str
    trigger: TriggerType
    conditions: list[ConditionLite]
    actions: list[ActionLite]

    def to_automation(self) -> Automation:
        return Automation(
            key=self.key,
            name=self.name,
            entity=self.entity,
            description=self.description,
            trigger=Trigger(type=self.trigger),
            conditions=[Condition(**c.model_dump()) for c in self.conditions],
            actions=[Action(**a.model_dump()) for a in self.actions],
            provenance=Provenance(source="inferred", confidence=0.7),
        )


class AutomationsStage(_Stage):
    automations: list[AutomationLite]


ACCESS_LEVELS = {
    "read": dict(read=True),
    "edit": dict(read=True, create=True, edit=True),
    "full": dict(read=True, create=True, edit=True, delete=True),
    "admin": dict(read=True, create=True, edit=True, delete=True, view_all=True, modify_all=True),
}


class PermissionLite(_Stage):
    entity: str = Field(description="Entity key")
    access: Literal["read", "edit", "full", "admin"] = Field(
        description="read; edit = create+edit; full = +delete; admin = +see and change everyone's records"
    )


class RoleLite(_Stage):
    key: str
    label: str
    description: str | None
    reports_to: str | None = Field(description="Role key of the manager role")
    permissions: list[PermissionLite]

    def to_role(self) -> Role:
        return Role(
            key=self.key,
            label=self.label,
            description=self.description,
            reports_to=self.reports_to,
            permissions=[Permission(entity=p.entity, **ACCESS_LEVELS[p.access]) for p in self.permissions],
            provenance=Provenance(source="inferred", confidence=0.7),
        )


class RolesStage(_Stage):
    roles: list[RoleLite]


class ExtrasStage(_Stage):
    integrations: list[Integration]
    reports: list[Report]
    assumptions: list[Assumption]


class RefinePlan(_Stage):
    """What newly answered questions change in an existing design (targeted refinement)."""

    summary: str = Field(description="Updated short narrative of the business and the CRM design")
    changes: list[str] = Field(description="Each change and the answer that caused it")
    profile: BusinessProfile | None = Field(description="Updated profile, only if the answers change it")
    new_entities: list[EntityOutline]
    removed_entities: list[str] = Field(description="Keys of entities the answers make unnecessary")
    redo_fields_for: list[str] = Field(description="Existing entity keys whose fields must change")
    redo_pipelines: bool
    redo_automations_for: list[str] = Field(description="Entity keys whose automations must be redone")
    redo_roles: bool
    redo_integrations_and_reports: bool
    questions: list[ClarifyingQuestion] = Field(description="At most 4 new follow-up questions")


class TokenPacer:
    """Token bucket matching how providers such as Groq meter a tokens-per-minute limit.

    The budget refills continuously (limit / 60 tokens per second) and is charged what calls actually
    use: measured on Groq, a call allowed 6,000 output tokens left 7,431 of 8,000 available right after.
    A call reserves an estimate while it runs; the difference to its real usage is settled on release.
    """

    def __init__(self, tokens_per_minute: int):
        self.tpm = tokens_per_minute
        self.rate = tokens_per_minute / 60
        self.level = float(tokens_per_minute)
        self.stamp = time.monotonic()
        self.inflight: dict[int, int] = {}
        self._next = 0
        self._lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        self.level = min(self.tpm, self.level + (now - self.stamp) * self.rate)
        self.stamp = now

    def _load(self, now: float) -> int:
        """Tokens currently unavailable (spent and not yet refilled, plus in-flight reservations)."""
        self._refill(now)
        return round(self.tpm - self.level)

    async def acquire(self, estimate: int, on_wait: Callable[[int], Awaitable[None]] | None = None) -> int:
        estimate = min(estimate, self.tpm)
        while True:
            async with self._lock:
                self._refill(time.monotonic())
                if self.level >= estimate:
                    self.level -= estimate
                    self._next += 1
                    self.inflight[self._next] = estimate
                    return self._next
                wait = max(1, int((estimate - self.level) / self.rate) + 1)
            if on_wait and wait >= 5:
                await on_wait(wait)
            await asyncio.sleep(wait)

    def release(self, ticket: int, actual_tokens: int | None) -> None:
        estimate = self.inflight.pop(ticket, 0)
        self._refill(time.monotonic())
        if actual_tokens:
            self.level -= actual_tokens - estimate  # refund what was over-reserved, or charge the overrun


def _estimate(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _clip(text: str, max_tokens: int) -> tuple[str, bool]:
    limit = int(max_tokens * CHARS_PER_TOKEN)
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _sheets(ctx) -> list[tuple[str, dict]]:
    return [
        (s.filename, sh) for s in ctx.sources if s.kind == "tabular" and s.profile for sh in s.profile.get("sheets", [])
    ]


def _compact_sources(ctx) -> str:
    lines = []
    for filename, sh in _sheets(ctx):
        cols = []
        for c in sh["columns"]:
            extra = ""
            if c.get("top_values"):
                extra = " [" + "|".join(list(c["top_values"])[:6]) + "]"
            cols.append(f"{c['name']}:{c['inferred_type']}{extra}")
        lines.append(f"- {filename} > {sh['sheet']} ({sh['row_count']} rows): " + ", ".join(cols))
    for r in ctx.relationships[:10]:
        lines.append(f"- link: {r['from']} -> {r['to']} ({int(r['overlap'] * 100)}% match)")
    return "\n".join(lines)


def compact_design(model: BusinessModel, with_fields: bool = True) -> str:
    lines = []
    for e in model.entities:
        head = f'- {e.key} ({e.kind}) "{e.label}"'
        if with_fields and e.fields:
            parts = []
            for f in e.fields:
                t = f.type
                if f.reference_entity:
                    t += f"->{f.reference_entity}"
                if f.options:
                    t += "[" + "|".join(o.label for o in f.options[:8]) + "]"
                parts.append(f"{f.key}:{t}")
            head += ": " + ", ".join(parts)
        lines.append(head)
    for p in model.processes:
        lines.append(f"- process {p.key} on {p.entity}.{p.stage_field}: " + " > ".join(s.label for s in p.stages))
    for r in model.roles:
        lines.append(f"- role {r.key}" + (f" reports to {r.reports_to}" if r.reports_to else ""))
    return "\n".join(lines)


class StagedRunner:
    def __init__(self, provider: LLMProvider, tokens_per_minute: int, progress: Progress | None):
        self.provider = provider
        self.tpm = tokens_per_minute
        self.pacer = TokenPacer(tokens_per_minute)
        self.progress = progress
        self.usage = LLMUsage()
        self.model_name = ""
        self.calls = 0

    async def _say(self, message: str) -> None:
        if self.progress:
            await self.progress(message)

    async def call(self, label: str, instructions: str, context: str, output_type: type[T]) -> T:
        schema_tokens = _estimate(json.dumps(strict_json_schema(output_type)))
        fixed = _estimate(SYSTEM_PROMPT) + _estimate(instructions) + schema_tokens + SAFETY_MARGIN
        room = self.tpm - fixed - MIN_OUTPUT_TOKENS
        if room <= 0:
            raise LLMError(
                f"The AI provider's limit ({self.tpm:,} tokens/minute) is too small for this step. "
                "Upgrade the provider plan or choose another provider in AI settings."
            )
        context, clipped = _clip(context, max(room // 2, 200))
        if clipped:
            logger.warning("staged step %s: context clipped to fit %s tokens/minute", label, self.tpm)
            context += "\n[Context truncated to fit the provider's rate limit.]"
        prompt_tokens = fixed - SAFETY_MARGIN + _estimate(context)
        max_out = min(MAX_OUTPUT_TOKENS, self.tpm - prompt_tokens - SAFETY_MARGIN)
        estimate = prompt_tokens + min(max_out, EXPECTED_OUTPUT_TOKENS)
        messages = [LLMMessage("user", f"{instructions}\n\n{context}")]

        async def waiting(seconds: int) -> None:
            await self._say(f"{label} (waiting {seconds}s for the provider's rate limit)")

        output_failures = rate_limits = 0
        while True:
            ticket = await self.pacer.acquire(estimate, waiting)
            await self._say(label)
            actual = None
            try:
                result = await self.provider.generate_structured(
                    system=SYSTEM_PROMPT, messages=messages, output_type=output_type, max_tokens=max_out
                )
                actual = result.usage.input_tokens + result.usage.output_tokens
            except LLMTruncatedError:
                raise  # retrying the same request cannot help; callers split the work instead
            except LLMRateLimitError as exc:
                rate_limits += 1
                if exc.retry_after and exc.retry_after > MAX_RATE_LIMIT_WAIT:
                    # A long wait means a daily/hourly quota, not the per-minute budget: stop and say so.
                    minutes = max(1, round(exc.retry_after / 60))
                    raise LLMError(
                        f"The AI provider's usage limit is used up for now; it asks to wait about {minutes} "
                        f"minutes. Try again later, or switch provider in AI settings. ({exc})"
                    ) from exc
                if rate_limits > RATE_LIMIT_RETRIES:
                    raise
                wait = int(exc.retry_after or 15) + 1
                logger.info("staged step %s rate limited; retrying in %ss", label, wait)
                await waiting(wait)
                await asyncio.sleep(wait)
                continue
            except (LLMOutputError, LLMTimeoutError) as exc:
                output_failures += 1
                if output_failures == 2:
                    raise LLMError(f"{label} failed twice: {exc}") from exc
                logger.warning("staged step %s failed, retrying: %s", label, exc)
                continue
            finally:
                self.pacer.release(ticket, actual)
            self.calls += 1
            self.usage = self.usage + result.usage
            self.model_name = result.model
            return result.output


def _base_context(ctx, new_answer_keys: set[str] | None = None, with_previous: bool = True) -> str:
    project = ctx.project
    parts = [
        f"Client: {project.get('client_name')} | Industry: {project.get('industry') or 'unknown'}",
        f"Business narrative:\n{project.get('description') or '(none)'}",
    ]
    if ctx.knowledge:
        parts.append(render_knowledge(ctx.knowledge))
    sources = _compact_sources(ctx)
    if sources:
        parts.append("Spreadsheets (file > sheet (rows): column:type [sample values]):\n" + sources)
    for d in (s for s in ctx.sources if s.kind == "document" and s.text):
        parts.append(f"Document {d.filename}:\n{d.text}")
    if ctx.answered:
        lines = []
        for a in ctx.answered:
            new = " [NEW]" if new_answer_keys and a["key"] in new_answer_keys else ""
            lines.append(f"- ({a['key']}){new} {a['question']} => {a['answer']}")
        parts.append("Answers from the client:\n" + "\n".join(lines))
    if ctx.dismissed:
        parts.append("Dismissed as not relevant (do not ask again): " + "; ".join(d["key"] for d in ctx.dismissed))
    if ctx.previous_model and with_previous:
        prev = BusinessModel.model_validate(ctx.previous_model)
        parts.append("Previous design (keep keys stable):\n" + compact_design(prev))
    return "\n\n".join(parts)


def _mappings_from_fields(entity_key: str, ef: EntityFields, sheet_names: set[str]) -> list[DataMapping]:
    """Spreadsheet evidence on fields doubles as the migration mapping (no separate mapping call)."""
    out = []
    pairs = [(ef.name_from_column, "name")] + [(f.evidence, f.key) for f in ef.fields]
    for evidence, field_key in pairs:
        parts = [p.strip() for p in (evidence or "").split(">")]
        if len(parts) >= 3 and f"{parts[0]} > {parts[1]}" in sheet_names:
            out.append(
                DataMapping(
                    source=f"{parts[0]} > {parts[1]}", column=" > ".join(parts[2:]), entity=entity_key, field=field_key
                )
            )
    return out


async def run_staged_discovery(
    provider: LLMProvider,
    ctx,
    tokens_per_minute: int,
    progress: Progress | None = None,
    automation_guidance: str | None = None,
):
    from app.pipeline.discovery import DiscoveryOutcome  # avoid import cycle

    run = StagedRunner(provider, tokens_per_minute, progress)
    new_answers = set(getattr(ctx, "new_answer_keys", []) or [])
    targeted = bool(ctx.previous_model and new_answers and not getattr(ctx, "inputs_changed", True))
    first = _base_context(ctx, new_answers)  # step 1 sees the previous design
    base = _base_context(ctx, new_answers, with_previous=False)  # later steps get "design so far" instead
    sheet_names = {f"{f} > {sh['sheet']}" for f, sh in _sheets(ctx)}
    settled = [q["key"] for q in ctx.answered] + [q["key"] for q in ctx.dismissed]

    # ---- wave 1: outline, or a plan of what the new answers change -------------------------------------
    plan: RefinePlan | None = None
    if targeted:
        plan = await run.call(
            "Step 1: planning changes from your answers",
            "The design below already exists. The answers marked [NEW] are new. Work out the smallest set of "
            "changes that applies them faithfully: new or removed entities, which entities need their fields "
            "redone, and which other parts must be regenerated. Leave everything else untouched. Keep keys "
            "stable. Ask follow-up questions only for gaps the new answers open up.",
            first,
            RefinePlan,
        )
        model = BusinessModel.model_validate(ctx.previous_model)
        if plan.profile:
            model.profile = plan.profile
        removed = set(plan.removed_entities)
        model.entities = [e for e in model.entities if e.key not in removed]
        existing = {e.key for e in model.entities}
        added = [
            Entity(**e.model_dump(exclude={"provenance"}), provenance=e.provenance, fields=[])
            for e in plan.new_entities
            if e.key not in existing
        ]
        model.entities += added
        redo = {k for k in plan.redo_fields_for if k in existing} | {e.key for e in added}
        field_targets = [e for e in model.entities if e.key in redo]
        summary, changes, questions = plan.summary, list(plan.changes), list(plan.questions)
    else:
        refine = ctx.previous_model is not None
        outline = await run.call(
            "Step 1: understanding the business",
            ("Refine" if refine else "Design")
            + " the CRM at outline level: business profile, the list of entities (no fields yet), and up to 8 "
            "clarifying questions for high-impact gaps. Question keys must be stable snake_case; never re-ask "
            "answered or dismissed questions."
            + (" List what changed versus the previous design in `changes`." if refine else " Leave `changes` empty."),
            first,
            OutlineStage,
        )
        entities = [
            Entity(**e.model_dump(exclude={"provenance"}), provenance=e.provenance, fields=[]) for e in outline.entities
        ]
        model = BusinessModel(profile=outline.profile, entities=entities)
        field_targets = entities
        summary, changes, questions = outline.summary, list(outline.changes), list(outline.questions)

    by_key = {e.key: e for e in model.entities}
    previous = BusinessModel.model_validate(ctx.previous_model) if ctx.previous_model else None

    def previous_fields(items: list[Entity]) -> str:
        """The batch's fields in the previous design, so refined fields keep their keys."""
        if not previous:
            return ""
        lines = [
            line
            for e in items
            if (pe := previous.entity(e.key))
            for line in compact_design(BusinessModel(profile=previous.profile, entities=[pe])).splitlines()
        ]
        return ("\n\nPrevious fields of these entities (keep keys stable):\n" + "\n".join(lines)) if lines else ""

    entity_list = "\n".join(f'- {e.key} ({e.kind}) "{e.label}": {e.description or ""}' for e in model.entities)

    async def split_call(step: str, items: list, describe, instructions, context: str, output_type, apply) -> None:
        """Run one call for `items`; if the output does not fit, split the items in half and retry."""
        try:
            out = await run.call(f"{step} for {describe(items)}", instructions(items), context, output_type)
        except LLMTruncatedError as exc:
            if len(items) == 1:
                raise LLMError(
                    f"{step} for {describe(items)} does not fit the AI provider's limit "
                    f"({tokens_per_minute:,} tokens/minute). Upgrade the plan or choose another provider."
                ) from exc
            mid = len(items) // 2
            await asyncio.gather(
                split_call(step, items[:mid], describe, instructions, context, output_type, apply),
                split_call(step, items[mid:], describe, instructions, context, output_type, apply),
            )
            return
        apply(out)

    def labels(items: list[Entity]) -> str:
        return ", ".join(e.label for e in items)

    def keys(items: list[Entity]) -> str:
        return ", ".join(e.key for e in items)

    # ---- wave 2: fields (batches in parallel) -------------------------------------------------------
    redone_fields: set[str] = set()

    def apply_fields(out: FieldsStage) -> None:
        for ef in out.entities:
            if ef.entity in by_key:
                entity = by_key[ef.entity]
                entity.fields = [f.to_field(entity.provenance, sheet_names) for f in ef.fields]
                redone_fields.add(ef.entity)
                model.data_mappings = [m for m in model.data_mappings if m.entity != ef.entity]
                model.data_mappings += _mappings_from_fields(ef.entity, ef, sheet_names)

    await asyncio.gather(
        *(
            split_call(
                "Step 2: fields",
                field_targets[i : i + FIELD_BATCH],
                labels,
                lambda items: (
                    f"Define the fields for these entities only: {keys(items)}. Do not repeat the built-in "
                    "name/owner/created fields. Lookups must reference entity keys from the list. Picklists need "
                    "options. Only fields the business needs (usually 5-12 per entity). Give every field a short "
                    "description and its evidence; map every business-relevant spreadsheet column that belongs "
                    "to these entities to a field (or to name_from_column). " + NO_PLACEHOLDERS + previous_fields(items)
                ),
                f"{base}\n\nAll entities in the design:\n{entity_list}",
                FieldsStage,
                apply_fields,
            )
            for i in range(0, len(field_targets), FIELD_BATCH)
        )
    )

    # ---- wave 3: pipelines and roles (parallel) -----------------------------------------------------
    do_pipelines = plan is None or plan.redo_pipelines
    do_roles = plan is None or plan.redo_roles or bool(plan.new_entities or plan.removed_entities)
    design = compact_design(model)

    async def pipelines() -> None:
        out = await run.call(
            "Step 3: pipelines",
            "Define the processes: each pipeline or lifecycle with its ordered stages (mark won/lost/closed). "
            "stage_field must be a picklist field key on that entity, or a new key which will be created. Only "
            "entities that really move through stages.",
            f"{base}\n\nDesign so far:\n{design}",
            ProcessStage,
        )
        model.processes = out.processes

    async def roles() -> None:
        out = await run.call(
            "Step 3: roles and access",
            "Define the roles: who uses the CRM, the hierarchy via reports_to, and least-privilege access per "
            "entity key.",
            f"{base}\n\nEntities:\n{compact_design(model, with_fields=False)}",
            RolesStage,
        )
        model.roles = [r.to_role() for r in out.roles]

    await asyncio.gather(*([pipelines()] if do_pipelines else []), *([roles()] if do_roles else []))

    # ---- wave 4: automations, integrations and reports (parallel) -----------------------------------
    if plan is None:
        automation_targets = model.entities
    else:
        wanted = set(plan.redo_automations_for) | {e.key for e in plan.new_entities}
        automation_targets = [e for e in model.entities if e.key in wanted]
    do_extras = plan is None or plan.redo_integrations_and_reports
    design = compact_design(model)
    fresh_automations: list[Automation] = []

    async def automations() -> None:
        if not automation_targets:
            return
        guidance = f" {automation_guidance}" if automation_guidance else ""
        await split_call(
            "Step 4: automations",
            automation_targets,
            lambda items: "all entities" if len(items) == len(model.entities) else labels(items),
            lambda items: (
                f"Define automations for these entities only: {keys(items)}. Only what the business does "
                "repeatedly (follow-ups, assignments, reminders, notifications, status roll-forward), at most 3 "
                "per entity, each small, explicit and in the right business order. Reference only the entity and "
                "field keys below." + guidance + " " + NO_PLACEHOLDERS
            ),
            f"{base}\n\nDesign so far:\n{design}",
            AutomationsStage,
            lambda out: fresh_automations.extend(a.to_automation() for a in out.automations),
        )

    async def extras() -> None:
        if not do_extras:
            return
        out = await run.call(
            "Step 4: integrations and reports",
            "Define integrations with external systems, useful reports/dashboards (group_by uses field keys; "
            "audience uses role keys), and the assumptions you made for gaps.",
            f"{base}\n\nDesign so far:\n{design}",
            ExtrasStage,
        )
        model.integrations, model.reports = out.integrations, out.reports
        model.assumptions = out.assumptions

    await asyncio.gather(automations(), extras())
    redone = {e.key for e in automation_targets}
    model.automations = [a for a in model.automations if a.entity not in redone] + fresh_automations

    # ---- clean up, then wave 5: review ----------------------------------------------------------------
    model = normalize_model(model)
    model, placeholder_notes = strip_placeholders(model)
    model, notes = prune_invalid(model)
    model = normalize_model(model)
    issues: list[Issue] = validate_model(model)

    asked = settled + list(getattr(ctx, "open_question_keys", []) or []) + [q.key for q in questions]

    async def review_call(instructions: str, context: str) -> ReviewStage:
        return await run.call("Step 5: reviewing the design", instructions, context, ReviewStage)

    try:
        review_issues, review_questions = await run_review(review_call, model, compact_design(model), settled, asked)
        issues += review_issues
        questions += review_questions
    except LLMError as exc:  # the design is still usable without the review
        logger.warning("design review skipped: %s", exc)

    result = DiscoveryResult(
        summary=summary,
        model=model,
        questions=[q for q in questions if q.key not in set(settled)],
        changes=changes + placeholder_notes + notes,
    )
    logger.info("staged discovery finished calls=%s targeted=%s issues=%s", run.calls, targeted, len(issues))
    return DiscoveryOutcome(result, issues, run.usage, run.model_name, run.calls, keep_open_questions=targeted)


__all__ = ["run_staged_discovery", "TokenPacer", "compact_automation"]
