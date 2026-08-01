from pathlib import Path

import pytest

from agentguard.domain import AgentEvolutionCase, EvolutionTrial, EvolutionVerification, VerificationCriterion
from agentguard.evolution_evidence import EvolutionEvidenceError, recompute_evolution_comparison
from agentguard.store import Store


def trial(project: str, case: str, role: str, index: int, environment: str = "e" * 64) -> EvolutionTrial:
    return EvolutionTrial(
        project_id=project,
        evolution_case_id=case,
        revision_id=f"revision_{role}",
        revision_role=role,
        trial_index=index,
        status="completed",
        environment_fingerprint=environment,
        reset_evidence_ref="reset:x",
        request_fingerprint="r" * 64,
        response_evidence_ref="response:x",
        trace_evidence_ref="trace:x",
        initial_state_ref="sha256:" + "i" * 64,
        final_state_ref="sha256:" + role,
        terminal_reason="completed",
    )


def test_recompute_comparison_requires_same_pair_inputs(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "comparison.db"))
    project, case_id = "project", "case"
    case = AgentEvolutionCase(
        evolution_case_id=case_id,
        project_id=project,
        source_id="source",
        baseline_revision_id="revision_baseline",
        candidate_revision_id="revision_candidate",
        evolution_changeset_id="changeset",
    )
    baseline = trial(project, case_id, "baseline", 1)
    candidate = trial(project, case_id, "candidate", 1, environment="x" * 64)
    store.save_many([
        ("agent_evolution_case", case_id, project, case),
        ("evolution_trial", baseline.evolution_trial_id, project, baseline),
        ("evolution_trial", candidate.evolution_trial_id, project, candidate),
    ])
    with pytest.raises(EvolutionEvidenceError, match="environment fingerprints differ"):
        recompute_evolution_comparison(store, project, case_id)


def test_recompute_comparison_uses_verifier_status_not_agent_text(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "comparison.db"))
    project, case_id = "project", "case"
    case = AgentEvolutionCase(
        evolution_case_id=case_id,
        project_id=project,
        source_id="source",
        baseline_revision_id="revision_baseline",
        candidate_revision_id="revision_candidate",
        evolution_changeset_id="changeset",
    )
    baseline = trial(project, case_id, "baseline", 1)
    candidate = trial(project, case_id, "candidate", 1)
    criterion = VerificationCriterion(name="fact", status="passed", detail="saved verifier fact")
    baseline_verification = EvolutionVerification(
        project_id=project, evolution_case_id=case_id, evolution_trial_id=baseline.evolution_trial_id,
        status="failed", criteria=[criterion], evidence_refs=["evidence:baseline"],
    )
    candidate_verification = EvolutionVerification(
        project_id=project, evolution_case_id=case_id, evolution_trial_id=candidate.evolution_trial_id,
        status="passed", criteria=[criterion], evidence_refs=["evidence:candidate"],
    )
    store.save_many([
        ("agent_evolution_case", case_id, project, case),
        ("evolution_trial", baseline.evolution_trial_id, project, baseline),
        ("evolution_trial", candidate.evolution_trial_id, project, candidate),
        ("evolution_verification", baseline_verification.evolution_verification_id, project, baseline_verification),
        ("evolution_verification", candidate_verification.evolution_verification_id, project, candidate_verification),
    ])
    result = recompute_evolution_comparison(store, project, case_id)
    assert result.status == "compared"
    assert "baseline-fail/candidate-pass" in result.conclusion
    assert result.evidence_ids == [
        baseline_verification.evolution_verification_id,
        candidate_verification.evolution_verification_id,
    ]


def test_recompute_comparison_rejects_duplicate_role_index(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "comparison.db"))
    project, case_id = "project", "case"
    case = AgentEvolutionCase(
        evolution_case_id=case_id,
        project_id=project,
        source_id="source",
        baseline_revision_id="revision_baseline",
        candidate_revision_id="revision_candidate",
        evolution_changeset_id="changeset",
    )
    baseline_a = trial(project, case_id, "baseline", 1)
    baseline_b = trial(project, case_id, "baseline", 1)
    candidate = trial(project, case_id, "candidate", 1)
    store.save_many([
        ("agent_evolution_case", case_id, project, case),
        ("evolution_trial", baseline_a.evolution_trial_id, project, baseline_a),
        ("evolution_trial", baseline_b.evolution_trial_id, project, baseline_b),
        ("evolution_trial", candidate.evolution_trial_id, project, candidate),
    ])
    with pytest.raises(EvolutionEvidenceError, match="ambiguous duplicate"):
        recompute_evolution_comparison(store, project, case_id)
