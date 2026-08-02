from agentguard.domain import SkillContract
from agentguard.integrations.lighttable_skill_ablation import _build_evidence, _verify_target_outcome
from agentguard.target_runtime import TargetTraceEvidence


def _contract() -> SkillContract:
    return SkillContract(
        project_id="lighttable", evolution_case_id="recipe-case", skill_name="recipe_planning",
        kind="declared_capability", trigger="planner starts", execution="native provider call",
        deliverable="structured plans", termination="planner completes",
        required_trace_event_types=["recipe_planning_generated"], status="approved",
    )


def test_lighttable_skill_ablation_evidence_requires_target_usage_and_rejects_fallback() -> None:
    contract = _contract()
    trace = TargetTraceEvidence((
        {"event_type": "native_provider_request_started", "model": "deepseek"},
        {"event_type": "native_provider_request_completed", "request_id": "request-1", "input_tokens": 5, "output_tokens": 3, "cache_hit_tokens": 0},
        {"event_type": "recipe_planning_generated", "plan_count": 1, "core_ingredients": ["豆腐"]},
    ), {"status": "passed"})
    initial = {"recommendation_history": [], "user": [{"id": "default"}]}
    final = {"recommendation_history": [{"id": "one"}], "user": [{"id": "default"}]}

    target_criteria = tuple(_verify_target_outcome(
        response_status=200, response={"plans": [{}]}, trace=trace, initial=initial, final=final,
        evidence_ref="file:evidence",
    ))
    evidence = _build_evidence(
        contract=contract, trial_ref="enabled-1", intervention="enabled", response={"plans": [{}]},
        trace=trace, initial=initial, final=final, evidence_ref="file:evidence", target_criteria=target_criteria, runtime_error=None,
    )

    assert evidence.sut_provider_request_ids == ["request-1"]
    assert evidence.trace_complete is True
    assert evidence.fallback_used is False
    assert evidence.boundary_outcome == "none"
    assert all(item.status == "passed" for item in target_criteria)

    fallback_trace = TargetTraceEvidence(trace.events + ({"event_type": "recipe_planning_fallback"},), {"status": "passed"})
    assert _build_evidence(
        contract=contract, trial_ref="replacement-1", intervention="invalid_replacement", response={"plans": [{}]},
        trace=fallback_trace, initial=initial, final=final, evidence_ref="file:evidence", target_criteria=target_criteria, runtime_error=None,
    ).fallback_used is True
