from agentguard.domain import SkillContract
from agentguard.integrations.chord_skill_ablation import _build_evidence, _verify_target_output
from agentguard.skill_ablation import SkillAblationVerifier
from agentguard.target_runtime import TargetTraceEvidence


def _output(report: str) -> dict[str, object]:
    structured_result = {
        "report_markdown": report,
        "model_trace": {"used_llm": True, "fallback_reason": ""},
        "evidence": {"raw_counts": {"row_count": 2}},
        "metrics": {"installed_app_count": 2, "lending_app_count": 1},
    }
    return {"result": {"results": [{"result": {"data": {"structured_result": structured_result}}}]}}


def _contract() -> SkillContract:
    return SkillContract(
        project_id="chord", evolution_case_id="app-profile", skill_name="app_profile",
        kind="runtime_skill", trigger="native provider call", execution="profile DAG",
        deliverable="structured profile", termination="profile completion",
        required_trace_event_types=["native_provider_request_started"], status="approved",
    )


def test_chord_skill_ablation_rejects_report_that_contradicts_target_metrics() -> None:
    trace = TargetTraceEvidence((
        {"event_type": "native_provider_request_started"},
        {"event_type": "native_provider_request_completed", "request_id": "request-1", "input_tokens": 2, "output_tokens": 3, "cache_hit_tokens": 0},
        {"event_type": "profile_run_completed"},
    ), {"status": "passed"})
    output = _output("共安装 12 款应用。借贷 App 数量：3 个。")
    criteria = tuple(_verify_target_output(output, trace, True, "file:evidence"))
    contract = _contract()
    evidence = _build_evidence(contract, "enabled-1", "enabled", output, trace, True, "file:evidence", criteria)

    assert next(item for item in criteria if item.name == "profile_evidence_consistency").status == "failed"
    assert SkillAblationVerifier().verify(contract, evidence).status == "failed"


def test_chord_skill_ablation_accepts_report_consistent_with_target_metrics() -> None:
    trace = TargetTraceEvidence((
        {"event_type": "native_provider_request_started"},
        {"event_type": "native_provider_request_completed", "request_id": "request-1", "input_tokens": 2, "output_tokens": 3, "cache_hit_tokens": 0},
        {"event_type": "profile_run_completed"},
    ), {"status": "passed"})
    criteria = _verify_target_output(_output("共安装 2 款应用。借贷 App 数量：1 个。"), trace, True, "file:evidence")

    assert all(item.status == "passed" for item in criteria)
