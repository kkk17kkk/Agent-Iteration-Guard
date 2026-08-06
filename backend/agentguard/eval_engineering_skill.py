"""Eval Engineering design assistant for the first Planner strategy.

This module is deliberately outside the generic planning contracts. It owns
the experiment-selection policy for an ablation change and can later be
replaced or extended by another Eval Engineering design assistant without
changing Target, Plan, Adapter, or Analyst schemas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .evaluation_planning import (
    EvaluationChange,
    EvaluationDimensionPlan,
    EvaluationExperiment,
    EvaluationPlanDesign,
    EvaluationPlanningAssistant,
    EvaluationTarget,
    PlannerStrategyError,
)
from .evaluation_scenario_generator import (
    EvaluationEvidenceRequirementsGenerator,
    EvaluationScenarioGenerator,
)
from .interaction_evaluation import InteractionRelationshipProfile, validate_scenario_categories
from .evolution_types import ChangeType, ComponentType
class EvalEngineeringDesignAssistant:
    """Select experiments and define cases/criteria from the planning contract."""

    def design(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator: EvaluationScenarioGenerator,
        evidence_requirements_generator: EvaluationEvidenceRequirementsGenerator,
    ) -> EvaluationPlanDesign:
        if change.change_type != "ablation":
            raise PlannerStrategyError(
                f"Eval Engineering ablation design cannot handle {change.change_type}."
            )
        scenarios = scenario_generator.generate(target, change)
        evidence_requirements = evidence_requirements_generator.generate(target, change, scenarios)
        if len(scenarios) < 3 or len(scenarios) > 5:
            raise PlannerStrategyError("Evaluation Scenario Generator must return between 3 and 5 scenarios.")
        scenario_ids = {scenario.scenario_id for scenario in scenarios}
        if {item.scenario_id for item in evidence_requirements} != scenario_ids:
            raise PlannerStrategyError("Evidence Requirements Generator must cover every generated scenario exactly once.")
        dimensions = _default_dimensions()
        all_dimensions = [item.dimension for item in dimensions]
        label = target.component_type.title()
        experiments = [
            EvaluationExperiment(
                experiment_id=_experiment_id(target, change, "baseline"),
                experiment_kind="baseline",
                name="Full Capability Baseline",
                purpose=f"验证完整 Agent 在目标场景下的理想能力，为 {target.name} 建立产品质量基线。",
                design="保留目标能力，在声明的用户任务中运行完整 Agent。",
                control_group="无变更的完整 Agent 能力，作为后续比较的产品基线。",
                comparison="这个能力正常工作时，用户应该得到什么结果？",
                dimensions=all_dimensions,
                success_criteria=["四个维度均有可复核证据", "交付物满足产品定义"],
            ),
            EvaluationExperiment(
                experiment_id=_experiment_id(target, change, "removal"),
                experiment_kind="removal",
                name=f"{label} Removal",
                purpose=f"验证 {target.name} 是否是实现产品目标的必要组成，并识别移除后的能力损失。",
                design="移除目标能力，保留相同任务、环境和产品约束，与完整能力基线进行对照。",
                control_group="Full Capability Baseline",
                comparison="没有目标能力时，哪些维度和用户结果发生变化？",
                dimensions=all_dimensions,
                success_criteria=["明确记录保留能力与损失能力", "不把基础输出误判为完整产品能力"],
            ),
            EvaluationExperiment(
                experiment_id=_experiment_id(target, change, "equivalence"),
                experiment_kind="equivalence",
                name="Capability Equivalence",
                purpose=f"验证候选实现是否能够保持 {target.name} 提供的核心产品价值。",
                design="使用候选实现完成相同任务，并与完整能力基线按四个维度逐项比较。",
                control_group="Full Capability Baseline",
                comparison="候选实现是否同时保持激活、处理流程、交付质量和边界控制？",
                dimensions=all_dimensions,
                success_criteria=["候选实现不仅产生输出，还保持核心约束与交付质量"],
            ),
        ]
        if self.needs_interaction(target, change):
            experiments.append(
                EvaluationExperiment(
                    experiment_id=_experiment_id(target, change, "interaction"),
                    experiment_kind="interaction",
                    name=f"{label} Interaction",
                    purpose=f"验证 {target.name} 与相关能力组合是否产生额外产品价值或新的风险。",
                    design="分别运行目标能力、相关能力和两者组合，保持任务与环境一致。",
                    control_group="目标能力单独运行与相关能力单独运行的结果。",
                    comparison="组合能力在四个维度上是否产生增益、重叠或冲突？",
                    dimensions=all_dimensions,
                    success_criteria=["组合效果有独立证据支持", "新增价值不以新增越界风险为代价"],
                )
            )
        return EvaluationPlanDesign(
            rationale=f"判断 {target.name} 是否真正改善用户获得产品结果的过程，而不只验证内部执行是否发生。",
            hypothesis=(
                f"如果 {target.name} 被移除或替换，Agent 可能仍能产生基础输出，"
                "但在约束执行、交付质量或边界控制上出现能力损失。"
            ),
            dimensions=dimensions,
            experiments=experiments,
            comparison_question="不同实现是否改变用户实际得到的产品能力，而不只是改变内部执行路径？",
            scenarios=scenarios,
            evidence_requirements=evidence_requirements,
            overall_success_criteria=[
                "每个实验覆盖 Trigger、Execution、Delivery、Boundary 四个维度。",
                "成功结论同时有机器证据和产品定义支持。",
                "基础设施失败单独标记，不解释为 Agent 产品能力失败。",
            ],
        )

    @staticmethod
    def needs_interaction(target: EvaluationTarget, change: EvaluationChange) -> bool:
        """Decide from declared relationships rather than a caller flag."""

        return bool(change.related_target_ids)


@dataclass(frozen=True)
class EvalEngineeringAblationStrategy:
    """Registry strategy for the migrated ablation path."""

    component_type: ComponentType = "skill"
    change_type: ChangeType = "ablation"
    assistant: EvaluationPlanningAssistant = EvalEngineeringDesignAssistant()

    def design(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator: EvaluationScenarioGenerator,
        evidence_requirements_generator: EvaluationEvidenceRequirementsGenerator,
    ) -> EvaluationPlanDesign:
        return self.assistant.design(
            target,
            change,
            scenario_generator=scenario_generator,
            evidence_requirements_generator=evidence_requirements_generator,
        )


@dataclass(frozen=True)
class EvalEngineeringSkillPairStrategy:
    """Design a component-neutral interaction evaluation for two capabilities."""

    component_type: ComponentType = "skill_pair"
    change_type: ChangeType = "interaction"

    def design(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator: EvaluationScenarioGenerator,
        evidence_requirements_generator: EvaluationEvidenceRequirementsGenerator,
    ) -> EvaluationPlanDesign:
        if len(target.component_members) != 2 or len(set(target.component_members)) != 2:
            raise PlannerStrategyError(
                "Skill Pair Planner requires exactly two registered component members."
            )
        relationship, scenarios = self._generate_scenarios_with_hypothesis(
            target, change, scenario_generator=scenario_generator
        )
        dimension_plans = _interaction_dimensions()
        evidence_requirements = evidence_requirements_generator.generate(
            target,
            change,
            scenarios,
            dimensions=[item.dimension for item in dimension_plans],
        )
        scenario_ids = {scenario.scenario_id for scenario in scenarios}
        if {item.scenario_id for item in evidence_requirements} != scenario_ids:
            raise PlannerStrategyError("Interaction evidence requirements must cover every generated scenario exactly once.")
        member_a, member_b = target.component_members
        dimension_names = [item.dimension for item in dimension_plans]
        experiments = [
            EvaluationExperiment(
                experiment_id=_experiment_id(target, change, "pair_a_only"),
                experiment_kind="pair_a_only",
                name=f"{member_a} only",
                purpose=f"Measure the product behavior with only {member_a} enabled.",
                design=f"Run the declared scenarios with {member_a} enabled and {member_b} disabled.",
                control_group="The same scenarios and runtime with the other pair member disabled.",
                comparison=f"What does {member_a} contribute without {member_b}?",
                dimensions=dimension_names,
                success_criteria=["Record trigger, contribution, coordination, conflict, reliability, and cost evidence."],
            ),
            EvaluationExperiment(
                experiment_id=_experiment_id(target, change, "pair_b_only"),
                experiment_kind="pair_b_only",
                name=f"{member_b} only",
                purpose=f"Measure the product behavior with only {member_b} enabled.",
                design=f"Run the declared scenarios with {member_b} enabled and {member_a} disabled.",
                control_group="The same scenarios and runtime with the first pair member disabled.",
                comparison=f"What does {member_b} contribute without {member_a}?",
                dimensions=dimension_names,
                success_criteria=["Record trigger, contribution, coordination, conflict, reliability, and cost evidence."],
            ),
            EvaluationExperiment(
                experiment_id=_experiment_id(target, change, "pair_combined"),
                experiment_kind="pair_combined",
                name=f"{member_a} + {member_b}",
                purpose="Measure whether the pair creates additional value or interaction risk.",
                design=f"Run the same scenarios with both {member_a} and {member_b} enabled.",
                control_group=f"Compare against the two single-member conditions: {member_a} only and {member_b} only.",
                comparison="Does the combined condition add value, conflict, or risk beyond the single-member conditions?",
                dimensions=dimension_names,
                success_criteria=["Only claim synergy or conflict when the three conditions are independently evidenced."],
            ),
        ]
        return EvaluationPlanDesign(
            rationale=f"Evaluate the observed interaction between {member_a} and {member_b} across a relationship-driven scenario matrix without inferring untested attribution.",
            hypothesis=f"The combination of {member_a} and {member_b} may add product value, coordinate through information exchange, or introduce conflict relative to either member alone.",
            dimensions=dimension_plans,
            experiments=experiments,
            comparison_question="Does the interaction between the two capabilities create additional product value, coordination, conflict, or unjustified cost across the selected scenarios?",
            scenarios=scenarios,
            evidence_requirements=evidence_requirements,
            overall_success_criteria=[
                "Compare A-only, B-only, and combined results for every planned user scenario.",
                "Separate capability contribution from synergy; simple sequential execution is not synergy evidence.",
                "Report coordination, conflict/interference, reliability, latency, token, and cost evidence separately.",
            ],
            interaction_hypothesis=relationship,
        )

    def generate_scenarios(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator: EvaluationScenarioGenerator,
    ):
        """Let Eval Engineering classify the relationship and choose the matrix."""

        _, scenarios = self._generate_scenarios_with_hypothesis(
            target, change, scenario_generator=scenario_generator
        )
        return scenarios

    def _generate_scenarios_with_hypothesis(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator: EvaluationScenarioGenerator,
    ) -> tuple[InteractionRelationshipProfile, list]:
        """Classify the pair for scenario selection, never as a result verdict."""

        analyze = getattr(scenario_generator, "analyze_pair_relationship", None)
        generate_pair = getattr(scenario_generator, "generate_pair_scenarios", None)
        if not callable(analyze) or not callable(generate_pair):
            raise PlannerStrategyError(
                "Skill Pair planning requires a Pair-aware Scenario Generator with relationship analysis."
            )
        relationship: InteractionRelationshipProfile = analyze(target, change)
        scenarios = generate_pair(target, change, relationship=relationship)
        if not 3 <= len(scenarios) <= 5:
            raise PlannerStrategyError("Interaction Scenario Generator must return between 3 and 5 scenarios.")
        try:
            validate_scenario_categories(
                relationship.relationship,
                [scenario.category for scenario in scenarios],
            )
        except ValueError as error:
            raise PlannerStrategyError(str(error)) from error
        if any(scenario.expected_behavior is None for scenario in scenarios):
            raise PlannerStrategyError(
                "Interaction scenarios must declare expected behavior for A only, B only, and A+B."
            )
        return relationship, scenarios


def _default_dimensions() -> list[EvaluationDimensionPlan]:
    return [
        EvaluationDimensionPlan(
            dimension="trigger",
            question="是否在正确用户场景激活目标能力？",
            success_criteria=["目标任务进入声明的能力路径", "不在无关场景误触发"],
            evidence_to_collect=["激活事件", "请求场景", "能力路径识别"],
        ),
        EvaluationDimensionPlan(
            dimension="execution",
            question="激活后是否完成设计的处理流程？",
            success_criteria=["完成声明的处理步骤", "执行过程遵守能力约束"],
            evidence_to_collect=["过程事件", "工具或 provider 调用", "过程错误"],
        ),
        EvaluationDimensionPlan(
            dimension="delivery",
            question="最终交付是否达到产品要求？",
            success_criteria=["输出满足结构要求", "输出满足用户约束", "结果可直接使用"],
            evidence_to_collect=["最终交付物", "交付物结构", "约束检查结果"],
        ),
        EvaluationDimensionPlan(
            dimension="boundary",
            question="是否超出能力范围或产生产品风险？",
            success_criteria=["只执行声明范围内行为", "不产生未声明副作用", "越界时可观察并阻断"],
            evidence_to_collect=["状态差异", "边界事件", "副作用记录"],
        ),
    ]


def _interaction_dimensions() -> list[EvaluationDimensionPlan]:
    """Pair-specific dimensions; Trigger remains the activation gate."""

    return [
        EvaluationDimensionPlan(
            dimension="trigger",
            question="Are both capabilities activated for the right user need and in the right order?",
            success_criteria=["The relevant capability path is selected", "The second capability is not activated without a product reason"],
            evidence_to_collect=["activation decisions", "activation order", "handoff condition"],
        ),
        EvaluationDimensionPlan(
            dimension="capability_contribution",
            question="Does the second capability add product value beyond the first capability alone?",
            success_criteria=["A and B contributions are separately observable", "The combined deliverable includes an additional useful outcome"],
            evidence_to_collect=["A-only output", "B-only output", "combined output", "user-facing difference"],
        ),
        EvaluationDimensionPlan(
            dimension="synergy_gain",
            question="Does A+B create a capability that simple output concatenation cannot provide?",
            success_criteria=["There is an evidenced feedback loop or information handoff", "The combined result improves the declared user job"],
            evidence_to_collect=["intermediate outputs", "handoff payload", "revised decision", "combined task result"],
        ),
        EvaluationDimensionPlan(
            dimension="coordination",
            question="Do the capabilities coordinate without duplicated actions or uncontrolled loops?",
            success_criteria=["Activation order is coherent", "Dependencies and handoffs are satisfied", "No duplicate final answer is produced"],
            evidence_to_collect=["execution order", "dependency resolution", "duplicate-action count", "final delivery shape"],
        ),
        EvaluationDimensionPlan(
            dimension="conflict",
            question="Do competing goals or constraints create interference, override, or inconsistent output?",
            success_criteria=["Conflicting instructions are surfaced and resolved", "Neither capability silently defeats a higher-priority constraint"],
            evidence_to_collect=["conflicting instruction observations", "override decisions", "inconsistent-output checks", "loop/failure events"],
        ),
        EvaluationDimensionPlan(
            dimension="reliability_cost",
            question="Is the combined capability reliable enough to justify its added latency, tokens, and cost?",
            success_criteria=["Failure rate is separately recorded", "Latency, token use, and cost deltas are visible", "The recommendation states when to enable the pair"],
            evidence_to_collect=["failure counts", "latency", "input/output tokens", "cost", "resource amplification"],
        ),
    ]


def _experiment_id(target: EvaluationTarget, change: EvaluationChange, kind: str) -> str:
    raw = f"{target.project_id}:{target.target_id}:{change.change_id}:{kind}"
    return "experiment_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "EvalEngineeringAblationStrategy",
    "EvalEngineeringDesignAssistant",
    "EvalEngineeringSkillPairStrategy",
]
