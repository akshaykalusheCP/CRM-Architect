"""AI design review: a second look at the finished design for business-logic problems.

Structural checks (validation.py) catch broken references; they cannot tell that "create a Quote when the
deal is Won" is backwards, or that a logistics CRM has no way to track shipments. This step asks the model
to critique the compact design and turns its findings into review notes (warnings, never build-blocking)
plus follow-up questions that only become visible once fields and pipelines exist.
"""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.business_model import Automation, BusinessModel, ClarifyingQuestion, QuestionCategory
from app.domain.validation import Issue

REVIEW_INSTRUCTIONS = (
    "Review the CRM design below as a senior solution architect would before showing it to the client. "
    "Look for business-logic mistakes (steps in the wrong order, automations that fire at the wrong moment "
    "or do something the business would not want, duplicated or contradictory records), essentials this "
    "kind of business normally needs but the design lacks, and access that is too broad or too narrow. "
    "Report at most 8 findings, most important first, each pointing at the element's key. Do not report "
    "style issues or restate the design. Then list at most 4 follow-up questions for the client that only "
    "became apparent now; never repeat an answered, dismissed or still-open question."
)


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Plain strings rather than enums: some providers (Groq) validate instead of constraining output,
    # and one off-list value would fail the whole review. Unknown areas are treated as "scope".
    area: str = Field(description="entity, field, process, automation, role, integration, report or scope")
    target: str | None = Field(description="Key of the element concerned (entity, process, automation, role...)")
    problem: str
    fix: str = Field(description="The concrete change to make")


class ReviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Stable snake_case key")
    question: str
    why: str
    category: str = Field(
        description="business, entity, field, process, automation, security, integration, data, scope"
    )
    priority: str = Field(description="high, medium or low")
    suggested_answers: list[str] = Field(default_factory=list)

    def to_question(self) -> ClarifyingQuestion:
        category = self.category if self.category in QuestionCategory._value2member_map_ else "scope"
        priority = self.priority if self.priority in ("high", "medium", "low") else "medium"
        return ClarifyingQuestion(
            key=self.key,
            question=self.question,
            why=self.why,
            category=category,
            priority=priority,
            suggested_answers=self.suggested_answers,
        )


class ReviewStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[ReviewFinding]
    questions: list[ReviewQuestion]


_PREFIX = {
    "entity": "entities",
    "field": "entities",
    "process": "processes",
    "automation": "automations",
    "role": "roles",
    "integration": "integrations",
    "report": "reports",
    "scope": "profile",
}


def compact_automation(a: Automation) -> str:
    when = a.trigger.type + (f" ({a.trigger.schedule})" if a.trigger.schedule else "")
    if a.conditions:
        when += " if " + f" {a.condition_logic} ".join(
            f"{c.field} {c.operator} {c.value or ''}".strip() for c in a.conditions
        )
    then = "; ".join(f"{x.type}: {x.description}" for x in a.actions)
    return f"- automation {a.key} on {a.entity}: WHEN {when} THEN {then}"


def review_context(model: BusinessModel, compact_design: str, settled_keys: list[str], open_keys: list[str]) -> str:
    parts = [
        f"Business: {model.profile.name} ({model.profile.industry}). {model.profile.description}",
        "Design:\n" + compact_design,
        "Automations:\n" + ("\n".join(compact_automation(a) for a in model.automations) or "(none)"),
    ]
    asked = list(dict.fromkeys(settled_keys + open_keys))
    if asked:
        parts.append(f"Question keys already asked (do not repeat): {', '.join(asked)}")
    return "\n\n".join(parts)


def findings_to_issues(review: ReviewStage, model: BusinessModel) -> list[Issue]:
    known = {
        "entities": {e.key for e in model.entities},
        "processes": {p.key for p in model.processes},
        "automations": {a.key for a in model.automations},
        "roles": {r.key for r in model.roles},
        "integrations": {i.key for i in model.integrations},
        "reports": {r.key for r in model.reports},
    }
    issues = []
    for f in review.findings[:8]:
        prefix = _PREFIX.get(f.area.strip().lower(), "profile")
        target = f.target if f.target and f.target in known.get(prefix, set()) else None
        path = f"{prefix}.{target}" if target else prefix
        issues.append(Issue("warning", "ai_review", path, f"{f.problem.rstrip('.')}. Suggested: {f.fix}"))
    return issues


async def run_review(
    call: Callable[[str, str], Awaitable[ReviewStage]],
    model: BusinessModel,
    compact_design: str,
    settled_keys: list[str],
    open_keys: list[str],
) -> tuple[list[Issue], list[ClarifyingQuestion]]:
    """`call(instructions, context)` runs one structured request returning a ReviewStage."""
    review = await call(REVIEW_INSTRUCTIONS, review_context(model, compact_design, settled_keys, open_keys))
    asked = set(settled_keys) | set(open_keys)
    questions = [q.to_question() for q in review.questions if q.key not in asked][:4]
    return findings_to_issues(review, model), questions
