from collections import defaultdict

from agentguard.domain import (
    ChangeSet,
    EvalPlan,
    Evidence,
    ExecutionResult,
    Finding,
    HarnessRun,
    ReleaseDecision,
    RunEvent,
    VerificationResult,
    WorkItem,
)
from agentguard.service import Service
from agentguard.stage1 import Stage1HarnessArtifact


REPRESENTATIVE_CASES = (
    "dev-workflow-normal",
    "dev-skill-normal",
    "dev-permission-severe",
)


def test_three_stage1_cases_run_selected_and_full_real_harness_chains(tmp_path):
    service = Service(str(tmp_path / "stage1-remediation2.db"))
    artifacts = [
        artifact
        for case_id in REPRESENTATIVE_CASES
        for artifact in service.run_stage1_harness_pair(case_id)
    ]

    assert len(artifacts) == 6
    assert all(isinstance(artifact, Stage1HarnessArtifact) for artifact in artifacts)
    grouped = defaultdict(dict)
    for artifact in artifacts:
        grouped[artifact.case_id][artifact.branch] = artifact

        run = service.store.get("harness_run", artifact.harness_run_id, HarnessRun)
        plan = service.store.get("eval_plan", artifact.eval_plan_id, EvalPlan)
        assert run is not None
        assert plan is not None
        assert run.candidate_version_id
        assert service.store.get("release_decision", artifact.release_decision_id, ReleaseDecision) is not None
        assert service.store.list("work_item", WorkItem, artifact.product_id)
        assert service.store.list("execution", ExecutionResult, artifact.product_id)
        assert service.store.list("verification", VerificationResult, artifact.product_id)
        assert service.store.list("evidence", Evidence, artifact.product_id)
        assert service.store.list("finding", Finding, artifact.product_id) is not None
        events = [
            event
            for event in service.store.list("run_event", RunEvent, artifact.product_id)
            if event.harness_run_id == artifact.harness_run_id
        ]
        assert [event.event_type for event in events] == [
            "RUN_CREATED",
            "PLAN_CREATED",
            "TRIALS_COMPLETED",
            "VERIFICATION_COMPLETED",
            *( ["FINDING_CREATED"] if artifact.release_status == "blocked" else [] ),
            "RELEASE_DECIDED",
            "RUN_RECORDED",
        ]
        assert artifact.execution_ids
        assert artifact.verification_ids
        assert artifact.evidence_ids

    assert set(grouped) == set(REPRESENTATIVE_CASES)
    for case_id, branches in grouped.items():
        selected = branches["selected"]
        full = branches["full_regression"]
        selected_run = service.store.get("harness_run", selected.harness_run_id, HarnessRun)
        full_run = service.store.get("harness_run", full.harness_run_id, HarnessRun)
        selected_changes = service.store.get("changeset", selected_run.changeset_id, ChangeSet)
        full_changes = service.store.get("changeset", full_run.changeset_id, ChangeSet)
        assert selected.product_id == full.product_id
        assert selected.candidate_ref == full.candidate_ref
        assert selected_changes.candidate_snapshot.fingerprint == full_changes.candidate_snapshot.fingerprint
        assert set(selected.selected_case_ids).issubset(set(full.selected_case_ids))

    assert grouped["dev-workflow-normal"]["selected"].selected_case_ids == ["eval_smoke"]
    assert grouped["dev-workflow-normal"]["full_regression"].selected_case_ids == [
        "eval_normal_write",
        "eval_security_no_secret_write",
        "eval_smoke",
    ]
    assert grouped["dev-skill-normal"]["selected"].selected_case_ids == ["eval_normal_write", "eval_smoke"]
    assert grouped["dev-permission-severe"]["selected"].selected_case_ids == [
        "eval_security_no_secret_write",
        "eval_smoke",
    ]
    assert grouped["dev-permission-severe"]["selected"].release_status == "blocked"
    assert grouped["dev-permission-severe"]["full_regression"].release_status == "blocked"
