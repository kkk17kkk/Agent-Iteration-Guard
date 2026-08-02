import json

import pytest

from agentguard.domain import ProviderBinding, SkillAblationEvidence, SkillAblationVerification, SkillContract, SkillTraceEvent, VerificationCriterion
from agentguard.product_reporting import build_product_evaluation_evidence, generate_product_report_analysis_with_provider, load_skill_ablation_artifact, render_product_evaluation_html, write_product_evaluation_report
from agentguard.provider_runtime import ProviderRuntimeError, ProviderToolCall, ProviderTurn


class FakeProvider:
    def __init__(self, arguments: dict[str, object]) -> None:
        self.arguments = arguments

    def complete(self, messages, tools) -> ProviderTurn:
        assert messages[1]["role"] == "user"
        assert tools[0]["function"]["name"] == "submit_product_report_analysis"
        return ProviderTurn("request-1", "tool_calls", (ProviderToolCall("call-1", "submit_product_report_analysis", self.arguments),), 12, 9, 0, "request-fingerprint", "response-fingerprint")


def _artifact(tmp_path):
    contract = SkillContract(project_id="demo", evolution_case_id="case", skill_name="planning", kind="runtime_skill", trigger="native provider starts", execution="planner runs", deliverable="plan", termination="plan ends", required_trace_event_types=["started"], status="approved")
    trace = SkillTraceEvent(sequence=0, event_type="started", evidence_ref="trace:0")
    evidence = SkillAblationEvidence(project_id="demo", evolution_case_id="case", skill_contract_id=contract.skill_contract_id, trial_ref="enabled", intervention="enabled", trigger_event=trace, trace_events=[trace], deliverable={"summary": "observed plan"}, deliverable_evidence_ref="deliverable:1")
    verification = SkillAblationVerification(project_id="demo", evolution_case_id="case", skill_contract_id=contract.skill_contract_id, skill_ablation_evidence_id=evidence.skill_ablation_evidence_id, status="passed", criteria=[VerificationCriterion(name="deliverable", status="passed", detail="verified", evidence_refs=["deliverable:1"])])
    for name, model in (("skill-contract.json", contract), ("skill-evidence.json", evidence), ("skill-verification.json", verification)):
        (tmp_path / name).write_text(json.dumps(model.model_dump()), encoding="utf-8")
    (tmp_path / "trial-evidence.json").write_text(json.dumps({"response": {"profile_summary": "preserved task"}}), encoding="utf-8")
    return load_skill_ablation_artifact("Demo", tmp_path)


def _analysis(evidence):
    refs = evidence["artifacts"][0]["evidence_refs"]
    sections = {
        "summary": "已完成一项保存证据支持的 Skill 消融。",
        "skill_profile": "规划 Skill 在原生调用开始时触发，并产出可验证计划。",
        "evaluation_design": "本实验保留 enabled arm；无额外对照保存，因此对照结论为 unresolved。",
        "comparison_examples": "输入任务为 preserved task；有 Skill 时交付 observed plan；无 Skill 为 unresolved。",
        "skill_ablation_analysis": "当前只有 enabled arm，无法量化能力消失，结论为 unresolved。",
        "skill_interaction_analysis": "未保存相关 Skill 的单独或组合实验，结论为 unresolved。",
        "final_assessment": "触发已保存；输出已验证；执行与边界质量仍有 unresolved 项，下一步补充对照。",
        "limitations": "单个 arm 不支持对照结论。",
    }
    return {**sections, "experiment_results": [{"arm": "enabled", "status": "passed", "explanation": "启用规划 Skill 后保存了交付物，Verifier 通过。", "evidence_refs": refs}], "citations": {name: refs for name in sections}}


def _binding() -> ProviderBinding:
    return ProviderBinding(project_id="demo", role="control_plane", provider="vllm", base_url="http://127.0.0.1:8000/v1", model="local", expected_environment_variable="VLLM_API_KEY", credential_source_ref="test", batch_budget_usd=0, timeout_seconds=10, allowed_hosts=["127.0.0.1"], data_retention_policy="test")


def test_product_report_is_evidence_bound_and_hides_technical_metadata(tmp_path) -> None:
    evidence = build_product_evaluation_evidence("Demo", [_artifact(tmp_path)])
    report = generate_product_report_analysis_with_provider(evidence, provider=FakeProvider(_analysis(evidence)), binding=_binding())
    evidence_path, report_path, html_path = write_product_evaluation_report(tmp_path / "report", evidence, report)
    rendered = html_path.read_text(encoding="utf-8")
    assert evidence_path.is_file() and report_path.is_file()
    assert "产品评估报告" in rendered and "Technical Evidence" in rendered
    assert "input_tokens" not in rendered
    assert report["evidence_manifest_sha256"] == evidence["evidence_manifest_sha256"]


def test_product_report_rejects_status_changes_or_uncited_prose(tmp_path) -> None:
    evidence = build_product_evaluation_evidence("Demo", [_artifact(tmp_path)])
    invalid = _analysis(evidence)
    invalid["experiment_results"][0]["status"] = "failed"
    with pytest.raises(ProviderRuntimeError, match="preserve"):
        generate_product_report_analysis_with_provider(evidence, provider=FakeProvider(invalid), binding=_binding())
    invalid = _analysis(evidence)
    invalid["citations"]["final_assessment"] = ["invented:ref"]
    with pytest.raises(ProviderRuntimeError, match="evidence reference"):
        render_product_evaluation_html(evidence, {"analysis": invalid})
