import json

import pytest

from agentguard.cli import main
from agentguard.domain import Capability, EvalPlan, LLMAssistance, ReleaseDecision, Requirement
from agentguard.llm import Completion, DeepSeekAssistant
from agentguard.service import AssistantOutputError, Service


class ScriptedAssistant:
    provider = "scripted-test"

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def complete_json(self, system_prompt: str, input_payload: dict[str, object]) -> Completion:
        return Completion(
            provider_request_id="scripted-request",
            model="scripted-model",
            content=json.dumps(self.response),
        )


def test_failure_explanation_is_inferred_and_cannot_change_release_decision(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_agent_fixture()
    result = service.run_file_agent(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
    )
    original_decision = result.release_decision.model_dump()
    original_plan = result.eval_plan.model_dump()
    permission_change = next(change for change in result.changeset.changes if change.kind == "permission_changed")

    assistance = service.explain_failure(
        result.run.harness_run_id,
        ScriptedAssistant(
            {
                "failure_type": "permission_violation",
                "explanation": "该回归可能由 Skill 新增的 write_file 权限导致。",
                "suspected_change_ids": [permission_change.change_id],
                "limitation": "这是基于已验证 Oracle 结果的推断，不是发布决策。",
            }
        ),
    )

    assert assistance.kind == "failure_explanation"
    assert assistance.evidence_level == "inferred"
    assert assistance.input_artifact_ids == [
        result.findings[0].finding_id,
        result.verifications[1].verification_id,
        result.changeset.changeset_id,
    ]
    assert service.store.get("release_decision", result.release_decision.decision_id, ReleaseDecision).model_dump() == original_decision
    assert service.store.get("eval_plan", result.eval_plan.eval_plan_id, EvalPlan).model_dump() == original_plan
    events = service.store.list("run_event", type(result.events[0]), fixture.product.product_id)
    assert events[-1].event_type == "LLM_ASSISTANCE_RECORDED"


def test_requirement_mapping_is_a_candidate_and_does_not_mutate_capability_or_plan(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_agent_fixture()
    result = service.run_file_agent(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
    )
    requirement = service.store.list("requirement", Requirement, fixture.product.product_id)[0]
    capabilities_before = [item.model_dump() for item in service.store.list("capability", Capability, fixture.product.product_id)]
    plan_before = result.eval_plan.model_dump()
    permission_change = next(change for change in result.changeset.changes if change.kind == "permission_changed")

    assistance = service.suggest_requirement_mapping(
        fixture.product.product_id,
        requirement.requirement_id,
        result.changeset.changeset_id,
        ScriptedAssistant(
            {
                "requirement_id": requirement.requirement_id,
                "candidate_capability": "security capability",
                "impacted_change_ids": [permission_change.change_id],
                "rationale": "只读需求与权限策略变更共同影响安全能力。",
                "confidence": "medium",
            }
        ),
    )

    assert assistance.kind == "requirement_mapping"
    assert assistance.output.candidate_capability == "security capability"
    assert [item.model_dump() for item in service.store.list("capability", Capability, fixture.product.product_id)] == capabilities_before
    assert service.store.get("eval_plan", result.eval_plan.eval_plan_id, EvalPlan).model_dump() == plan_before


def test_deepseek_adapter_uses_bounded_json_without_tools(monkeypatch):
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return b'{"id":"request-1","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("agentguard.llm.urlopen", fake_urlopen)
    completion = DeepSeekAssistant().complete_json("Return JSON", {"failure_type": "permission_violation"})

    assert completion.provider_request_id == "request-1"
    assert completion.content == '{"ok":true}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["timeout"] == 30
    assert captured["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "Return JSON"},
            {"role": "user", "content": 'INPUT:\n{"failure_type": "permission_violation"}'},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 300,
        "stream": False,
    }


def test_invalid_llm_contract_is_visible_and_does_not_persist_an_artifact(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_agent_fixture()
    result = service.run_file_agent(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
    )
    requirement = service.store.list("requirement", Requirement, fixture.product.product_id)[0]

    with pytest.raises(AssistantOutputError, match="declared assistant contract"):
        service.suggest_requirement_mapping(
            fixture.product.product_id,
            requirement.requirement_id,
            result.changeset.changeset_id,
            ScriptedAssistant(
                {
                    "requirement_id": requirement.requirement_id,
                    "candidate_capability": "security capability",
                    "impacted_change_ids": [],
                    "rationale": "Invalid confidence must not be coerced.",
                    "confidence": 0.95,
                }
            ),
        )
    assert service.store.list("llm_assistance", LLMAssistance, fixture.product.product_id) == []


def test_cli_returns_structured_error_when_llm_assistance_fails(monkeypatch, capsys):
    class FailingService:
        def __init__(self, db: str) -> None:
            self.db = db

        def explain_failure(self, run_id: str):
            raise AssistantOutputError("LLM response does not conform to the declared assistant contract.")

    monkeypatch.setattr("agentguard.cli.Service", FailingService)

    assert main(["--format", "json", "assistant", "explain", "--run-id", "run_1"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["stage"] == "llm_assistant"
