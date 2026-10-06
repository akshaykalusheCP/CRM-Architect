"""Discovery pipeline: sources + narrative + answers -> validated canonical business model."""

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.domain.business_model import DiscoveryResult
from app.domain.validation import Issue, has_errors, normalize_model, strip_placeholders, validate_model
from app.llm.base import LLMError, LLMMessage, LLMProvider, LLMUsage
from app.pipeline.prompts import (
    DISCOVERY_INSTRUCTIONS,
    REFINE_INSTRUCTIONS,
    REPAIR_INSTRUCTIONS,
    SYSTEM_PROMPT,
)
from app.pipeline.review import ReviewStage, run_review
from app.pipeline.staged import NO_PLACEHOLDERS, Progress, compact_design, run_staged_discovery
from app.services.knowledge import render_knowledge

logger = logging.getLogger(__name__)


@dataclass
class SourceContext:
    filename: str
    kind: str
    profile: dict | None = None
    text: str | None = None
    text_truncated: bool = False


@dataclass
class AnalysisContext:
    project: dict[str, Any]
    knowledge: list[dict] = field(default_factory=list)
    sources: list[SourceContext] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    answered: list[dict] = field(default_factory=list)
    dismissed: list[dict] = field(default_factory=list)
    open_question_keys: list[str] = field(default_factory=list)
    previous_model: dict | None = None
    previous_summary: str | None = None
    # Answers not yet applied to any design version; with unchanged inputs they allow a targeted refine.
    new_answer_keys: list[str] = field(default_factory=list)
    # Whether the narrative or sources changed since the last AI-generated version.
    inputs_changed: bool = True

    @property
    def is_refine(self) -> bool:
        return self.previous_model is not None


@dataclass
class DiscoveryOutcome:
    result: DiscoveryResult
    issues: list[Issue]
    usage: LLMUsage
    model_name: str
    attempts: int
    # Targeted refinements only add follow-ups; questions still open stay open.
    keep_open_questions: bool = False


def render_context(ctx: AnalysisContext, automation_guidance: str | None = None) -> str:
    structured = {
        "project": ctx.project,
        "tabular_sources": [{"filename": s.filename, "profile": s.profile} for s in ctx.sources if s.kind == "tabular"],
        "detected_cross_file_relationships": ctx.relationships,
        "answered_questions": ctx.answered,
        "dismissed_questions": ctx.dismissed,
        "still_open_question_keys": ctx.open_question_keys,
    }
    rules = NO_PLACEHOLDERS + (f" Automations: {automation_guidance}" if automation_guidance else "")
    parts = [
        (REFINE_INSTRUCTIONS if ctx.is_refine else DISCOVERY_INSTRUCTIONS) + "\n" + rules,
        "<context_json>\n" + json.dumps(structured, indent=1, default=str) + "\n</context_json>",
    ]
    if ctx.knowledge:
        parts.append(render_knowledge(ctx.knowledge))
    for s in ctx.sources:
        if s.kind == "document" and s.text:
            note = ' truncated="true"' if s.text_truncated else ""
            parts.append(f'<document filename="{s.filename}"{note}>\n{s.text}\n</document>')
    if ctx.previous_model is not None:
        parts.append(
            "<previous_summary>\n" + (ctx.previous_summary or "") + "\n</previous_summary>\n"
            "<current_model>\n" + json.dumps(ctx.previous_model, default=str) + "\n</current_model>"
        )
    return "\n\n".join(parts)


async def run_discovery(
    provider: LLMProvider,
    ctx: AnalysisContext,
    repair_attempts: int = 1,
    progress: Progress | None = None,
    single_call_budget: int = 90_000,
    automation_guidance: str | None = None,
) -> DiscoveryOutcome:
    """Design the CRM in one structured call, or in paced stages when the provider's limit is too small.

    single_call_budget: tokens-per-minute needed for one full call (prompt + schema + max output).
    """
    limit = await provider.token_limit()
    if limit is not None and limit < single_call_budget:
        logger.info("provider limit %s tokens/min < %s; using staged discovery", limit, single_call_budget)
        return await run_staged_discovery(provider, ctx, limit, progress, automation_guidance)

    messages = [LLMMessage("user", render_context(ctx, automation_guidance))]
    usage = LLMUsage()
    attempt = 0
    settled = [q["key"] for q in ctx.answered] + [q["key"] for q in ctx.dismissed]
    while True:
        attempt += 1
        llm_result = await provider.generate_structured(
            system=SYSTEM_PROMPT, messages=messages, output_type=DiscoveryResult
        )
        usage = usage + llm_result.usage
        result = llm_result.output
        result.model = normalize_model(result.model)
        result.model, placeholder_notes = strip_placeholders(result.model)
        issues = validate_model(result.model)

        # Drop questions that were already answered or dismissed; the model sometimes repeats them.
        result.questions = [q for q in result.questions if q.key not in set(settled)]

        if not has_errors(issues) or attempt > repair_attempts:
            break
        errors = [asdict(i) for i in issues if i.severity == "error"]
        logger.info("discovery repair attempt=%s errors=%s", attempt, len(errors))
        messages = messages + [
            LLMMessage("assistant", result.model_dump_json()),
            LLMMessage("user", REPAIR_INSTRUCTIONS + "\n<errors>\n" + json.dumps(errors, indent=1) + "\n</errors>"),
        ]
    result.changes += placeholder_notes

    if progress:
        await progress("Reviewing the design")

    async def review_call(instructions: str, context: str) -> ReviewStage:
        nonlocal usage
        r = await provider.generate_structured(
            system=SYSTEM_PROMPT, messages=[LLMMessage("user", f"{instructions}\n\n{context}")], output_type=ReviewStage
        )
        usage = usage + r.usage
        return r.output

    try:
        asked = settled + ctx.open_question_keys + [q.key for q in result.questions]
        review_issues, review_questions = await run_review(
            review_call, result.model, compact_design(result.model), settled, asked
        )
        issues += review_issues
        result.questions += review_questions
    except LLMError as exc:  # the design is still usable without the review
        logger.warning("design review skipped: %s", exc)

    logger.info("discovery finished attempts=%s issues=%s", attempt, len(issues))
    return DiscoveryOutcome(result, issues, usage, llm_result.model, attempt)
