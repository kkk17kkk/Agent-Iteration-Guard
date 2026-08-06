"""Deterministic Release Decision Gate for Product Evaluation Reports.

The gate consumes only persisted report facts and the report's immutable
evidence envelope.  It never calls an LLM and never rewrites Analyst prose.
Semantic findings can require human review, but they cannot turn incomplete
evidence into an approval.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .benchmark_evidence import recompute_integrity_hash
from .product_evaluation_report import ProductEvaluationReport


GateDecision = Literal["approve", "review", "block"]
GateCheckStatus = Literal["passed", "review", "blocked"]


class ReleaseGateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    status: GateCheckStatus
    detail: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class ReleaseDecisionGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.release-decision-gate.v1"] = "aig.release-decision-gate.v1"
    report_id: str = Field(min_length=1)
    report_hash: str = Field(min_length=16)
    evidence_manifest_hash: str = Field(min_length=16)
    decision: GateDecision
    deterministic: Literal[True] = True
    ruleset: Literal["evidence_complete_and_semantic_review.v1"] = "evidence_complete_and_semantic_review.v1"
    checks: list[ReleaseGateCheck] = Field(min_length=1)
    blocking_reasons: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


def evaluate_release_decision(report: ProductEvaluationReport) -> ReleaseDecisionGateResult:
    """Return a release decision using only deterministic report contracts."""

    checks: list[ReleaseGateCheck] = []
    blockers: list[str] = []
    reviews: list[str] = []

    def add_check(
        name: str,
        status: GateCheckStatus,
        detail: str,
        *,
        evidence_refs: list[str] | None = None,
    ) -> None:
        checks.append(ReleaseGateCheck(name=name, status=status, detail=detail, evidence_refs=list(evidence_refs or [])))

    def block(reason: str) -> None:
        blockers.append(reason)

    def review(reason: str) -> None:
        reviews.append(reason)

    evidence = report.evidence
    evidence_refs = [evidence.artifact_manifest_hash]
    if evidence.conditions:
        evidence_refs.extend(evidence.conditions[0].evidence_refs[:2])

    report_complete = report.status == "completed"
    add_check(
        "report_completed",
        "passed" if report_complete else "blocked",
        "Product Evaluation Report status is completed." if report_complete else "Report is not completed.",
        evidence_refs=evidence_refs,
    )
    if not report_complete:
        block("Product Evaluation Report is not completed.")

    evidence_complete = report.evaluation.evidence_status == "evidence_complete"
    integrity_complete = evidence.integrity.status == "complete"
    add_check(
        "evidence_integrity",
        "passed" if evidence_complete and integrity_complete else "blocked",
        (
            "Evidence status and immutable integrity are complete."
            if evidence_complete and integrity_complete
            else f"evidence_status={report.evaluation.evidence_status}; integrity={evidence.integrity.status}."
        ),
        evidence_refs=evidence_refs,
    )
    if not evidence_complete or not integrity_complete:
        block("Evidence is incomplete or conflicted.")
    if evidence.integrity.missing:
        block("Evidence is missing: " + ", ".join(evidence.integrity.missing))
    if evidence.integrity.conflicts:
        block("Evidence conflicts are present: " + ", ".join(evidence.integrity.conflicts))

    report_hash_valid = (
        callable(getattr(report, "recompute_report_hash", None))
        and report.recompute_report_hash() == report.report_hash
    )
    provenance_hash_valid = report.provenance.evidence_manifest_hash == evidence.artifact_manifest_hash
    add_check(
        "report_hash_integrity",
        "passed" if report_hash_valid and provenance_hash_valid else "blocked",
        f"report_hash_valid={report_hash_valid}; evidence_manifest_binding={provenance_hash_valid}.",
        evidence_refs=evidence_refs,
    )
    if not report_hash_valid:
        block("Persisted Product Evaluation Report hash does not match its contents.")
    if not provenance_hash_valid:
        block("Report provenance is not bound to the persisted evidence manifest.")

    supplementary = list(getattr(report, "supplementary_evidence", []) or [])
    if supplementary:
        supplementary_valid = all(
            item.evidence_level == "external"
            and len(item.source_sha256) == 64
            and bool(item.evidence_refs)
            and item.integrity_hash == recompute_integrity_hash(item)
            for item in supplementary
        )
        supplementary_refs = [ref for item in supplementary for ref in item.evidence_refs]
        add_check(
            "supplementary_benchmark_evidence",
            "passed" if supplementary_valid else "blocked",
            f"imported_records={len(supplementary)}; integrity_valid={supplementary_valid}; external_only=True.",
            evidence_refs=supplementary_refs,
        )
        if not supplementary_valid:
            block("Supplementary benchmark evidence is malformed or its integrity binding is invalid.")

    verified_conditions = all(
        condition.observations.get("oracle_verified") is True
        for condition in evidence.conditions
    )
    condition_count = len(evidence.conditions)
    failure_rate = evidence.summary.get("failure_rate")
    no_failures = isinstance(failure_rate, (int, float)) and not isinstance(failure_rate, bool) and failure_rate == 0
    add_check(
        "verified_execution_evidence",
        "passed" if verified_conditions and no_failures else "blocked",
        f"conditions={condition_count}; oracle_verified={verified_conditions}; failure_rate={failure_rate}.",
        evidence_refs=evidence_refs,
    )
    if not verified_conditions:
        block("At least one execution condition lacks an independently verified oracle.")
    if not no_failures:
        block("Execution evidence reports a non-zero or unavailable failure rate.")

    if report.evaluation_type == "skill_pair_evaluation":
        type_data = evidence.type_data
        scenario_ids = list(type_data.get("scenario_ids") or [])
        expected_keys = {(scenario_id, kind) for scenario_id in scenario_ids for kind in ("a_only", "b_only", "combined")}
        observed_keys = {
            (condition.scenario_id, str(condition.observations.get("condition_kind") or ""))
            for condition in evidence.conditions
        }
        matrix_complete = bool(scenario_ids) and observed_keys == expected_keys
        interaction_present = report.interaction_analysis is not None
        add_check(
            "interaction_matrix_complete",
            "passed" if matrix_complete and interaction_present else "blocked",
            f"scenarios={len(scenario_ids)}; condition_matrix={len(observed_keys)}/{len(expected_keys)}; interaction_analysis={interaction_present}.",
            evidence_refs=evidence_refs,
        )
        if not matrix_complete:
            block("Skill Pair evidence does not contain exactly one A-only, B-only, and combined condition per scenario.")
        if not interaction_present:
            block("Skill Pair report is missing cross-scenario interaction analysis.")
        if type_data.get("scenario_readiness_required") is True:
            readiness_status = type_data.get("scenario_readiness_status")
            readiness_ok = readiness_status == "ready"
            add_check(
                "scenario_readiness",
                "passed" if readiness_ok else "blocked",
                f"scenario_readiness_status={readiness_status}.",
                evidence_refs=evidence_refs,
            )
            if not readiness_ok:
                block("Scenario input/fixture readiness was not completed before execution.")
        required_interaction_dimensions = [
            "capability_contribution",
            "composition_gain",
            "synergy_gain",
            "coordination",
            "conflict",
            "reliability_cost",
        ]
        interaction_conclusions = list(
            getattr(report.interaction_analysis, "dimension_conclusions", []) or []
        ) if interaction_present else []
        observed_interaction_dimensions = [
            str(getattr(item, "dimension", "")) for item in interaction_conclusions
        ]
        allowed_evidence_refs = {
            ref
            for condition in evidence.conditions
            for ref in condition.evidence_refs
        } | {
            ref
            for fact in getattr(evidence, "facts", [])
            for ref in fact.evidence_refs
        }
        conclusion_refs_valid = all(
            bool(getattr(item, "evidence_refs", []))
            and set(getattr(item, "evidence_refs", [])) <= allowed_evidence_refs
            for item in interaction_conclusions
        )
        interaction_dimension_contract = (
            observed_interaction_dimensions == required_interaction_dimensions and conclusion_refs_valid
        )
        add_check(
            "interaction_dimension_evidence",
            "passed" if interaction_dimension_contract else "blocked",
            "Six cross-scenario conclusions are ordered and bound to execution evidence."
            if interaction_dimension_contract
            else f"expected={required_interaction_dimensions}; observed={observed_interaction_dimensions}; evidence_refs_valid={conclusion_refs_valid}.",
            evidence_refs=evidence_refs,
        )
        if not interaction_dimension_contract:
            block("Skill Pair interaction conclusions are missing, misordered, or not evidence-bound.")
        unresolved_interaction = [
            str(getattr(item, "dimension", ""))
            for item in interaction_conclusions
            if getattr(item, "status", "unresolved") in {"unresolved", "mixed", "partially_supported"}
        ]
        if unresolved_interaction:
            review(
                "Cross-scenario interaction conclusions remain bounded or unresolved: "
                + ", ".join(unresolved_interaction)
                + "."
            )

    executive_status = report.executive_summary.status
    if executive_status != "supported":
        review(f"Executive product conclusion is {executive_status}; release requires product-owner review.")
    dimension_statuses = [item.status for item in report.executive_summary.dimensions if item.status != "supported"]
    if dimension_statuses:
        review("Non-supported evaluation dimensions remain: " + ", ".join(sorted(set(dimension_statuses))) + ".")
    stability_status = report.scenario_stability.status
    if stability_status != "supported":
        review(f"Cross-scenario stability is {stability_status}.")
    release_relevance = report.business_impact.release_relevance
    if release_relevance == "blocked_by_evidence":
        block("Product impact explicitly marks release as blocked by evidence.")
    elif release_relevance == "requires_review":
        review("Product impact requires release review.")
    high_priority = [item.priority for item in report.recommendations if item.priority in {"high", "critical"}]
    if high_priority:
        review("The report contains high-priority follow-up recommendations: " + ", ".join(sorted(set(high_priority))) + ".")

    decision: GateDecision = "block" if blockers else "review" if reviews else "approve"
    if decision == "approve":
        rationale = "All deterministic evidence gates passed and the Product Evaluation Report contains no unresolved semantic review condition."
    elif decision == "review":
        rationale = "Deterministic evidence is complete, but the report contains semantic product risks or unresolved dimensions requiring human review."
    else:
        rationale = "Release is blocked because one or more deterministic evidence gates failed."

    return ReleaseDecisionGateResult(
        report_id=report.report_id,
        report_hash=report.report_hash,
        evidence_manifest_hash=evidence.artifact_manifest_hash,
        decision=decision,
        checks=checks,
        blocking_reasons=blockers,
        review_reasons=reviews,
        rationale=rationale,
    )


__all__ = ["ReleaseDecisionGateResult", "ReleaseGateCheck", "evaluate_release_decision"]
