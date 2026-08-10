"""Shared target execution orchestration for API and CLI callers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .domain import ProviderBinding
from .change_adapters import build_v1_evaluation_adapter_layer
from .evaluation_adapters import AdapterContext
from .evaluation_failures import EvidenceIncompleteError
from .evaluation_execution_config import EvaluationExecutionConfiguration
from .evaluation_planning import EvaluationPlan
from .interaction_matrix import (
    PAIR_INTERACTION_CONDITIONS,
    EvaluationMatrixArtifact,
    execute_evaluation_matrix,
)
from .interaction_runner import ManifestInteractionTrialRunner, SubprocessInteractionOracle
from .evaluation_request import EvaluationRequest
from .product_evaluation_analyst import ProductAnalystInput, ProductEvaluationAnalyst, ProductAnalystResult
from .product_evaluation_report import ProductEvaluationReport, assemble_product_evaluation_report
from .semantic_reporting import ProductDefinition
from .project_intelligence import ProjectIntelligence
from .scenario_contracts import check_evaluation_plan_readiness
from .skill_ablation import SKILL_ABLATION_CONDITIONS, execute_skill_ablation_matrix
from .skill_ablation_adapter import skill_ablation_experiment_ids_by_condition
from .skill_pair_evaluation import skill_pair_experiment_ids_by_condition
from .target_runtime import TargetRuntimeAdapter


EvaluationRunArtifact = EvaluationMatrixArtifact


@dataclass(frozen=True)
class EvaluationExecutionInputs:
    manifest_path: Path
    cache_root: Path
    run_root: Path
    oracle_command: tuple[str, ...]
    oracle_id: str
    oracle_type: str
    oracle_version: str
    oracle_cwd: Path | None
    target_provider_binding_id: str | None


def resolve_evaluation_execution_inputs(
    plan: EvaluationPlan,
    config: EvaluationExecutionConfiguration,
    *,
    evaluation_id: str,
) -> EvaluationExecutionInputs:
    """Bind one reviewed server-owned runtime contract to a frozen Plan."""

    if config.project_id != plan.project_id:
        raise ValueError("Evaluation Execution Configuration belongs to a different project.")
    if plan.evaluation_scope is None:
        raise ValueError("Evaluation execution requires a frozen Evaluation Scope.")
    if config.snapshot_version and config.snapshot_version != plan.evaluation_scope.candidate_version:
        raise ValueError("Project runtime configuration is stale for this Evaluation Plan snapshot; review the runtime again.")
    if config.target_provider_binding_id != plan.evaluation_scope.target_provider_binding_id:
        raise ValueError("Target ProviderBinding does not match the frozen Evaluation Scope.")
    return EvaluationExecutionInputs(
        manifest_path=Path(config.manifest_path),
        cache_root=Path(config.cache_root),
        run_root=Path(config.run_root_parent) / evaluation_id,
        oracle_command=tuple(config.oracle_command),
        oracle_id=config.oracle_id,
        oracle_type=config.oracle_type,
        oracle_version=config.oracle_version,
        oracle_cwd=Path(config.oracle_cwd) if config.oracle_cwd else None,
        target_provider_binding_id=config.target_provider_binding_id,
    )


def evaluation_condition_kinds(plan: EvaluationPlan) -> tuple[str, ...]:
    """Return the condition set owned by the registered execution strategy."""

    if plan.evaluation_type == "skill_ablation":
        return tuple(SKILL_ABLATION_CONDITIONS)
    if plan.evaluation_type == "skill_pair_evaluation":
        return PAIR_INTERACTION_CONDITIONS
    raise ValueError(
        f"Evaluation execution is deferred for evaluation_type={plan.evaluation_type}; no condition strategy is registered."
    )


def planned_trial_count(plan: EvaluationPlan) -> int:
    """Calculate the frozen matrix size without leaking condition counts to API/CLI."""

    return sum(item.repetition_count for item in plan.scenarios) * len(evaluation_condition_kinds(plan))


def execute_evaluation_run(
    plan: EvaluationPlan,
    intelligence: ProjectIntelligence,
    *,
    manifest_path: Path,
    cache_root: Path,
    fixture_root: Path | None,
    run_root: Path,
    evaluation_id: str,
    oracle_command: tuple[str, ...],
    oracle_id: str,
    oracle_type: str = "rule_based",
    oracle_version: str = "1.0",
    oracle_cwd: Path | None = None,
    target_binding: ProviderBinding | None = None,
) -> EvaluationRunArtifact:
    """Run the declared target and independent Oracle for one frozen Plan."""

    if plan.project_id != intelligence.project_id:
        raise ValueError("Evaluation Plan and Project Intelligence must belong to the same project.")
    if plan.evaluation_scope is None:
        raise ValueError("Evaluation execution requires a frozen Evaluation Scope.")
    readiness = check_evaluation_plan_readiness(
        plan,
        intelligence.runtime_profile.fixture_catalog,
        fixture_root=fixture_root,
    )
    target = TargetRuntimeAdapter(manifest_path, cache_root)
    oracle = SubprocessInteractionOracle(
        oracle_command,
        verifier_id=oracle_id,
        oracle_type=oracle_type,
        oracle_version=oracle_version,
        working_directory=oracle_cwd,
    )
    runner = ManifestInteractionTrialRunner(
        target,
        fixture_catalog=intelligence.runtime_profile.fixture_catalog,
        fixture_root=fixture_root,
        oracle=oracle,
        binding=target_binding,
        timeout_seconds=plan.scenario_suite.trial_timeout_seconds if plan.scenario_suite else None,
    )
    if plan.evaluation_type == "skill_pair_evaluation":
        return execute_evaluation_matrix(
            plan,
            evaluation_name=plan.evaluation_name,
            evaluation_id=evaluation_id,
            readiness=readiness,
            runner=runner,
            condition_kinds=PAIR_INTERACTION_CONDITIONS,
            run_root=run_root,
        )
    if plan.evaluation_type == "skill_ablation":
        return execute_skill_ablation_matrix(
            plan,
            evaluation_id=evaluation_id,
            readiness=readiness,
            runner=runner,
            run_root=run_root,
        )
    raise ValueError(
        f"Evaluation execution is deferred for evaluation_type={plan.evaluation_type}; no target runner is registered."
    )


def adapt_evaluation_run_evidence(
    plan: EvaluationPlan,
    request: EvaluationRequest,
    *,
    run_id: str,
    scope_id: str,
    artifact: dict[str, object],
):
    """Adapt one completed run through the registered immutable evidence layer."""

    if plan.evaluation_scope is None or plan.evaluation_scope.scope_id != scope_id:
        raise ValueError("Evaluation Run scope does not match the immutable Evaluation Plan scope.")
    if plan.evaluation_type == "skill_ablation":
        experiment_ids = skill_ablation_experiment_ids_by_condition(plan)
    elif plan.evaluation_type == "skill_pair_evaluation":
        experiment_ids = skill_pair_experiment_ids_by_condition(plan)
    else:
        raise ValueError(f"Evidence adaptation is deferred for evaluation_type={plan.evaluation_type}.")
    try:
        return build_v1_evaluation_adapter_layer().adapt(
            plan.evaluation_type,
            artifact,
            context=AdapterContext(
                project_id=plan.project_id,
                evaluation_name=plan.evaluation_name,
                evaluation_type=plan.evaluation_type,
                component_name=plan.component_name,
                source_ref=f"evaluation-run:{run_id}",
                evaluation_request_id=request.request_id,
                baseline_version=request.baseline_version,
                candidate_version=request.candidate_version,
                scope_id=scope_id,
                evaluation_plan_id=plan.plan_id,
                experiment_ids_by_condition=experiment_ids,
            ),
        )
    except (TypeError, ValueError) as error:
        raise EvidenceIncompleteError("Evaluation artifact failed immutable evidence admission.") from error


def build_product_evaluation_report(
    plan: EvaluationPlan,
    product_definition: ProductDefinition,
    evidence,
    *,
    binding: ProviderBinding,
    provider,
    forbidden_tokens: set[str] | None = None,
) -> ProductEvaluationReport:
    """Run the bounded Analyst and bind its interpretation to immutable evidence."""

    if product_definition.component_type != plan.component_type or product_definition.component_name != plan.component_name:
        raise ValueError("Product definition does not match the Evaluation Plan target.")
    analyst_input = ProductAnalystInput(
        project_id=plan.project_id,
        evaluation_name=plan.evaluation_name,
        evaluation_type=plan.evaluation_type,
        evaluation_question=plan.comparison_question,
        hypothesis=plan.hypothesis,
        product_definition=product_definition,
        evidence=evidence,
        evaluation_plan=plan,
    )
    analyst_result: ProductAnalystResult = ProductEvaluationAnalyst().analyze(
        analyst_input,
        provider=provider,
        binding=binding,
        forbidden_tokens=forbidden_tokens or set(),
    )
    return assemble_product_evaluation_report(analyst_input, analyst_result)


__all__ = [
    "EvaluationRunArtifact",
    "adapt_evaluation_run_evidence",
    "build_product_evaluation_report",
    "evaluation_condition_kinds",
    "execute_evaluation_run",
    "planned_trial_count",
    "resolve_evaluation_execution_inputs",
]
