from app.domain.business_model import DiscoveryResult
from app.llm.base import LLMProvider, LLMResult, LLMUsage
from app.pipeline.discovery import AnalysisContext, run_discovery
from app.pipeline.review import ReviewStage
from tests.factories import solar_model


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.reviews = 0

    async def generate_structured(self, *, system, messages, output_type, max_tokens=None):
        if output_type is ReviewStage:
            self.reviews += 1
            return LLMResult(output=ReviewStage(findings=[], questions=[]), model="scripted", usage=LLMUsage(1, 1))
        self.calls.append(messages)
        return LLMResult(output=self.outputs.pop(0), model="scripted", usage=LLMUsage(10, 5))


async def test_repair_loop_feeds_errors_back():
    broken = solar_model()
    broken.processes[0].entity = "unknown_entity"
    fixed = solar_model()
    provider = ScriptedProvider(
        [
            DiscoveryResult(summary="v1", model=broken),
            DiscoveryResult(summary="v2", model=fixed),
        ]
    )
    outcome = await run_discovery(provider, AnalysisContext(project={"name": "x"}), repair_attempts=1)
    assert outcome.attempts == 2
    assert outcome.result.summary == "v2"
    assert outcome.usage.input_tokens == 21  # 2 design calls + 1 review call
    repair_msg = provider.calls[1][-1].content
    assert "unknown_entity" in repair_msg and provider.calls[1][-2].role == "assistant"
    assert provider.reviews == 1  # the finished design is reviewed once


async def test_settled_questions_are_not_reasked():
    result = DiscoveryResult.model_validate(
        {
            "summary": "s",
            "model": solar_model().model_dump(),
            "questions": [
                {"key": "stages", "question": "?", "why": "w", "category": "process", "priority": "high"},
                {"key": "teams", "question": "?", "why": "w", "category": "security", "priority": "low"},
            ],
        }
    )
    ctx = AnalysisContext(project={}, answered=[{"key": "stages", "question": "?", "answer": "a"}])
    outcome = await run_discovery(ScriptedProvider([result]), ctx)
    assert [q.key for q in outcome.result.questions] == ["teams"]
