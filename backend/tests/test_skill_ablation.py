import json

import pytest

from agentguard.cli import main
from agentguard.domain import ProviderBinding, SkillAblationAnalysis, SkillAblationEvidence, SkillContract, SkillTraceEvent, SutProviderUsage, VerificationCriterion
from agentguard.evolution_runtime import EvolutionAgentRuntime
from agentguard.service import Service
from agentguard.skill_ablation import SkillAblationVerifier
from agentguard.skill_ablation_analysis import SkillAblationEvidenceAdapter, SKILL_ABLATION_ANALYSIS_SYSTEM_PROMPT
from agentguard.store import Store
from agentguard.provider_runtime import ProviderToolCall, ProviderTurn


def approved_contract(project_id: str, case_id: str) -> SkillContract:
    return SkillContract(
        project_id=project_id,
        evolution_case_id=case_id,
        skill_name="app_profile",
        kind="runtime_skill",
        trigger="The runtime registry dispatches app_profile after input validation.",
        execution="The native Skill invokes the configured target LLM provider.",
        deliverable="A structured application profile is returned.",
        termination="The Skill completes or emits a boundary block.",
        required_trace_event_types=["skill_started"],
        boundary_expectation="blocked",
        status="approved",
    )


def complete_evidence(contract: SkillContract) -> SkillAblationEvidence:
    trigger = SkillTraceEvent(sequence=4, event_type="skill_started", evidence_ref="trace:4")
    return SkillAblationEvidence(
        project_id=contract.project_id,
        evolution_case_id=contract.evolution_case_id,
        skill_contract_id=contract.skill_contract_id,
        trial_ref="trial:baseline:1",
        intervention="enabled",
        sut_provider_request_ids=["target-request-01"],
        sut_provider_usage=[SutProviderUsage(request_id="target-request-01", input_tokens=100, output_tokens=20)],
        trigger_event=trigger,
        trace_events=[trigger, SkillTraceEvent(sequence=5, event_type="skill_completed", evidence_ref="trace:5")],
        trace_complete=True,
        deliverable={"profile": {"name": "test app"}},
        deliverable_evidence_ref="artifact:profile",
        target_criteria=[VerificationCriterion(name="independent_profile_shape", status="passed", detail="Independent verifier accepted the structured profile.", evidence_refs=["artifact:profile"])],
        boundary_outcome="blocked",
        boundary_evidence_refs=["trace:boundary"],
    )


def test_skill_ablation_verifier_requires_all_real_execution_evidence() -> None:
    contract = approved_contract("project", "case")
    result = SkillAblationVerifier().verify(contract, complete_evidence(contract))
    assert result.status == "passed"
    assert {item.name for item in result.criteria} == {
        "target_runtime", "real_llm_execution", "provider_usage_evidence", "skill_trigger", "post_trigger_trace", "deliverable", "boundary_behavior", "no_fallback",
        "independent_deliverable_verifier", "independent_profile_shape",
    }


@pytest.mark.parametrize(
    ("change", "criterion"),
    [
        (lambda item: item.model_copy(update={"sut_provider_request_ids": []}), "real_llm_execution"),
        (lambda item: item.model_copy(update={"trace_complete": False}), "post_trigger_trace"),
        (lambda item: item.model_copy(update={"deliverable_evidence_ref": None}), "deliverable"),
        (lambda item: item.model_copy(update={"fallback_used": True}), "no_fallback"),
    ],
)
def test_skill_ablation_verifier_fails_closed(change, criterion: str) -> None:
    contract = approved_contract("project", "case")
    result = SkillAblationVerifier().verify(contract, change(complete_evidence(contract)))
    assert result.status == "failed"
    assert next(item for item in result.criteria if item.name == criterion).status == "failed"


def test_skill_ablation_verifier_rejects_incomplete_contract() -> None:
    contract = approved_contract("project", "case").model_copy(update={"status": "incomplete"})
    with pytest.raises(ValueError, match="approved SkillContract"):
        SkillAblationVerifier().verify(contract, complete_evidence(contract))


def test_skill_ablation_verifier_marks_missing_provider_usage_as_infrastructure_error() -> None:
    contract = approved_contract("project", "case")
    evidence = complete_evidence(contract).model_copy(update={"sut_provider_usage": []})
    result = SkillAblationVerifier().verify(contract, evidence)
    assert result.status == "infrastructure_error"
    assert next(item for item in result.criteria if item.name == "provider_usage_evidence").status == "failed"


def test_skill_ablation_verifier_marks_target_runtime_failure_as_infrastructure_error() -> None:
    contract = approved_contract("project", "case")
    evidence = complete_evidence(contract).model_copy(update={"runtime_error": "TimeoutError"})
    result = SkillAblationVerifier().verify(contract, evidence)
    assert result.status == "infrastructure_error"
    assert next(item for item in result.criteria if item.name == "target_runtime").status == "failed"


def test_verify_skill_ablation_cli_persists_and_returns_json(tmp_path, capsys) -> None:
    db = str(tmp_path / "agentguard.db")
    service = Service(db)
    product, _ = service.create("Skill target")
    contract = approved_contract(product.product_id, "case")
    evidence = complete_evidence(contract)
    contract_path = tmp_path / "contract.json"
    evidence_path = tmp_path / "evidence.json"
    contract_path.write_text(json.dumps(contract.model_dump()), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence.model_dump()), encoding="utf-8")

    assert main([
        "--db", db, "--format", "json", "evolution", "verify-skill-ablation",
        "--project-id", product.product_id, "--case-id", "case",
        "--contract", str(contract_path), "--evidence", str(evidence_path),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["status"] == "passed"
    store = Store(db)
    assert len(store.list("skill_ablation_contract", SkillContract, product.product_id)) == 1
    assert len(store.list("skill_ablation_evidence", SkillAblationEvidence, product.product_id)) == 1


def test_analysis_adapter_requires_evidence_linked_four_part_analysis(tmp_path) -> None:
    contract = approved_contract("project_runtime", "case")
    evidence = complete_evidence(contract)
    store = Store(str(tmp_path / "analysis.db"))
    binding = ProviderBinding(
        project_id="project_runtime", role="control_plane", provider="deepseek", base_url="https://api.deepseek.com",
        model="deepseek-v4-flash", expected_environment_variable="DEEPSEEK_API_KEY", credential_source_ref="runtime:test",
        batch_budget_usd=0.03, timeout_seconds=30, allowed_hosts=["api.deepseek.com"],
        data_retention_policy="non-secret metadata only", input_price_per_million_usd=0.14,
        output_price_per_million_usd=0.28, pricing_source="official:test-snapshot", pricing_verified_at="2026-08-01T00:00:00+00:00",
    )
    def provider_turn(sequence: int, name: str, arguments: dict[str, object]) -> ProviderTurn:
        return ProviderTurn(
            request_id=f"request_{sequence}", finish_reason="tool_calls",
            tool_calls=(ProviderToolCall(f"call_{sequence}", name, arguments),), input_tokens=100, output_tokens=20,
            cache_hit_tokens=0, request_fingerprint=str(sequence) * 64, response_fingerprint=str(sequence + 1) * 64,
        )
    class Provider:
        def __init__(self): self.calls = 0
        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return provider_turn(1, "read_skill_ablation_evidence", {})
            return provider_turn(2, "submit_skill_ablation_analysis", {
                "trigger_analysis": "The app_profile Skill was explicitly started.",
                "trace_analysis": "A completed event followed the trigger in the complete trace.",
                "deliverable_analysis": "The profile artifact is linked as the target deliverable.",
                "boundary_analysis": "The declared blocked boundary has its own trace reference.",
                "evidence_refs": ["trace:4", "trace:5", "artifact:profile", "trace:boundary"],
                "limitation": "This is an interpretation, not a verifier verdict.",
            })
    run = EvolutionAgentRuntime(
        store, binding, Provider(), SkillAblationEvidenceAdapter(store, contract, evidence),
        system_prompt=SKILL_ABLATION_ANALYSIS_SYSTEM_PROMPT,
    ).start(project_id="project_runtime", evolution_case_id="case", objective="Analyze Skill evidence.")
    assert run.status == "completed"
    analysis = store.get("skill_ablation_analysis", run.terminal_artifact_id or "", SkillAblationAnalysis)
    assert analysis and analysis.evidence_level == "inferred"
    assert len(run.model_call_ids) == 2
