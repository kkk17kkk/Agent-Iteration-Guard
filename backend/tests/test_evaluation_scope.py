from __future__ import annotations

import pytest

from agentguard.domain import ProviderBinding
from agentguard.evaluation_request import EvaluationRequest
from agentguard.evaluation_scope import EvaluationScopeError, freeze_evaluation_scope
from agentguard.project_intelligence import (
    AgentManifest,
    CapabilityRecord,
    ProjectIntelligenceRegistration,
    ProjectIntelligenceRepository,
    RuntimeProfile,
)
from agentguard.store import Store


def _registration(version: str, *, snapshot: bool = False) -> ProjectIntelligenceRegistration:
    project_id = "scope-agent"
    capability = CapabilityRecord(
        project_id=project_id,
        component_type="skill",
        name="planning",
        responsibility="Plan the task.",
    )
    return ProjectIntelligenceRegistration(
        project_id=project_id,
        agent_manifest=AgentManifest(
            project_id=project_id,
            agent_name="Scope Agent",
            purpose="Complete bounded tasks.",
            source_kind="repository",
            source_ref=f"repo:scope@{version}",
            available_components=["planning"],
            capability_descriptions={"planning": "Plan the task."},
        ),
        capabilities=[capability],
        runtime_profile=RuntimeProfile(
            project_id=project_id,
            entrypoint="python -m scope_agent",
            runtime_kind="native_command",
            execution_requirements=["isolated working directory"],
            source_ref=f"repo:scope@{version}",
        ),
        baseline_version="baseline",
        snapshot_version=version if snapshot else None,
    )


def _binding() -> ProviderBinding:
    return ProviderBinding(
        project_id="scope-agent",
        role="sut_native",
        provider="vllm",
        model="scope-model",
        expected_environment_variable="SCOPE_KEY",
        credential_source_ref="env:SCOPE_KEY",
        batch_budget_usd=0.20,
        timeout_seconds=120,
        allowed_hosts=["localhost"],
        data_retention_policy="non-secret metadata only",
    )


def _request() -> EvaluationRequest:
    return EvaluationRequest(
        project_id="scope-agent",
        component_type="skill",
        component_name="planning",
        change_type="modify",
        candidate_version="candidate",
        baseline_version="baseline",
    )


def test_scope_freezes_runtime_provider_fixture_and_budget_identity(tmp_path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "scope.db")))
    repository.register(_registration("baseline"))
    repository.register_snapshot(_registration("candidate", snapshot=True))
    intelligence = repository.get("scope-agent")
    assert intelligence is not None

    scope = freeze_evaluation_scope(_request(), intelligence, _binding(), planned_trial_count=9)

    assert len(scope.scope_id) == 64
    assert scope.baseline_version == "baseline"
    assert scope.candidate_version == "candidate"
    assert scope.planned_trial_count == 9
    assert scope.side_effect_policy == "isolated_read"
    assert "SCOPE_KEY" not in scope.model_dump_json()


def test_scope_refuses_unregistered_candidate_runtime(tmp_path) -> None:
    intelligence = ProjectIntelligenceRepository(Store(str(tmp_path / "scope.db"))).register(_registration("baseline"))

    with pytest.raises(EvaluationScopeError, match="registered baseline and candidate snapshots"):
        freeze_evaluation_scope(_request(), intelligence, _binding(), planned_trial_count=9)
