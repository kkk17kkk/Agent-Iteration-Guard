from types import SimpleNamespace as NS

from agentguard.release_decision_gate import evaluate_release_decision


def _report(
    *,
    executive_status="supported",
    dimension_status="supported",
    stability_status="supported",
    release_relevance="informational",
    failure_rate=0.0,
    matrix_complete=True,
    interaction_present=True,
    report_hash_valid=True,
):
    scenario_ids = ["scenario_1", "scenario_2", "scenario_3"]
    conditions = []
    for scenario_id in scenario_ids:
        for condition_kind in ("a_only", "b_only", "combined"):
            if matrix_complete or (scenario_id, condition_kind) != ("scenario_3", "combined"):
                conditions.append(NS(
                    scenario_id=scenario_id,
                    evidence_refs=["evidence-ref"],
                    observations={"condition_kind": condition_kind, "oracle_verified": True},
                ))
    evidence = NS(
        artifact_manifest_hash="sha256:artifact-manifest",
        conditions=conditions,
        facts=[],
        summary={"failure_rate": failure_rate},
        integrity=NS(status="complete", missing=[], conflicts=[]),
        type_data={"scenario_ids": scenario_ids},
    )
    dimensions = [
        NS(dimension=name, status=dimension_status)
        for name in ("trigger", "capability_contribution", "synergy_gain", "coordination", "conflict", "reliability_cost")
    ]
    report = NS(
        report_id="report-1",
        report_hash="abcdef0123456789",
        status="completed",
        evaluation_type="skill_pair_evaluation",
        evaluation=NS(evidence_status="evidence_complete"),
        evidence=evidence,
        provenance=NS(evidence_manifest_hash="sha256:artifact-manifest"),
        executive_summary=NS(status=executive_status, dimensions=dimensions),
        scenario_stability=NS(status=stability_status),
        business_impact=NS(release_relevance=release_relevance),
        recommendations=[],
        interaction_analysis=(
            NS(
                dimension_conclusions=[
                    NS(dimension=name, status=dimension_status, evidence_refs=["evidence-ref"])
                    for name in (
                        "capability_contribution",
                        "composition_gain",
                        "synergy_gain",
                        "coordination",
                        "conflict",
                        "reliability_cost",
                    )
                ]
            )
            if interaction_present
            else None
        ),
    )
    report.recompute_report_hash = lambda: report.report_hash if report_hash_valid else "tampered-report-hash"
    return report


def test_complete_supported_report_is_approved() -> None:
    result = evaluate_release_decision(_report())

    assert result.decision == "approve"
    assert result.deterministic is True
    assert result.blocking_reasons == []


def test_semantic_risk_requires_review_but_does_not_block_complete_evidence() -> None:
    result = evaluate_release_decision(
        _report(executive_status="partially_supported", dimension_status="mixed", release_relevance="requires_review")
    )

    assert result.decision == "review"
    assert result.blocking_reasons == []
    assert result.review_reasons


def test_nonzero_failure_rate_blocks_release() -> None:
    result = evaluate_release_decision(_report(failure_rate=0.25))

    assert result.decision == "block"
    assert any("failure rate" in reason for reason in result.blocking_reasons)


def test_incomplete_pair_matrix_blocks_release() -> None:
    result = evaluate_release_decision(_report(matrix_complete=False))

    assert result.decision == "block"
    assert any("exactly one A-only" in reason for reason in result.blocking_reasons)


def test_missing_scenario_readiness_blocks_new_input_aware_matrix() -> None:
    report = _report()
    report.evidence.type_data.update({
        "interaction_model": "scenario_matrix",
        "scenario_readiness_required": True,
        "scenario_readiness_status": "not_recorded",
    })

    result = evaluate_release_decision(report)

    assert result.decision == "block"
    assert any("readiness" in reason for reason in result.blocking_reasons)


def test_tampered_report_hash_blocks_release() -> None:
    result = evaluate_release_decision(_report(report_hash_valid=False))

    assert result.decision == "block"
    assert any("hash" in reason for reason in result.blocking_reasons)
