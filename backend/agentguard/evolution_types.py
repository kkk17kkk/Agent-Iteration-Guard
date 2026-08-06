"""Canonical type vocabulary shared by planning and evidence boundaries."""

from typing import Literal


ComponentType = Literal["skill", "skill_pair", "tool", "memory", "prompt", "release"]
ChangeType = Literal["ablation", "interaction", "regression", "evolution", "modification", "release"]
EvaluationType = Literal[
    "skill_ablation",
    "skill_pair_evaluation",
    "tool_skill_interaction",
    "tool_regression",
    "memory_evolution",
    "prompt_change",
    "release_summary",
]
EvaluationDimension = Literal[
    "trigger",
    "execution",
    "delivery",
    "boundary",
    "capability_contribution",
    "synergy_gain",
    "coordination",
    "conflict",
    "reliability_cost",
]
EvaluationExperimentKind = Literal[
    "baseline",
    "removal",
    "equivalence",
    "interaction",
    "pair_a_only",
    "pair_b_only",
    "pair_combined",
]


__all__ = [
    "ChangeType",
    "ComponentType",
    "EvaluationDimension",
    "EvaluationExperimentKind",
    "EvaluationType",
]
