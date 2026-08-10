"""Shared planning vocabulary for interactions between Agent capabilities.

This module is intentionally component-neutral.  A Skill Pair is one current
consumer; a future Tool + Skill interaction can reuse the same relation and
scenario policy without inheriting Skill Ablation semantics.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .evaluation_suite import ScenarioSuiteConfig, scenario_category_sequence


InteractionRelationship = Literal[
    "complementary",
    "competitive",
    "validator_checker",
    "uncertain",
    "overlapping",
]
InteractionScenarioCategory = Literal[
    "complementary",
    "synergy",
    "conflict",
    "single_skill_dominant",
    "boundary",
    "equivalent_choice",
    "a_preferred",
    "b_preferred",
    "ambiguous_overlap",
]


class PlanningCallMetadata(BaseModel):
    """Non-secret provenance for one Eval Engineering control-plane call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    request_id: str | None = Field(default=None, max_length=200)
    request_fingerprint: str = Field(min_length=16, max_length=200)
    response_fingerprint: str = Field(min_length=16, max_length=200)


class InteractionHypothesisSource(BaseModel):
    """States why a relationship is a planning hypothesis, not ground truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["eval_engineering"] = "eval_engineering"
    inputs: list[Literal["description", "responsibility", "dependency", "boundary"]] = Field(
        min_length=1, max_length=4
    )
    status: Literal["hypothesis"] = "hypothesis"


class InteractionRelationshipProfile(BaseModel):
    """LLM-derived relationship hypothesis used only to choose scenarios."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relationship: InteractionRelationship
    rationale: str = Field(min_length=1, max_length=360)
    signals: list[str] = Field(min_length=1, max_length=6)
    hypothesis_source: InteractionHypothesisSource = Field(
        default_factory=lambda: InteractionHypothesisSource(inputs=["description", "responsibility"])
    )
    provider_metadata: PlanningCallMetadata | None = None
    hypothesis_hash: str | None = Field(default=None, min_length=16, max_length=200)


_SCENARIO_POLICIES: dict[InteractionRelationship, tuple[InteractionScenarioCategory, ...]] = {
    # Two complementary cases retain the user's requested 2x complementary
    # coverage while still testing feedback, conflict, and a safe boundary.
    "complementary": ("complementary", "synergy", "conflict", "boundary"),
    "competitive": ("conflict", "single_skill_dominant", "boundary"),
    "validator_checker": ("synergy", "conflict", "boundary"),
    "uncertain": ("complementary", "conflict", "single_skill_dominant", "boundary"),
    "overlapping": ("equivalent_choice", "a_preferred", "b_preferred", "ambiguous_overlap"),
}


def scenario_categories_for_relationship(
    relationship: InteractionRelationship,
) -> tuple[InteractionScenarioCategory, ...]:
    """Return the bounded scenario matrix selected by Eval Engineering."""

    return _SCENARIO_POLICIES[relationship]


def validate_scenario_categories(
    relationship: InteractionRelationship,
    categories: list[str] | tuple[str, ...],
    *,
    suite_config: ScenarioSuiteConfig | None = None,
) -> None:
    selected = scenario_categories_for_relationship(relationship)
    expected_sequence = (
        scenario_category_sequence(selected, suite_config)
        if suite_config is not None
        else selected
    )
    expected = Counter(expected_sequence)
    observed = Counter(categories)
    if observed != expected:
        raise ValueError(
            "Interaction Scenario Generator returned the wrong category matrix: "
            f"expected={dict(expected)}, observed={dict(observed)}."
        )


__all__ = [
    "InteractionRelationship",
    "InteractionHypothesisSource",
    "InteractionRelationshipProfile",
    "InteractionScenarioCategory",
    "PlanningCallMetadata",
    "scenario_categories_for_relationship",
    "validate_scenario_categories",
]
