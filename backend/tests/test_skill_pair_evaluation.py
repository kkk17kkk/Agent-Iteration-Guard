from agentguard.evaluation_request import EvaluationRequest
from agentguard.project_intelligence import AgentManifest, BaselineSnapshot, CapabilityRecord, ProjectIntelligence, RuntimeProfile
from agentguard.semantic_reporting import ProductDefinition
from agentguard.skill_pair_evaluation import (
    build_skill_pair_evaluation_change,
    build_skill_pair_evaluation_target,
)


def _intelligence() -> ProjectIntelligence:
    project_id = "generic-agent"
    capabilities = [
        CapabilityRecord(project_id=project_id, component_type="skill", name="task_planning", responsibility="Plan tasks."),
        CapabilityRecord(project_id=project_id, component_type="skill", name="result_delivery", responsibility="Deliver results."),
        CapabilityRecord(
            project_id=project_id,
            component_type="skill_pair",
            capability_id="capability-planning-and-delivery",
            name="planning_and_delivery",
            responsibility="Coordinate planning and delivery.",
            dependencies=["task_planning", "result_delivery"],
        ),
    ]
    manifest = AgentManifest(
        project_id=project_id,
        agent_name="Generic Agent",
        purpose="Complete tasks.",
        source_kind="repository",
        source_ref="repo:generic@baseline",
        available_components=[item.name for item in capabilities],
        capability_descriptions={item.name: item.responsibility for item in capabilities},
    )
    runtime = RuntimeProfile(
        project_id=project_id,
        entrypoint="python -m generic_agent",
        runtime_kind="native_command",
        execution_requirements=["reset"],
        source_ref="repo:generic@baseline",
    )
    baseline = BaselineSnapshot(
        snapshot_id="baseline-1",
        project_id=project_id,
        baseline_version="git:baseline",
        agent_manifest_id="manifest-1",
        agent_manifest_fingerprint="0" * 64,
        capability_snapshot=capabilities,
        capability_snapshot_fingerprint="1" * 64,
        runtime_snapshot=runtime,
        runtime_snapshot_fingerprint="2" * 64,
        snapshot_fingerprint="3" * 64,
    )
    return ProjectIntelligence(
        project_id=project_id,
        status="ready",
        agent_manifest=manifest,
        capability_registry=capabilities,
        runtime_profile=runtime,
        baseline_snapshot=baseline,
        intelligence_fingerprint="4" * 64,
    )


def test_skill_pair_target_resolves_registered_members_and_change() -> None:
    request = EvaluationRequest(
        request_id="evaluation-request-pair",
        project_id="generic-agent",
        component_type="skill_pair",
        component_name="planning_and_delivery",
        change_type="modify",
        candidate_version="git:candidate",
        baseline_version="git:baseline",
    )
    definition = ProductDefinition(
        component_type="skill_pair",
        component_name="planning_and_delivery",
        description="Coordinate planning and delivery.",
        product_responsibility="Complete user tasks end to end.",
        user_job="Get a usable result.",
    )

    target = build_skill_pair_evaluation_target(_intelligence(), request.component_name, definition)
    change = build_skill_pair_evaluation_change(request, evaluation_name="Pair Evaluation")

    assert target.component_members == ["task_planning", "result_delivery"]
    assert target.component_type == "skill_pair"
    assert change.change_id == request.request_id
    assert change.change_type == "interaction"
    assert change.evaluation_type == "skill_pair_evaluation"


def test_skill_pair_target_resolves_temporary_members_without_a_pair_registry_record() -> None:
    definition = ProductDefinition(
        component_type="skill_pair",
        component_name="result_delivery__task_planning",
        description="Evaluate the discovered pair.",
        product_responsibility="Complete a user task.",
        user_job="Receive a usable result.",
    )

    target = build_skill_pair_evaluation_target(
        _intelligence(),
        "result_delivery__task_planning",
        definition,
        pair_members=["task_planning", "result_delivery"],
    )

    assert target.target_id.startswith("temporary_pair_")
    assert target.component_members == ["task_planning", "result_delivery"]
