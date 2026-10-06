import asyncio
import time

import pytest

from app.domain.business_model import DiscoveryResult
from app.domain.validation import prune_invalid
from app.llm.base import LLMProvider, LLMRateLimitError, LLMResult, LLMTruncatedError, LLMUsage
from app.pipeline import staged
from app.pipeline.discovery import AnalysisContext, SourceContext, run_discovery
from app.pipeline.review import ReviewStage
from app.pipeline.staged import (
    AutomationsStage,
    ExtrasStage,
    FieldsStage,
    OutlineStage,
    ProcessStage,
    RefinePlan,
    RolesStage,
    TokenPacer,
)
from tests.factories import solar_model

PROV = {"source": "client_input", "reference": None, "confidence": 0.8}
SHEET = "leads.csv > Sheet1"


def _outline(n_entities: int = 4) -> OutlineStage:
    kinds = ["account", "opportunity", "custom", "custom", "custom"]
    return OutlineStage.model_validate(
        {
            "summary": "Solar installer CRM",
            "profile": {"name": "SunPeak", "industry": "Solar", "description": "d"},
            "entities": [
                {
                    "key": f"e{i}",
                    "label": f"E{i}",
                    "plural_label": f"E{i}s",
                    "kind": kinds[i],
                    "description": "x",
                    "provenance": PROV,
                }
                for i in range(n_entities)
            ],
            "questions": [
                {"key": "stages", "question": "Stages?", "why": "w", "category": "process", "priority": "high"},
                {"key": "answered_one", "question": "Old?", "why": "w", "category": "scope", "priority": "low"},
            ],
            "changes": [],
        }
    )


def _field(key, type_="text", evidence="best practice", **extra):
    base = {
        "key": key,
        "label": key.title(),
        "type": type_,
        "description": f"The {key}",
        "required": False,
        "options": [],
        "reference_entity": None,
        "master_detail": False,
        "unique": False,
        "pii": False,
        "evidence": evidence,
    }
    return {**base, **extra}


def _fields(keys: list[str]) -> FieldsStage:
    return FieldsStage.model_validate(
        {
            "entities": [
                {
                    "entity": k,
                    "name_from_column": f"{SHEET} > Name" if k == "e0" else None,
                    "fields": [
                        _field(
                            "status",
                            "picklist",
                            options=["New", "Done"],
                            evidence=f"{SHEET} > Status" if k == "e0" else "narrative",
                        ),
                        _field("parent", "lookup", reference_entity="e0" if k != "e0" else "ghost"),
                        _field("ref_no", "auto_number", unique=True, evidence="answer: numbering"),
                    ],
                }
                for k in keys
            ]
        }
    )


def _auto(k: str, target="status", value="New", condition_value=None):
    return {
        "key": f"auto_{k}",
        "name": f"Auto {k}",
        "entity": k,
        "description": "d",
        "trigger": "record_created",
        "conditions": [{"field": "status", "operator": "is_blank", "value": condition_value}],
        "actions": [
            {
                "type": "update_field",
                "description": "Set status",
                "target_field": target,
                "value": value,
                "related_entity": None,
                "recipient": None,
                "due_in_days": None,
            }
        ],
    }


class StageProvider(LLMProvider):
    name = "stage-fake"

    def __init__(self, limit=8000, truncate_automations_over=None, plan=None):
        self.limit = limit
        self.truncate_automations_over = truncate_automations_over
        self.plan = plan
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    async def token_limit(self):
        return self.limit

    async def generate_structured(self, *, system, messages, output_type, max_tokens=None):
        name = output_type.__name__
        prompt = messages[-1].content
        self.calls.append(name)
        self.prompts.setdefault(name, []).append(prompt)
        if output_type is OutlineStage:
            out = _outline()
        elif output_type is RefinePlan:
            out = self.plan
        elif output_type is FieldsStage:
            out = _fields(prompt.split("these entities only: ")[1].split(".")[0].split(", "))
            self.field_prompts = getattr(self, "field_prompts", []) + [prompt]
        elif output_type is ProcessStage:
            out = ProcessStage.model_validate(
                {
                    "processes": [
                        {
                            "key": "sales",
                            "name": "Sales",
                            "entity": "e1",
                            "stage_field": "deal_stage",
                            "provenance": PROV,
                            "stages": [
                                {"key": "new", "label": "New"},
                                {"key": "won", "label": "Won", "category": "won"},
                            ],
                        }
                    ]
                }
            )
        elif output_type is AutomationsStage:
            keys = prompt.split("these entities only: ")[1].split(".")[0].split(", ")
            if self.truncate_automations_over and len(keys) > self.truncate_automations_over:
                raise LLMTruncatedError("out of tokens")
            autos = []
            for k in keys:
                if k == "e2":
                    autos.append(_auto(k, target="nope"))  # broken reference: pruned
                elif k == "e3":
                    autos.append(_auto(k, condition_value="{{today_plus_2}}"))  # placeholder condition: dropped
                else:
                    autos.append(_auto(k))
            out = AutomationsStage.model_validate({"automations": autos})
        elif output_type is RolesStage:
            out = RolesStage.model_validate(
                {
                    "roles": [
                        {
                            "key": "rep",
                            "label": "Rep",
                            "description": None,
                            "reports_to": "missing",
                            "permissions": [{"entity": "e1", "access": "edit"}, {"entity": "e0", "access": "admin"}],
                        }
                    ]
                }
            )
        elif output_type is ExtrasStage:
            out = ExtrasStage.model_validate({"integrations": [], "reports": [], "assumptions": []})
        elif output_type is ReviewStage:
            out = ReviewStage.model_validate(
                {
                    "findings": [
                        {
                            "area": "automation",
                            "target": "auto_e1",
                            "problem": "Fires too early",
                            "fix": "Trigger on Won instead",
                        },
                        {"area": "Workflow", "target": None, "problem": "Odd area", "fix": "Still kept"},
                    ],
                    "questions": [
                        {"key": "stages", "question": "dup", "why": "w", "category": "process", "priority": "low"},
                        {
                            "key": "returns",
                            "question": "Returns?",
                            "why": "w",
                            "category": "not-a-category",
                            "priority": "urgent",
                        },
                    ],
                }
            )
        else:
            raise AssertionError(output_type)
        assert max_tokens and max_tokens <= self.limit
        return LLMResult(output=out, model="fake", usage=LLMUsage(100, 50))


@pytest.fixture(autouse=True)
def instant_pacing(monkeypatch, request):
    if "real_pacer" in request.keywords:
        return

    async def acquire(self, estimate, on_wait=None):
        self._next += 1
        return self._next

    monkeypatch.setattr(TokenPacer, "acquire", acquire)


def _ctx(**kw) -> AnalysisContext:
    return AnalysisContext(
        project={"client_name": "SunPeak", "description": "Solar"},
        sources=[
            SourceContext(
                "leads.csv",
                "tabular",
                {
                    "sheets": [
                        {
                            "sheet": "Sheet1",
                            "row_count": 3,
                            "columns": [
                                {"name": "Name", "inferred_type": "text"},
                                {"name": "Status", "inferred_type": "picklist"},
                            ],
                        }
                    ]
                },
            )
        ],
        **kw,
    )


async def test_full_staged_run():
    provider = StageProvider()
    progress: list[str] = []

    async def record(msg):
        progress.append(msg)

    ctx = _ctx(answered=[{"key": "answered_one", "question": "Old?", "answer": "yes"}])
    outcome = await run_discovery(
        provider, ctx, progress=record, single_call_budget=90_000, automation_guidance="PREFER-FLOWS"
    )
    calls = provider.calls
    assert calls[0] == "OutlineStage" and calls[-1] == "ReviewStage"
    assert sorted(calls[1:3]) == ["FieldsStage", "FieldsStage"]
    assert sorted(calls[3:5]) == ["ProcessStage", "RolesStage"]
    assert sorted(calls[5:7]) == ["AutomationsStage", "ExtrasStage"]
    assert "MappingStage" not in calls and len(calls) == 8
    assert "PREFER-FLOWS" in provider.prompts["AutomationsStage"][0]
    assert progress[0].startswith("Step 1") and any(p.startswith("Step 5") for p in progress)

    model = outcome.result.model
    e0 = model.entity("e0")
    status = next(f for f in e0.fields if f.key == "status")
    assert status.description == "The status"
    assert status.provenance.source == "file" and status.provenance.reference == f"{SHEET} > Status"
    assert next(f for f in e0.fields if f.key == "ref_no").provenance.source == "answer"
    # evidence doubles as the migration mapping
    assert {(m.column, m.field) for m in model.data_mappings} == {("Name", "name"), ("Status", "status")}
    assert next(f for f in e0.fields if f.key == "parent").type == "text"  # lookup to unknown entity repaired
    # e2 targets a missing field (pruned); e3's placeholder condition is dropped but the automation stays
    assert sorted(a.key for a in model.automations) == ["auto_e0", "auto_e1", "auto_e3"]
    assert model.automations[-1].conditions == [] or all(not c.value for a in model.automations for c in a.conditions)
    assert any("dynamic value" in c for c in outcome.result.changes)
    # review findings land on the automation, follow-up questions are merged without duplicates
    review = [i for i in outcome.issues if i.code == "ai_review"]
    assert review and review[0].path == "automations.auto_e1" and "Trigger on Won" in review[0].message
    assert [q.key for q in outcome.result.questions] == ["stages", "returns"]
    returns = outcome.result.questions[1]
    assert returns.category == "scope" and returns.priority == "medium"  # off-list values normalised
    assert any(i.path == "profile" and "Odd area" in i.message for i in review)
    assert not [i for i in outcome.issues if i.severity == "error"]
    assert outcome.keep_open_questions is False


async def test_targeted_refine_only_redoes_what_the_answers_touch():
    previous = solar_model()
    plan = RefinePlan.model_validate(
        {
            "summary": "Updated",
            "changes": ["Added approval stage"],
            "profile": None,
            "new_entities": [],
            "removed_entities": [],
            "redo_fields_for": ["deal"],
            "redo_pipelines": True,
            "redo_automations_for": ["deal"],
            "redo_roles": False,
            "redo_integrations_and_reports": False,
            "questions": [],
        }
    )
    provider = StageProvider(plan=plan)
    ctx = _ctx(
        previous_model=previous.model_dump(mode="json"),
        previous_summary="old",
        answered=[{"key": "approvals", "question": "Approvals?", "answer": "Discounts over 10%"}],
        new_answer_keys=["approvals"],
        inputs_changed=False,
    )
    outcome = await run_discovery(provider, ctx, single_call_budget=90_000)

    assert provider.calls[0] == "RefinePlan" and "OutlineStage" not in provider.calls
    assert (
        provider.calls.count("FieldsStage") == 1 and "these entities only: deal." in provider.prompts["FieldsStage"][0]
    )
    assert "RolesStage" not in provider.calls and "ExtrasStage" not in provider.calls
    assert "[NEW]" in provider.prompts["RefinePlan"][0]
    assert "Previous fields of these entities" in provider.prompts["FieldsStage"][0]
    assert "Previous design" not in provider.prompts["ProcessStage"][0]  # sent once, to the plan only
    model = outcome.result.model
    # untouched parts come straight from the previous design
    assert [r.key for r in model.roles] == [r.key for r in previous.roles]
    assert model.entity("site_visit").fields == previous.entity("site_visit").fields
    assert {a.key for a in model.automations} >= {"visit_follow_up", "default_status"}
    assert outcome.result.summary == "Updated" and outcome.keep_open_questions is True


async def test_changed_inputs_force_a_full_refine():
    provider = StageProvider()
    ctx = _ctx(previous_model=solar_model().model_dump(mode="json"), new_answer_keys=["x"], inputs_changed=True)
    await run_discovery(provider, ctx, single_call_budget=90_000)
    assert provider.calls[0] == "OutlineStage"


async def test_truncated_automations_are_split_by_entity():
    provider = StageProvider(truncate_automations_over=2)
    outcome = await run_discovery(provider, AnalysisContext(project={"description": "x"}), single_call_budget=90_000)
    assert provider.calls.count("AutomationsStage") == 3  # 4 entities fail -> 2 + 2
    assert len(outcome.result.model.automations) == 3  # e2's is invalid and pruned


async def test_rate_limit_waits_and_retries(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(staged.asyncio, "sleep", fake_sleep)

    class Flaky(StageProvider):
        failed = False

        async def generate_structured(self, **kw):
            if not self.failed:
                self.failed = True
                raise LLMRateLimitError("slow down", retry_after=7)
            return await super().generate_structured(**kw)

    outcome = await run_discovery(Flaky(), AnalysisContext(project={"description": "x"}), single_call_budget=90_000)
    assert sleeps and sleeps[0] == 8
    assert outcome.result.model.entities


async def test_large_limit_uses_single_call_and_reviews():
    class Big(StageProvider):
        async def generate_structured(self, *, system, messages, output_type, max_tokens=None):
            self.calls.append(output_type.__name__)
            if output_type is ReviewStage:
                return await super().generate_structured(
                    system=system, messages=messages, output_type=output_type, max_tokens=1000
                )
            return LLMResult(output=DiscoveryResult(summary="one shot", model=solar_model()), model="big")

    provider = Big(limit=500_000)
    outcome = await run_discovery(provider, AnalysisContext(project={}), single_call_budget=90_000)
    assert outcome.result.summary == "one shot"
    assert provider.calls[0] == "DiscoveryResult" and "ReviewStage" in provider.calls


@pytest.mark.real_pacer
async def test_pacer_counts_actual_usage_not_the_output_allowance():
    pacer = TokenPacer(8000)
    waits: list[int] = []

    async def on_wait(s):
        waits.append(s)

    t1 = await pacer.acquire(7000, on_wait)
    pacer.release(t1, 400)  # the call only used 400 tokens
    t2 = await asyncio.wait_for(pacer.acquire(7000, on_wait), timeout=1)  # admitted immediately
    assert waits == [] and t2 != t1
    pacer.release(t2, 7500)
    assert pacer._load(time.monotonic()) == 7900


def test_prune_breaks_role_cycles_and_dedupes():
    model = solar_model()
    model.roles[0].reports_to = "sales_rep"
    model.entities.append(model.entities[0].model_copy())
    pruned, notes = prune_invalid(model)
    assert len(pruned.entities) == len(model.entities) - 1
    assert any("cycle" in n for n in notes)


@pytest.mark.real_pacer
async def test_pacer_refills_continuously():
    pacer = TokenPacer(6000)  # 100 tokens per second
    waits: list[int] = []

    async def on_wait(s):
        waits.append(s)

    t = await pacer.acquire(4000, on_wait)
    pacer.release(t, 4000)
    pacer.level, pacer.stamp = 2000, time.monotonic() - 21  # 21 s later: refilled to ~4100
    await asyncio.wait_for(pacer.acquire(4000, on_wait), timeout=1)
    assert waits == []


def test_invented_sheet_references_are_not_file_evidence():
    from app.pipeline.staged import evidence_provenance

    fallback = {"source": "client_input", "reference": None, "confidence": 0.8}
    from app.domain.business_model import Provenance

    fb = Provenance.model_validate(fallback)
    assert evidence_provenance("leads.csv > Sheet1 > Phone", fb, {"leads.csv > Sheet1"}).source == "file"
    assert evidence_provenance("narrative > sales > stage", fb, {"leads.csv > Sheet1"}).source == "inferred"


async def test_long_provider_wait_fails_fast_with_a_clear_message(monkeypatch):
    from app.llm.base import LLMError

    class Exhausted(StageProvider):
        async def generate_structured(self, **kw):
            raise LLMRateLimitError("tokens per day exceeded", retry_after=1125)

    with pytest.raises(LLMError, match="about 19 minutes"):
        await run_discovery(Exhausted(), AnalysisContext(project={"description": "x"}), single_call_budget=90_000)


async def test_parallel_steps_through_the_real_job_path(auth_client, monkeypatch):
    """Parallel steps report progress concurrently; the job must still complete and save a version."""
    from app.services import analysis
    from app.services.jobs import wait_for_inline_jobs

    async def provider_for_org(session, org_id):
        return StageProvider()

    monkeypatch.setattr(analysis, "get_provider_for_org", provider_for_org)
    pid = (
        await auth_client.post("/api/projects", json={"name": "P", "client_name": "C", "description": "Solar"})
    ).json()["id"]
    job = (await auth_client.post(f"/api/projects/{pid}/analyze")).json()
    await wait_for_inline_jobs()
    job = (await auth_client.get(f"/api/projects/{pid}/jobs/{job['id']}")).json()
    assert job["status"] == "succeeded", job["error"]
    assert job["progress"] == "Done"
    version = (await auth_client.get(f"/api/projects/{pid}/model")).json()
    assert any(i["code"] == "ai_review" for i in version["issues"])
    assert version["llm_usage"]["inputs"]


async def test_a_crash_mid_run_is_recorded_as_failed(auth_client, monkeypatch):
    from app.services import analysis
    from app.services.jobs import wait_for_inline_jobs

    class Crashing(StageProvider):
        async def generate_structured(self, *, system, messages, output_type, max_tokens=None):
            if output_type is FieldsStage:
                raise RuntimeError("boom")
            return await super().generate_structured(
                system=system, messages=messages, output_type=output_type, max_tokens=max_tokens
            )

    async def provider_for_org(session, org_id):
        return Crashing()

    monkeypatch.setattr(analysis, "get_provider_for_org", provider_for_org)
    pid = (await auth_client.post("/api/projects", json={"name": "P", "client_name": "C", "description": "x"})).json()[
        "id"
    ]
    job = (await auth_client.post(f"/api/projects/{pid}/analyze")).json()
    await wait_for_inline_jobs()
    job = (await auth_client.get(f"/api/projects/{pid}/jobs/{job['id']}")).json()
    assert job["status"] == "failed" and "boom" in job["error"]
    assert (await auth_client.get(f"/api/projects/{pid}")).json()["status"] == "error"
