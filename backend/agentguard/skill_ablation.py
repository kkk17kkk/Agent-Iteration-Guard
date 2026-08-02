from __future__ import annotations

from .domain import (
    SkillAblationEvidence,
    SkillAblationVerification,
    SkillContract,
    VerificationCriterion,
)
from .store import Store


class SkillAblationVerifier:
    """Fail-closed deterministic evidence checker for real Skill interventions."""

    def verify(
        self, contract: SkillContract, evidence: SkillAblationEvidence
    ) -> SkillAblationVerification:
        if contract.status != "approved":
            raise ValueError("Skill ablation requires an approved SkillContract")
        if evidence.project_id != contract.project_id or evidence.evolution_case_id != contract.evolution_case_id:
            raise ValueError("Skill evidence does not belong to the SkillContract scope")
        if evidence.skill_contract_id != contract.skill_contract_id:
            raise ValueError("Skill evidence and SkillContract do not match")

        criteria: list[VerificationCriterion] = []

        def add(name: str, passed: bool, detail: str, refs: list[str] | None = None) -> None:
            criteria.append(VerificationCriterion(
                name=name,
                status="passed" if passed else "failed",
                detail=detail,
                evidence_refs=refs or [],
            ))

        add(
            "target_runtime",
            evidence.runtime_error is None,
            "Target runtime completed without an infrastructure error."
            if evidence.runtime_error is None else f"Target runtime error: {evidence.runtime_error}.",
        )

        real_llm = bool(evidence.sut_provider_request_ids)
        add(
            "real_llm_execution",
            real_llm or not contract.requires_real_llm,
            "The target runtime recorded at least one native provider request." if real_llm else "No target-native provider request was recorded.",
            [f"provider_request:{item}" for item in evidence.sut_provider_request_ids],
        )

        usage_request_ids = {item.request_id for item in evidence.sut_provider_usage}
        usage_ok = not real_llm or set(evidence.sut_provider_request_ids).issubset(usage_request_ids)
        add(
            "provider_usage_evidence",
            usage_ok,
            "Every native provider request has non-secret token accounting."
            if usage_ok else "At least one native provider request has no token accounting; cost cannot be audited.",
            [f"provider_usage:{item.request_id}" for item in evidence.sut_provider_usage],
        )

        trigger = evidence.trigger_event
        trigger_ok = bool(trigger and trigger.event_type in contract.required_trace_event_types)
        add(
            "skill_trigger",
            trigger_ok,
            "The declared Skill trigger was recorded with an approved event type."
            if trigger_ok else "No declared Skill trigger event was recorded.",
            [trigger.evidence_ref] if trigger else [],
        )

        trace_ok = evidence.trace_complete and bool(trigger) and any(
            item.sequence > trigger.sequence for item in evidence.trace_events
        )
        add(
            "post_trigger_trace",
            trace_ok,
            "A complete trace contains at least one event after the Skill trigger."
            if trace_ok else "Trace is incomplete or contains no post-trigger event.",
            [item.evidence_ref for item in evidence.trace_events],
        )

        deliverable_ok = bool(evidence.deliverable) and bool(evidence.deliverable_evidence_ref)
        add(
            "deliverable",
            deliverable_ok,
            "A structured target deliverable is linked to immutable evidence."
            if deliverable_ok else "Target deliverable or its evidence reference is missing.",
            [evidence.deliverable_evidence_ref] if evidence.deliverable_evidence_ref else [],
        )

        independent_verifier_ok = bool(evidence.target_criteria) or not contract.requires_independent_verifier
        add(
            "independent_deliverable_verifier",
            independent_verifier_ok,
            "A target-specific independent verifier scored the deliverable."
            if independent_verifier_ok else "No target-specific independent deliverable verification was recorded.",
            [reference for criterion in evidence.target_criteria for reference in criterion.evidence_refs],
        )
        criteria.extend(evidence.target_criteria)

        boundary_ok = (
            evidence.boundary_outcome == contract.boundary_expectation
            and (evidence.boundary_outcome == "none" or bool(evidence.boundary_evidence_refs))
        )
        add(
            "boundary_behavior",
            boundary_ok,
            "Boundary behavior matches the declared contract."
            if boundary_ok else "Boundary behavior does not match the declared contract or lacks evidence.",
            evidence.boundary_evidence_refs,
        )

        add(
            "no_fallback",
            not evidence.fallback_used,
            "No deterministic or alternate target path was used."
            if not evidence.fallback_used else "A fallback path was used and cannot support real Skill acceptance.",
        )
        status = (
            "infrastructure_error" if evidence.runtime_error is not None or (real_llm and not usage_ok)
            else "passed" if all(item.status == "passed" for item in criteria)
            else "failed"
        )
        return SkillAblationVerification(
            project_id=contract.project_id,
            evolution_case_id=contract.evolution_case_id,
            skill_contract_id=contract.skill_contract_id,
            skill_ablation_evidence_id=evidence.skill_ablation_evidence_id,
            status=status,
            criteria=criteria,
        )


def record_skill_ablation(
    store: Store, contract: SkillContract, evidence: SkillAblationEvidence
) -> SkillAblationVerification:
    verification = SkillAblationVerifier().verify(contract, evidence)
    store.save("skill_ablation_contract", contract.skill_contract_id, contract.project_id, contract)
    store.save("skill_ablation_evidence", evidence.skill_ablation_evidence_id, contract.project_id, evidence)
    store.save(
        "skill_ablation_verification",
        verification.skill_ablation_verification_id,
        contract.project_id,
        verification,
    )
    return verification
