import json
from pathlib import Path

import pytest

from agentguard.cli import main
from agentguard.project_intelligence import (
    AgentManifest,
    CapabilityRecord,
    ProjectIntelligenceError,
    ProjectIntelligenceRegistration,
    ProjectIntelligenceRepository,
    RuntimeProfile,
)
from agentguard.store import Store


def registration(project_id: str = "generic-agent", baseline_version: str = "git:abc123") -> ProjectIntelligenceRegistration:
    capabilities = [
        CapabilityRecord(
            project_id=project_id,
            component_type="skill",
            name="task_planning",
            responsibility="Turn a user task into an executable plan.",
            boundary=["Ask for clarification when required information is missing."],
            status="observed",
        ),
        CapabilityRecord(
            project_id=project_id,
            component_type="skill",
            name="result_delivery",
            responsibility="Deliver a concise result to the user.",
            status="declared",
        ),
        CapabilityRecord(
            project_id=project_id,
            component_type="skill_pair",
            name="planning_and_delivery",
            responsibility="Coordinate planning with result delivery.",
            dependencies=["task_planning", "result_delivery"],
            status="declared",
        ),
        CapabilityRecord(
            project_id=project_id,
            component_type="tool",
            name="lookup_catalog",
            responsibility="Read catalog data for a user task.",
            status="declared",
        ),
    ]
    names = [capability.name for capability in capabilities]
    return ProjectIntelligenceRegistration(
        project_id=project_id,
        agent_manifest=AgentManifest(
            project_id=project_id,
            agent_name="Generic Local Agent",
            purpose="Complete structured user tasks with bounded tools.",
            source_kind="repository",
            source_ref="repo:generic-local-agent@abc123",
            available_components=names,
            capability_descriptions={name: f"Declared capability: {name}" for name in names},
        ),
        capabilities=capabilities,
        runtime_profile=RuntimeProfile(
            project_id=project_id,
            entrypoint="python -m generic_agent",
            runtime_kind="native_command",
            environment={"APP_ENV": "evaluation"},
            dependencies=["requirements.lock"],
            model_configuration={"provider": "approved-runtime-binding", "model": "configured-at-runtime"},
            execution_requirements=["isolated working directory", "reset before each trial"],
            source_ref="repo:generic-local-agent@abc123",
            trace_contract_ref="trace-contract:v1",
            reset_contract_ref="reset-contract:v1",
        ),
        baseline_version=baseline_version,
        initial_evaluation_history=["evaluation:initial-smoke"],
    )


def test_project_intelligence_registers_four_objects_and_is_queryable(tmp_path: Path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "agentguard.db")))

    first = repository.register(registration())
    loaded = repository.get("generic-agent")

    assert loaded == first
    assert loaded is not None
    assert loaded.status == "ready"
    assert loaded.agent_manifest.agent_name == "Generic Local Agent"
    assert {item.component_type for item in loaded.capability_registry} == {"skill", "skill_pair", "tool"}
    assert loaded.baseline_snapshot.baseline_version == "git:abc123"
    assert loaded.baseline_snapshot.snapshot_fingerprint


def test_project_intelligence_registration_is_idempotent_across_new_timestamps(tmp_path: Path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "agentguard.db")))

    first = repository.register(registration())
    second = repository.register(registration())

    assert second.intelligence_fingerprint == first.intelligence_fingerprint
    assert second.baseline_snapshot.snapshot_fingerprint == first.baseline_snapshot.snapshot_fingerprint


def test_project_intelligence_rejects_conflicting_immutable_baseline(tmp_path: Path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "agentguard.db")))
    repository.register(registration())

    with pytest.raises(ProjectIntelligenceError, match="different immutable baseline"):
        repository.register(registration(baseline_version="git:changed"))


def test_project_intelligence_rejects_invalid_pair_and_foreign_project(tmp_path: Path) -> None:
    invalid_pair = registration()
    invalid_pair = invalid_pair.model_copy(update={
        "capabilities": [
            capability.model_copy(update={"dependencies": ["task_planning"]})
            if capability.component_type == "skill_pair"
            else capability
            for capability in invalid_pair.capabilities
        ]
    })
    with pytest.raises(ProjectIntelligenceError, match="exactly two"):
        ProjectIntelligenceRepository(Store(str(tmp_path / "invalid-pair.db"))).register(invalid_pair)

    foreign = registration()
    foreign = foreign.model_copy(update={
        "runtime_profile": foreign.runtime_profile.model_copy(update={"project_id": "other-project"})
    })
    with pytest.raises(ProjectIntelligenceError, match="same project"):
        ProjectIntelligenceRepository(Store(str(tmp_path / "foreign-project.db"))).register(foreign)


def test_project_snapshot_history_produces_component_change_suggestions(tmp_path: Path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "snapshots.db")))
    repository.register(registration())
    candidate = registration().model_copy(update={
        "snapshot_version": "git:def456",
        "agent_manifest": registration().agent_manifest.model_copy(update={
            "source_ref": "repo:generic-local-agent@def456",
        }),
        "capabilities": [
            capability.model_copy(update={
                "responsibility": "Turn a user task into a revised executable plan."
            }) if capability.name == "task_planning" else capability
            for capability in registration().capabilities
        ],
        "runtime_profile": registration().runtime_profile.model_copy(update={
            "source_ref": "repo:generic-local-agent@def456",
        }),
    })

    result = repository.register_snapshot(candidate)
    statuses = {
        (item.component_type, item.component_name): item.status
        for item in result.diff.component_changes
    }

    assert result.snapshot.parent_snapshot_id == result.intelligence.snapshot_history[0].snapshot_id
    assert statuses[("skill", "task_planning")] == "changed"
    assert statuses[("skill", "result_delivery")] == "unchanged"
    assert statuses[("tool", "lookup_catalog")] == "unchanged"
    assert result.diff.runtime_changed is True
    assert len(result.intelligence.snapshot_history) == 2
    assert repository.register_snapshot(candidate).snapshot.snapshot_id == result.snapshot.snapshot_id


def test_project_intelligence_cli_register_and_get_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_path = tmp_path / "project-intelligence.json"
    input_path.write_text(
        json.dumps(registration().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    db_path = tmp_path / "agentguard.db"

    code = main([
        "--db", str(db_path), "--format", "json", "project", "register",
        "--project-id", "generic-agent", "--input", str(input_path),
    ])
    created = json.loads(capsys.readouterr().out)
    assert code == 0
    assert created["data"]["project_id"] == "generic-agent"

    code = main([
        "--db", str(db_path), "--format", "json", "project", "get", "generic-agent",
    ])
    fetched = json.loads(capsys.readouterr().out)
    assert code == 0
    assert fetched["data"]["baseline_snapshot"]["baseline_version"] == "git:abc123"


def test_cli_snapshot_and_optional_benchmark_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registration_path = tmp_path / "initial.json"
    registration_path.write_text(
        json.dumps(registration().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps({
            "benchmark": "custom-eval",
            "before": {"success": "70%"},
            "after": {"success": "75%"},
        }),
        encoding="utf-8",
    )
    snapshot_path = tmp_path / "candidate.json"
    snapshot_path.write_text(
        json.dumps(registration().model_copy(update={"snapshot_version": "git:def456"}).model_dump(mode="json")),
        encoding="utf-8",
    )
    db_path = tmp_path / "agentguard.db"

    assert main([
        "--db", str(db_path), "--format", "json", "project", "register",
        "--project-id", "generic-agent", "--input", str(registration_path),
        "--benchmark-result", str(benchmark_path),
    ]) == 0
    registered = json.loads(capsys.readouterr().out)
    assert len(registered["data"]["benchmark_evidence"]) == 1

    assert main([
        "--db", str(db_path), "--format", "json", "project", "snapshot",
        "--project-id", "generic-agent", "--input", str(snapshot_path),
    ]) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert len(snapshot["data"]["intelligence"]["snapshot_history"]) == 2
    assert snapshot["data"]["diff"]["component_changes"]

    assert main([
        "--db", str(db_path), "--format", "json", "benchmark", "list",
        "--project-id", "generic-agent",
    ]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"]["benchmark_evidence"][0]["benchmark_name"] == "custom-eval"
