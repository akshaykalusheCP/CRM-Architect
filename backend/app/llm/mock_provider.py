"""Offline, deterministic provider for local development and tests.

It builds a plausible DiscoveryResult from the structured context (one entity per sheet, fields from
profiled columns) so the full pipeline can run without API keys. It is not meant to be smart.
"""

import json
import re
from pathlib import Path

from app.domain.business_model import (
    Assumption,
    BusinessModel,
    BusinessProfile,
    ClarifyingQuestion,
    DataMapping,
    DiscoveryResult,
    Entity,
    Field_,
    FieldType,
    Permission,
    PicklistOption,
    Provenance,
    Role,
)
from app.llm.base import LLMMessage, LLMProvider, LLMResult, LLMUsage, T

_TYPE_MAP = {
    "email": FieldType.email,
    "phone": FieldType.phone,
    "url": FieldType.url,
    "percent": FieldType.percent,
    "currency": FieldType.currency,
    "number": FieldType.number,
    "boolean": FieldType.boolean,
    "date": FieldType.date,
    "datetime": FieldType.datetime,
    "long_text": FieldType.long_text,
    "picklist": FieldType.picklist,
}


def _key(text: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not key or not key[0].isalpha():
        key = f"f_{key}"
    return key[:60]


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


class MockProvider(LLMProvider):
    name = "mock"

    async def generate_structured(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        output_type: type[T],
        max_tokens: int | None = None,
    ) -> LLMResult[T]:
        from app.pipeline.review import ReviewStage

        if output_type is ReviewStage:
            return LLMResult(output=ReviewStage(findings=[], questions=[]), model="mock", usage=LLMUsage())  # type: ignore[arg-type]
        if output_type is not DiscoveryResult:
            raise NotImplementedError("MockProvider only supports DiscoveryResult and ReviewStage")
        first = messages[0].content
        ctx = json.loads(first.split("<context_json>")[1].split("</context_json>")[0])
        if "<current_model>" in first:
            previous = json.loads(first.split("<current_model>")[1].split("</current_model>")[0])
            model = BusinessModel.model_validate(previous)
            for answer in ctx["answered_questions"]:
                model.assumptions.append(
                    Assumption(statement=f"{answer['question']} -> {answer['answer']}", confidence=0.9)
                )
            result = DiscoveryResult(
                summary="Refined design (mock provider).",
                model=model,
                questions=[],
                changes=[f"Applied answer to '{a['key']}'" for a in ctx["answered_questions"]],
            )
        else:
            result = self._discover(ctx)
        return LLMResult(output=result, model="mock", usage=LLMUsage())  # type: ignore[arg-type]

    def _discover(self, ctx: dict) -> DiscoveryResult:
        project = ctx["project"]
        entities: list[Entity] = []
        mappings: list[DataMapping] = []
        for source in ctx["tabular_sources"]:
            for sheet in (source["profile"] or {}).get("sheets", []):
                stem = Path(source["filename"]).stem
                base = stem if len(source["profile"]["sheets"]) == 1 else f"{stem} {sheet['sheet']}"
                key = _singular(_key(base))
                if any(e.key == key for e in entities):
                    continue
                ref = f"{source['filename']} > {sheet['sheet']}"
                fields: list[Field_] = []
                for col in sheet["columns"]:
                    fkey = _key(col["name"])
                    if fkey in {"name", "id"} or any(f.key == fkey for f in fields):
                        continue
                    ftype = _TYPE_MAP.get(col["inferred_type"], FieldType.text)
                    options = []
                    if ftype == FieldType.picklist:
                        options = [PicklistOption(value=v, label=v) for v in col.get("top_values", {})]
                        if not options:
                            ftype = FieldType.text
                    fields.append(
                        Field_(
                            key=fkey,
                            label=col["name"][:40],
                            type=ftype,
                            options=options,
                            pii=ftype in {FieldType.email, FieldType.phone},
                            provenance=Provenance(source="file", reference=f"{ref} > {col['name']}", confidence=0.6),
                        )
                    )
                    mappings.append(DataMapping(source=ref, column=col["name"], entity=key, field=fkey))
                label = base.replace("_", " ").strip().title()[:40]
                entities.append(
                    Entity(
                        key=key,
                        label=_singular(label),
                        plural_label=label,
                        kind="custom",
                        fields=fields,
                        provenance=Provenance(source="file", reference=ref, confidence=0.6),
                    )
                )
        if not entities:
            entities.append(
                Entity(
                    key="customer",
                    label="Customer",
                    plural_label="Customers",
                    kind="account",
                    fields=[],
                    provenance=Provenance(source="best_practice", confidence=0.5),
                )
            )
        role = Role(
            key="crm_user",
            label="CRM User",
            permissions=[Permission(entity=e.key, read=True, create=True, edit=True) for e in entities],
            provenance=Provenance(source="best_practice", confidence=0.5),
        )
        model = BusinessModel(
            profile=BusinessProfile(
                name=project.get("client_name") or project.get("name") or "Client",
                industry=project.get("industry") or "Unknown",
                description=(project.get("description") or "")[:2000] or "No description provided.",
            ),
            entities=entities,
            roles=[role],
            data_mappings=mappings,
            assumptions=[Assumption(statement="Generated by the offline mock provider.", confidence=0.3)],
        )
        questions = [
            ClarifyingQuestion(
                key="sales_process_stages",
                question="What are the stages a deal goes through from first contact to closing?",
                why="Defines the sales pipeline and stage-based automations.",
                category="process",
                priority="high",
                suggested_answers=["New > Qualified > Proposal > Won/Lost"],
            ),
            ClarifyingQuestion(
                key="team_structure",
                question="Which teams will use the CRM and who reports to whom?",
                why="Drives roles, record visibility and permissions.",
                category="security",
                priority="high",
            ),
        ]
        return DiscoveryResult(summary="Initial design (mock provider).", model=model, questions=questions)
