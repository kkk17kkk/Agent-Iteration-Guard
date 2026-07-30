import pytest

from agentguard.service import Service
from agentguard.stage1 import assert_stage2_launch_allowed, gate_stage1_acceptance


def test_stage1_acceptance_gate_remains_blocked_without_hard_evidence(tmp_path):
    service = Service(str(tmp_path / "stage1.db"))
    batch = service.run_stage1_harness_corpus(["dev-workflow-normal", "dev-skill-normal", "dev-permission-severe"])
    service.report_stage1_harness_corpus(batch.batch_id)
    service.gate_stage1_harness_corpus(batch.batch_id)

    gate = gate_stage1_acceptance(service.store, batch.batch_id, tmp_path / "artifacts")

    assert gate.status == "BLOCKED"
    assert any(item.criterion == "fault_injection_matrix" and item.status == "missing" for item in gate.criteria)


def test_stage2_launch_lock_rejects_missing_or_blocked_stage1_gate(tmp_path):
    service = Service(str(tmp_path / "stage1.db"))

    with pytest.raises(ValueError, match="Stage 2 is locked"):
        assert_stage2_launch_allowed(service.store, "missing-batch")
