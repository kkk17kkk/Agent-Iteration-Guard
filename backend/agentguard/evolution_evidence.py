from __future__ import annotations

from .domain import AgentEvolutionCase, EvaluationPipeline, EvolutionComparison, EvolutionTrial, EvolutionVerification
from .store import Store


class EvolutionEvidenceError(RuntimeError):
    pass


def recompute_evolution_comparison(
    store: Store, project_id: str, evolution_case_id: str
) -> EvolutionComparison:
    case = store.get("agent_evolution_case", evolution_case_id, AgentEvolutionCase)
    if not case or case.project_id != project_id:
        raise EvolutionEvidenceError("Evolution case is unavailable in this project")
    trials = [
        item for item in store.list("evolution_trial", EvolutionTrial, project_id)
        if item.evolution_case_id == evolution_case_id
    ]
    verifications = {
        item.evolution_trial_id: item
        for item in store.list("evolution_verification", EvolutionVerification, project_id)
        if item.evolution_case_id == evolution_case_id
    }
    paired: list[tuple[EvolutionTrial, EvolutionVerification, EvolutionTrial, EvolutionVerification]] = []
    for trial_index in sorted({item.trial_index for item in trials}):
        baseline_trials = [
            item for item in trials
            if item.trial_index == trial_index and item.revision_role == "baseline"
        ]
        candidate_trials = [
            item for item in trials
            if item.trial_index == trial_index and item.revision_role == "candidate"
        ]
        if len(baseline_trials) > 1 or len(candidate_trials) > 1:
            raise EvolutionEvidenceError(
                f"Pair {trial_index} has ambiguous duplicate revision evidence"
            )
        baseline = baseline_trials[0] if baseline_trials else None
        candidate = candidate_trials[0] if candidate_trials else None
        if not baseline or not candidate:
            continue
        if baseline.environment_fingerprint != candidate.environment_fingerprint:
            raise EvolutionEvidenceError(f"Pair {trial_index} environment fingerprints differ")
        if baseline.request_fingerprint != candidate.request_fingerprint:
            raise EvolutionEvidenceError(f"Pair {trial_index} request fingerprints differ")
        if baseline.initial_state_ref != candidate.initial_state_ref:
            raise EvolutionEvidenceError(f"Pair {trial_index} initial states differ")
        baseline_verification = verifications.get(baseline.evolution_trial_id)
        candidate_verification = verifications.get(candidate.evolution_trial_id)
        if not baseline_verification or not candidate_verification:
            continue
        paired.append((baseline, baseline_verification, candidate, candidate_verification))
    if not paired:
        raise EvolutionEvidenceError("No complete revision-linked paired trial is available")

    evidence_ids = [
        item.evolution_verification_id
        for pair in paired
        for item in (pair[1], pair[3])
    ]
    supported = all(pair[1].status == "failed" and pair[3].status == "passed" for pair in paired)
    conclusion = (
        f"Deterministic recomputation supports a baseline-fail/candidate-pass behavior difference across {len(paired)} paired trial(s)."
        if supported
        else f"Paired evidence across {len(paired)} trial(s) does not uniformly support a baseline-fail/candidate-pass difference."
    )
    existing = [
        item for item in store.list("evolution_comparison", EvolutionComparison, project_id)
        if item.evolution_case_id == evolution_case_id
    ]
    comparison = (
        existing[-1].model_copy(update={"status": "compared", "conclusion": conclusion, "evidence_ids": evidence_ids})
        if existing
        else EvolutionComparison(
            project_id=project_id,
            evolution_case_id=evolution_case_id,
            status="compared",
            conclusion=conclusion,
            evidence_ids=evidence_ids,
        )
    )
    records: list[tuple[str, str, str, object]] = [
        ("evolution_comparison", comparison.evolution_comparison_id, project_id, comparison)
    ]
    pipelines = [
        item for item in store.list("evaluation_pipeline", EvaluationPipeline, project_id)
        if item.evolution_case_id == evolution_case_id and item.status in {"queued", "running"}
    ]
    if pipelines:
        pipeline = pipelines[-1].model_copy(update={"status": "completed"})
        records.append(("evaluation_pipeline", pipeline.evaluation_pipeline_id, project_id, pipeline))
    store.save_many(records)  # type: ignore[arg-type]
    return comparison
