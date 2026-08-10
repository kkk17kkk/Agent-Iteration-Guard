"""Skill Ablation implementation of the generic Evaluation Adapter contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .evaluation_adapters import AdapterContext, EvaluationAdapterLayer
from .evaluation_execution import EvaluationExecutionError
from .evaluation_planning import EvaluationChange, EvaluationPlan, EvaluationTarget
from .evaluation_request import EvaluationRequest
from .interaction_matrix import EvaluationMatrixArtifact
from .product_reporting import SkillAblationArtifact
from .semantic_reporting import ProductDefinition, ImmutableEvidenceBundle, build_skill_ablation_evidence_bundle


def build_skill_evaluation_target(
    artifact: SkillAblationArtifact,
    product_definition: ProductDefinition,
) -> EvaluationTarget:
    """Translate the persisted Skill contract into the generic target model."""

    contract = artifact.contract
    if artifact.project_name != contract.project_id:
        raise ValueError("Skill artifact project and contract project do not match.")
    if product_definition.component_type != "skill" or product_definition.component_name != contract.skill_name:
        raise ValueError("Skill product definition and contract component do not match.")
    return EvaluationTarget(
        target_id=contract.skill_contract_id,
        project_id=contract.project_id,
        component_type="skill",
        name=contract.skill_name,
        description=product_definition.description or contract.deliverable,
        product_responsibility=product_definition.product_responsibility,
        user_job=product_definition.user_job,
        expected_behavior=product_definition.expected_behavior or [contract.deliverable],
        quality_dimensions=product_definition.quality_dimensions,
        boundary=product_definition.boundary,
        definition_status=product_definition.definition_status,
        evidence_refs=product_definition.evidence_refs,
    )


def build_skill_ablation_change(
    artifact: SkillAblationArtifact,
    *,
    evaluation_name: str,
) -> EvaluationChange:
    """Translate Skill Ablation execution context into a generic change."""

    contract = artifact.contract
    return EvaluationChange(
        change_id=contract.skill_contract_id,
        project_id=contract.project_id,
        change_type="ablation",
        evaluation_type="skill_ablation",
        evaluation_name=evaluation_name,
        summary=f"Evaluate the product contribution of {contract.skill_name}.",
        baseline_ref=contract.skill_contract_id,
    )


def build_skill_evaluation_change(
    request: EvaluationRequest,
    *,
    evaluation_name: str,
) -> EvaluationChange:
    """Translate a persisted Skill request for the generic Planner path.

    Skill Ablation is intentionally limited to removal/replacement requests;
    other change types need a declared strategy rather than being silently
    mapped to an unrelated experiment.
    """

    if request.component_type != "skill":
        raise ValueError("Skill change translation requires component_type=skill.")
    if request.change_type not in {"remove", "replace"}:
        raise ValueError(
            "Skill evaluation currently supports remove and replace through the Skill Ablation strategy."
        )
    return EvaluationChange(
        change_id=request.request_id,
        project_id=request.project_id,
        change_type="ablation",
        evaluation_type="skill_ablation",
        evaluation_name=evaluation_name,
        summary=f"Evaluate the product contribution of {request.component_name}.",
        baseline_ref=request.baseline_version,
        candidate_ref=request.candidate_version,
        scenario_suite=request.scenario_suite,
    )


def skill_ablation_experiment_ids_by_condition(plan: EvaluationPlan) -> dict[str, str]:
    """Map technical persisted conditions to generic plan experiments at the adapter boundary."""

    if plan.component_type != "skill" or plan.change_type != "ablation":
        raise ValueError("Skill Ablation condition mapping requires a skill ablation Evaluation Plan.")
    baseline_id = plan.experiment_for_kind("baseline").experiment_id
    removal_id = plan.experiment_for_kind("removal").experiment_id
    replacement_id = plan.experiment_for_kind("equivalence").experiment_id
    return {
        "baseline": baseline_id,
        "removal": removal_id,
        "replacement": replacement_id,
        "enabled": baseline_id,
        "disabled": removal_id,
    }


class SkillAblationEvaluationAdapter:
    """Normalize persisted Skill Ablation artifacts into immutable evidence.

    The adapter is intentionally narrow.  Product interpretation, report
    assembly, and rendering remain downstream responsibilities.
    """

    evaluation_type = "skill_ablation"

    def adapt(
        self,
        artifact: Sequence[SkillAblationArtifact] | Mapping[str, object] | object,
        *,
        context: AdapterContext,
    ) -> ImmutableEvidenceBundle:
        if isinstance(artifact, (Mapping, EvaluationMatrixArtifact)):
            from .evaluation_matrix_adapter import EvaluationMatrixEvidenceAdapter

            return EvaluationMatrixEvidenceAdapter(
                self.evaluation_type,
                ("baseline", "removal", "replacement"),
            ).adapt(artifact, context=context)
        if not isinstance(artifact, Sequence) or isinstance(artifact, (str, bytes)):
            raise TypeError("Skill Ablation adapter expects a sequence of persisted artifacts.")
        artifacts = list(artifact)
        if not artifacts:
            raise ValueError("Skill Ablation adapter requires at least one artifact.")
        if any(not isinstance(item, SkillAblationArtifact) for item in artifacts):
            raise TypeError("Skill Ablation adapter received an unsupported artifact type.")
        if any(item.project_name != context.project_id for item in artifacts):
            raise ValueError("Skill Ablation artifacts must belong to the adapter context project.")
        if context.component_name is not None and any(
            item.contract.skill_name != context.component_name for item in artifacts
        ):
            raise ValueError("Skill Ablation artifacts must match the requested component.")
        scenario_ids_by_trial_ref = _scenario_ids_by_trial_ref(artifacts, context.scenario_ids_by_trial_ref)
        bundle, _ = build_skill_ablation_evidence_bundle(
            context.project_id,
            artifacts,
            evaluation_name=context.evaluation_name,
            evaluation_request_id=context.evaluation_request_id,
            baseline_version=context.baseline_version,
            candidate_version=context.candidate_version,
            evaluation_plan_id=context.evaluation_plan_id,
            experiment_ids_by_condition=context.experiment_ids_by_condition,
            scenario_ids_by_trial_ref=scenario_ids_by_trial_ref,
        )
        return bundle


def build_default_evaluation_adapter_layer() -> EvaluationAdapterLayer:
    """Return the registry used by current orchestration entry points."""

    layer = EvaluationAdapterLayer()
    layer.register(SkillAblationEvaluationAdapter())
    return layer


def _scenario_ids_by_trial_ref(
    artifacts: list[SkillAblationArtifact],
    explicit: dict[str, str],
) -> dict[str, str]:
    artifact_trial_refs = {artifact.evidence.trial_ref for artifact in artifacts}
    extra = sorted(set(explicit) - artifact_trial_refs)
    if extra:
        raise EvaluationExecutionError(
            f"Explicit scenario mapping contains trials absent from the artifact set: {extra}."
        )
    intrinsic = {
        artifact.evidence.trial_ref: artifact.evidence.scenario_id
        for artifact in artifacts
        if artifact.evidence.scenario_id is not None
    }
    if explicit and any(
        trial_ref in intrinsic and intrinsic[trial_ref] != scenario_id
        for trial_ref, scenario_id in explicit.items()
    ):
        raise EvaluationExecutionError("Explicit and artifact-persisted scenario mappings disagree.")
    return {**intrinsic, **explicit}


__all__ = [
    "SkillAblationEvaluationAdapter",
    "build_default_evaluation_adapter_layer",
    "build_skill_ablation_change",
    "build_skill_evaluation_change",
    "build_skill_evaluation_target",
    "skill_ablation_experiment_ids_by_condition",
]
