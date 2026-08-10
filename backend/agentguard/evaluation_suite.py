"""Shared configuration and planning helpers for scenario-suite evaluations."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioIdentity(Protocol):
    scenario_id: str
    category: str


class ScenarioSuiteConfig(BaseModel):
    """Frozen density, repetition, and execution budget for one evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.scenario-suite-config.v1"] = "aig.scenario-suite-config.v1"
    scenarios_per_category: int = Field(ge=1, le=20)
    max_scenarios: int = Field(ge=3, le=200)
    max_trials: int = Field(ge=3, le=1000)
    default_repetitions: int = Field(ge=1, le=10)
    stability_sample_per_category: int = Field(ge=0, le=20)
    stability_repetitions: int = Field(ge=1, le=10)
    trial_timeout_seconds: float = Field(gt=0, le=3600)

    @model_validator(mode="after")
    def validate_repetition_policy(self) -> "ScenarioSuiteConfig":
        if self.stability_repetitions < self.default_repetitions:
            raise ValueError("stability_repetitions cannot be lower than default_repetitions")
        return self


def default_scenario_suite_config(component_type: str) -> ScenarioSuiteConfig:
    """Return component defaults without embedding target/project behavior."""

    if component_type == "skill":
        return ScenarioSuiteConfig(
            scenarios_per_category=5,
            max_scenarios=20,
            max_trials=84,
            default_repetitions=1,
            stability_sample_per_category=1,
            stability_repetitions=3,
            trial_timeout_seconds=120,
        )
    if component_type == "skill_pair":
        return ScenarioSuiteConfig(
            scenarios_per_category=8,
            max_scenarios=40,
            max_trials=144,
            default_repetitions=1,
            stability_sample_per_category=1,
            stability_repetitions=3,
            trial_timeout_seconds=120,
        )
    raise ValueError(f"No scenario-suite defaults are registered for component_type={component_type}.")


def allocate_repetitions(
    scenarios: Sequence[ScenarioIdentity],
    config: ScenarioSuiteConfig,
    *,
    condition_count: int,
) -> dict[str, int]:
    """Allocate selective reruns deterministically within the frozen trial budget."""

    if condition_count < 1:
        raise ValueError("condition_count must be positive")
    if len({item.scenario_id for item in scenarios}) != len(scenarios):
        raise ValueError("Scenario repetition allocation requires unique scenario IDs.")
    base_trials = len(scenarios) * condition_count * config.default_repetitions
    if base_trials > config.max_trials:
        raise ValueError(
            f"Scenario suite requires {base_trials} base trials, exceeding max_trials={config.max_trials}."
        )
    counts = {item.scenario_id: config.default_repetitions for item in scenarios}
    if config.stability_sample_per_category == 0 or config.stability_repetitions == config.default_repetitions:
        return counts
    remaining = config.max_trials - base_trials
    extra_per_scenario = condition_count * (config.stability_repetitions - config.default_repetitions)
    selected_by_category: Counter[str] = Counter()
    for scenario in scenarios:
        if selected_by_category[scenario.category] >= config.stability_sample_per_category:
            continue
        if remaining < extra_per_scenario:
            break
        counts[scenario.scenario_id] = config.stability_repetitions
        selected_by_category[scenario.category] += 1
        remaining -= extra_per_scenario
    return counts


def scenario_category_sequence(
    categories: Sequence[str],
    config: ScenarioSuiteConfig,
) -> tuple[str, ...]:
    """Expand selected categories into a balanced, bounded generation quota."""

    if not categories or len(set(categories)) != len(categories):
        raise ValueError("Scenario categories must be a non-empty unique sequence.")
    if config.max_scenarios < len(categories):
        raise ValueError(
            f"max_scenarios={config.max_scenarios} cannot cover {len(categories)} required categories."
        )
    sequence: list[str] = []
    for _ in range(config.scenarios_per_category):
        for category in categories:
            if len(sequence) == config.max_scenarios:
                return tuple(sequence)
            sequence.append(category)
    return tuple(sequence)


class NumericSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=0)
    total: float
    mean: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class CategoryAggregate(BaseModel):
    """Observed trial evidence for one category and condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1)
    condition_kind: str = Field(min_length=1)
    scenario_count: int = Field(ge=0)
    trial_count: int = Field(ge=0)
    verified_success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    resolution_rate: float | None = Field(default=None, ge=0, le=1)
    observed_success_rate: float | None = Field(default=None, ge=0, le=1)
    latency_ms: NumericSummary
    cost_usd: NumericSummary
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    complete_evidence_count: int = Field(ge=0)


class RepetitionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    condition_kind: str = Field(min_length=1)
    repetition_ids: list[str] = Field(min_length=1, max_length=10)
    outcomes: list[str] = Field(min_length=1, max_length=10)
    observed_outcome_stability_rate: float = Field(ge=0, le=1)
    routing_sequence: list[str] | None = None
    observed_routing_stability_rate: float | None = Field(default=None, ge=0, le=1)


class EvaluationCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intended_scenarios_per_category: int | None = Field(default=None, ge=1)
    intended_scenario_count: int | None = Field(default=None, ge=1)
    planned_scenario_count: int = Field(ge=0)
    executed_scenario_count: int = Field(ge=0)
    planned_trial_count: int = Field(ge=0)
    executed_trial_count: int = Field(ge=0)
    category_scenario_counts: dict[str, int] = Field(default_factory=dict)
    condition_trial_counts: dict[str, int] = Field(default_factory=dict)
    intended_repeated_scenario_count: int | None = Field(default=None, ge=0)
    repeated_scenario_count: int = Field(ge=0)
    complete_evidence_count: int = Field(ge=0)
    live_target_metadata_count: int = Field(ge=0)
    status: Literal["full", "partial", "unavailable"]


class RoutingAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_trial_count: int = Field(ge=1)
    a_selected_count: int = Field(ge=0)
    b_selected_count: int = Field(ge=0)
    both_selected_count: int = Field(ge=0)
    neither_selected_count: int = Field(ge=0)
    first_selected_a_count: int = Field(ge=0)
    first_selected_b_count: int = Field(ge=0)
    repeated_activation_count: int = Field(ge=0)
    selection_share_a: float = Field(ge=0, le=1)
    selection_share_b: float = Field(ge=0, le=1)
    dual_activation_rate: float = Field(ge=0, le=1)
    missed_activation_rate: float = Field(ge=0, le=1)
    preferred_skill_selection_rate: float | None = Field(default=None, ge=0, le=1)
    routing_correctness: float | None = Field(default=None, ge=0, le=1)
    routing_stability_rate: float | None = Field(default=None, ge=0, le=1)
    success_given_a_selected: float | None = Field(default=None, ge=0, le=1)
    success_given_b_selected: float | None = Field(default=None, ge=0, le=1)
    routing_regression_rate: float | None = Field(default=None, ge=0, le=1)


class FailurePattern(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_type: str = Field(min_length=1)
    condition_kind: str = Field(min_length=1)
    frequency: int = Field(ge=1)
    affected_scenario_ids: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    assertion_status: Literal["failed", "unresolved"] = "failed"
    affected_scenario_count: int | None = Field(default=None, ge=1)
    affected_trial_count: int | None = Field(default=None, ge=1)
    stability: Literal["stable_repeated_failure", "intermittent", "single_run_anomaly", "unresolved"] = "unresolved"


class FailureIncidence(BaseModel):
    """Oracle-declared typed failure frequency over applicable evaluated support."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_type: str = Field(min_length=1)
    failure_count: int = Field(ge=0)
    applicable_scenario_count: int = Field(ge=1)
    applicable_trial_count: int = Field(ge=1)
    resolved_trial_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    observed_rate: float | None = Field(default=None, ge=0, le=1)
    affected_scenario_ids: list[str] = Field(default_factory=list)
    affected_conditions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class ScenarioRoutingAggregate(BaseModel):
    """Observed combined-arm routing for one scenario; shares require repetition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    scenario_category: str = Field(min_length=1)
    repetition_count: int = Field(ge=1)
    observed_trial_count: int = Field(ge=0)
    unavailable_trial_count: int = Field(ge=0)
    a_selected_count: int = Field(ge=0)
    b_selected_count: int = Field(ge=0)
    both_selected_count: int = Field(ge=0)
    neither_selected_count: int = Field(ge=0)
    first_a_count: int = Field(ge=0)
    first_b_count: int = Field(ge=0)
    a_empirical_share: float | None = Field(default=None, ge=0, le=1)
    b_empirical_share: float | None = Field(default=None, ge=0, le=1)
    both_empirical_share: float | None = Field(default=None, ge=0, le=1)
    neither_empirical_share: float | None = Field(default=None, ge=0, le=1)
    routing_stability: float | None = Field(default=None, ge=0, le=1)


class OracleScopeAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    declared_scopes: list[Literal["structural", "behavioral", "domain_correctness", "external_fact"]]
    scoped_trial_count: int = Field(ge=1)
    total_trial_count: int = Field(ge=1)
    unscoped_trial_count: int = Field(ge=0)
    limitations: list[str] = Field(default_factory=list)


class EvaluationSuiteAggregate(BaseModel):
    """One persisted deterministic projection shared by Skill and Pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-suite-aggregate.v1"] = "aig.evaluation-suite-aggregate.v1"
    coverage: EvaluationCoverage
    category_aggregates: list[CategoryAggregate]
    condition_aggregates: list[CategoryAggregate]
    repetitions: list[RepetitionSummary]
    failure_patterns: list[FailurePattern] = Field(default_factory=list)
    failure_incidence: list[FailureIncidence] = Field(default_factory=list)
    derived_metrics: dict[str, int | float | str | None]
    routing: RoutingAggregate | None = None
    scenario_routing: list[ScenarioRoutingAggregate] = Field(default_factory=list)
    oracle_scope: OracleScopeAggregate | None = None


def build_evaluation_suite_aggregate(
    scenarios: Sequence[Mapping[str, object]],
    conditions: Sequence[Mapping[str, object]],
    *,
    condition_kinds: Sequence[str],
    evaluation_type: str,
    suite_config: ScenarioSuiteConfig | None,
    component_members: Sequence[str] = (),
) -> EvaluationSuiteAggregate:
    """Derive replayable suite facts from completed trial cells exactly once."""

    scenario_by_id = {str(item["scenario_id"]): item for item in scenarios}
    if len(scenario_by_id) != len(scenarios):
        raise ValueError("Suite aggregation requires unique scenario IDs.")
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for item in conditions:
        scenario_id = str(item.get("scenario_id") or "")
        condition_kind = str(item.get("condition_kind") or "")
        if scenario_id not in scenario_by_id or condition_kind not in condition_kinds:
            raise ValueError("Suite aggregation received an unknown scenario or condition.")
        if item.get("category") is not None and str(item.get("category")) != str(scenario_by_id[scenario_id]["category"]):
            raise ValueError("Suite aggregation received a trial with a mismatched scenario category.")
        grouped[(scenario_id, condition_kind)].append(item)

    category_groups: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    condition_groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for (scenario_id, condition_kind), trials in grouped.items():
        category = str(scenario_by_id[scenario_id]["category"])
        category_groups[(category, condition_kind)].extend(trials)
        condition_groups[condition_kind].extend(trials)
    category_aggregates = [
        _trial_aggregate(category, condition_kind, trials)
        for (category, condition_kind), trials in sorted(category_groups.items())
    ]
    condition_aggregates = [
        _trial_aggregate("all", condition_kind, trials)
        for condition_kind, trials in sorted(condition_groups.items())
    ]

    repetitions: list[RepetitionSummary] = []
    routing_labels: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (scenario_id, condition_kind), trials in sorted(grouped.items()):
        ordered = sorted(trials, key=lambda item: int(item.get("repetition_index", 1)))
        outcomes = [str(_oracle(item).get("outcome") or "unresolved") for item in ordered]
        labels = [_trial_routing_label(item, component_members) for item in ordered]
        known_labels = [item for item in labels if item is not None]
        if known_labels:
            routing_labels[(scenario_id, condition_kind)] = known_labels
        repetitions.append(RepetitionSummary(
            scenario_id=scenario_id,
            condition_kind=condition_kind,
            repetition_ids=[
                str(item.get("repetition_id") or f"{scenario_id}:{condition_kind}:r{index}")
                for index, item in enumerate(ordered, 1)
            ],
            outcomes=outcomes,
            observed_outcome_stability_rate=_mode_rate(outcomes),
            routing_sequence=known_labels or None,
            observed_routing_stability_rate=_mode_rate(known_labels) if known_labels else None,
        ))

    category_counts = Counter(str(item["category"]) for item in scenarios)
    condition_counts = Counter(str(item.get("condition_kind") or "") for item in conditions)
    planned_trial_count = sum(
        int(item.get("repetition_count", 1)) * len(condition_kinds)
        for item in scenarios
    )
    complete_count = sum(_evidence_complete(item) for item in conditions)
    live_metadata_count = sum(
        bool(item.get("provider_request_ids") and item.get("usage") and item.get("output_artifact_ref"))
        for item in conditions
    )
    intended_scenario_count = (
        suite_config.scenarios_per_category * len(category_counts)
        if suite_config else None
    )
    repeated_scenario_count = sum(int(item.get("repetition_count", 1)) > 1 for item in scenarios)
    intended_repeated_scenario_count = None
    if suite_config:
        intended_repeated_scenario_count = (
            len(scenarios)
            if suite_config.default_repetitions > 1
            else sum(
                min(suite_config.stability_sample_per_category, category_count)
                for category_count in category_counts.values()
            )
            if suite_config.stability_repetitions > 1
            else 0
        )
    full = (
        len(conditions) == planned_trial_count
        and complete_count == len(conditions)
        and (intended_scenario_count is None or len(scenarios) == intended_scenario_count)
        and (
            intended_repeated_scenario_count is None
            or repeated_scenario_count == intended_repeated_scenario_count
        )
    )
    coverage = EvaluationCoverage(
        intended_scenarios_per_category=(suite_config.scenarios_per_category if suite_config else None),
        intended_scenario_count=intended_scenario_count,
        planned_scenario_count=len(scenarios),
        executed_scenario_count=len({str(item.get("scenario_id")) for item in conditions}),
        planned_trial_count=planned_trial_count,
        executed_trial_count=len(conditions),
        category_scenario_counts=dict(sorted(category_counts.items())),
        condition_trial_counts=dict(sorted(condition_counts.items())),
        intended_repeated_scenario_count=intended_repeated_scenario_count,
        repeated_scenario_count=repeated_scenario_count,
        complete_evidence_count=complete_count,
        live_target_metadata_count=live_metadata_count,
        status="full" if full else "partial",
    )
    derived = (
        _skill_derived_metrics(condition_aggregates, conditions)
        if evaluation_type == "skill_ablation"
        else _pair_derived_metrics(grouped, conditions)
        if evaluation_type == "skill_pair_evaluation"
        else {}
    )
    routing = (
        _routing_aggregate(scenario_by_id, grouped, component_members)
        if evaluation_type == "skill_pair_evaluation" and len(component_members) == 2
        else None
    )
    scenario_routing = (
        _scenario_routing_aggregates(scenario_by_id, grouped, component_members)
        if evaluation_type == "skill_pair_evaluation" and len(component_members) == 2
        else []
    )
    return EvaluationSuiteAggregate(
        coverage=coverage,
        category_aggregates=category_aggregates,
        condition_aggregates=condition_aggregates,
        repetitions=repetitions,
        failure_patterns=_failure_patterns(conditions),
        failure_incidence=_failure_incidence(conditions),
        derived_metrics=derived,
        routing=routing,
        scenario_routing=scenario_routing,
        oracle_scope=_oracle_scope_aggregate(conditions),
    )


def _trial_aggregate(category: str, condition_kind: str, trials: Sequence[Mapping[str, object]]) -> CategoryAggregate:
    outcomes = [str(_oracle(item).get("outcome") or "unresolved") for item in trials]
    passed = outcomes.count("passed")
    failed = outcomes.count("failed")
    unresolved = outcomes.count("unresolved")
    decided = passed + failed
    latencies = [float(_metrics(item)["latency_ms"]) for item in trials]
    costs = [float(_metrics(item)["cost_usd"]) for item in trials]
    input_tokens = _token_sum(trials, ("input_tokens", "prompt_tokens"))
    output_tokens = _token_sum(trials, ("output_tokens", "completion_tokens"))
    total_tokens = _token_sum(trials, ("total_tokens",))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return CategoryAggregate(
        category=category,
        condition_kind=condition_kind,
        scenario_count=len({str(item.get("scenario_id")) for item in trials}),
        trial_count=len(trials),
        verified_success_count=passed,
        failure_count=failed,
        resolved_count=decided,
        unresolved_count=unresolved,
        resolution_rate=decided / len(trials) if trials else None,
        observed_success_rate=passed / decided if decided else None,
        latency_ms=_numeric_summary(latencies),
        cost_usd=_numeric_summary(costs),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        complete_evidence_count=sum(_evidence_complete(item) for item in trials),
    )


def _skill_derived_metrics(
    aggregates: Sequence[CategoryAggregate],
    conditions: Sequence[Mapping[str, object]],
) -> dict[str, int | float | str | None]:
    rates = {item.condition_kind: item.observed_success_rate for item in aggregates}
    baseline = rates.get("baseline")
    removal = rates.get("removal")
    replacement = rates.get("replacement")
    trial_by_key = {
        (str(item.get("scenario_id")), str(item.get("condition_kind")), int(item.get("repetition_index", 1))): item
        for item in conditions
    }
    matched_keys = sorted({
        (scenario_id, repetition_index)
        for scenario_id, condition_kind, repetition_index in trial_by_key
        if condition_kind == "baseline"
        and all((scenario_id, kind, repetition_index) in trial_by_key for kind in ("removal", "replacement"))
    })
    resolved_triples: list[tuple[int, int, int]] = []
    for scenario_id, repetition_index in matched_keys:
        values = tuple(
            _resolved_pass_value(trial_by_key[(scenario_id, kind, repetition_index)])
            for kind in ("baseline", "removal", "replacement")
        )
        if all(value is not None for value in values):
            resolved_triples.append((int(values[0]), int(values[1]), int(values[2])))
    comparable_count = len(resolved_triples)
    comparable = {
        "baseline": sum(item[0] for item in resolved_triples) / comparable_count if comparable_count else None,
        "removal": sum(item[1] for item in resolved_triples) / comparable_count if comparable_count else None,
        "replacement": sum(item[2] for item in resolved_triples) / comparable_count if comparable_count else None,
    }
    return {
        "baseline_observed_pass_rate": baseline,
        "removal_observed_pass_rate": removal,
        "replacement_observed_pass_rate": replacement,
        "matched_triple_count": len(matched_keys),
        "resolved_matched_triple_count": comparable_count,
        "matched_triple_resolution_rate": comparable_count / len(matched_keys) if matched_keys else None,
        "comparable_baseline_observed_pass_rate": comparable["baseline"],
        "comparable_removal_observed_pass_rate": comparable["removal"],
        "comparable_replacement_observed_pass_rate": comparable["replacement"],
        "removal_delta_vs_baseline": (
            comparable["removal"] - comparable["baseline"]
            if comparable["removal"] is not None and comparable["baseline"] is not None else None
        ),
        "replacement_delta_vs_baseline": (
            comparable["replacement"] - comparable["baseline"]
            if comparable["replacement"] is not None and comparable["baseline"] is not None else None
        ),
    }


def _pair_derived_metrics(
    grouped: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
    conditions: Sequence[Mapping[str, object]],
) -> dict[str, int | float | str | None]:
    rates: dict[str, float | None] = {}
    for kind in ("a_only", "b_only", "combined"):
        outcomes = [str(_oracle(item).get("outcome") or "unresolved") for item in conditions if item.get("condition_kind") == kind]
        decided = outcomes.count("passed") + outcomes.count("failed")
        rates[kind] = outcomes.count("passed") / decided if decided else None
    trial_by_key = {
        (str(item.get("scenario_id")), str(item.get("condition_kind")), int(item.get("repetition_index", 1))): item
        for item in conditions
    }
    matched_keys = sorted({
        (scenario_id, repetition_index)
        for scenario_id, condition_kind, repetition_index in trial_by_key
        if condition_kind == "combined"
        and all((scenario_id, kind, repetition_index) in trial_by_key for kind in ("a_only", "b_only"))
    })
    resolved_triples: list[tuple[str, int, int, int]] = []
    for scenario_id, repetition_index in matched_keys:
        values = [
            _resolved_pass_value(trial_by_key[(scenario_id, kind, repetition_index)])
            for kind in ("a_only", "b_only", "combined")
        ]
        if all(value is not None for value in values):
            resolved_triples.append((scenario_id, int(values[0]), int(values[1]), int(values[2])))
    comparable_count = len(resolved_triples)
    comparable_rates = {
        "a_only": sum(item[1] for item in resolved_triples) / comparable_count if comparable_count else None,
        "b_only": sum(item[2] for item in resolved_triples) / comparable_count if comparable_count else None,
        "combined": sum(item[3] for item in resolved_triples) / comparable_count if comparable_count else None,
    }
    comparisons = [
        1 if ab > max(a_only, b_only) else -1 if ab < max(a_only, b_only) else 0
        for _, a_only, b_only, ab in resolved_triples
    ]
    count = len(comparisons)
    metrics: dict[str, int | float | str | None] = {
        "a_only_observed_pass_rate": rates["a_only"],
        "b_only_observed_pass_rate": rates["b_only"],
        "ab_observed_pass_rate": rates["combined"],
        "matched_triple_count": len(matched_keys),
        "resolved_matched_triple_count": comparable_count,
        "matched_triple_resolution_rate": comparable_count / len(matched_keys) if matched_keys else None,
        "comparable_scenario_count": len({item[0] for item in resolved_triples}),
        "comparable_a_only_observed_pass_rate": comparable_rates["a_only"],
        "comparable_b_only_observed_pass_rate": comparable_rates["b_only"],
        "comparable_ab_observed_pass_rate": comparable_rates["combined"],
        "ab_better_than_best_single_rate": comparisons.count(1) / count if count else None,
        "ab_equal_to_best_single_rate": comparisons.count(0) / count if count else None,
        "ab_worse_than_best_single_rate": comparisons.count(-1) / count if count else None,
        "pair_gain": (
            comparable_rates["combined"] - max(comparable_rates["a_only"], comparable_rates["b_only"])
            if all(value is not None for value in comparable_rates.values()) else None
        ),
        "delta_a_given_b": (
            comparable_rates["combined"] - comparable_rates["b_only"]
            if comparable_rates["combined"] is not None and comparable_rates["b_only"] is not None else None
        ),
        "delta_b_given_a": (
            comparable_rates["combined"] - comparable_rates["a_only"]
            if comparable_rates["combined"] is not None and comparable_rates["a_only"] is not None else None
        ),
    }
    for field in ("conflict", "boundary_violation", "unnecessary_dual_activation", "coordination_failure"):
        observed = [bool(_observations(item)[field]) for item in conditions if field in _observations(item)]
        metrics[field + "_rate"] = sum(observed) / len(observed) if observed else None
    for metric_name, output_name in (("latency_ms", "latency_delta"), ("cost_usd", "cost_delta")):
        arm_means = {
            kind: mean(float(_metrics(item)[metric_name]) for item in conditions if item.get("condition_kind") == kind)
            for kind in ("a_only", "b_only", "combined")
            if any(item.get("condition_kind") == kind for item in conditions)
        }
        delta = (
            arm_means["combined"] - min(arm_means["a_only"], arm_means["b_only"])
            if set(arm_means) == {"a_only", "b_only", "combined"}
            else None
        )
        metrics[output_name] = delta
        metrics[output_name + "_vs_best_single"] = delta
    token_means = {
        kind: mean(
            value
            for item in conditions
            if item.get("condition_kind") == kind
            if (value := _trial_total_tokens(item)) is not None
        )
        for kind in ("a_only", "b_only", "combined")
        if any(
            _trial_total_tokens(item) is not None
            for item in conditions
            if item.get("condition_kind") == kind
        )
    }
    metrics["token_delta"] = (
        token_means["combined"] - min(token_means["a_only"], token_means["b_only"])
        if set(token_means) == {"a_only", "b_only", "combined"}
        else None
    )
    metrics["token_delta_vs_best_single"] = metrics["token_delta"]
    return metrics


def _resolved_pass_value(item: Mapping[str, object]) -> int | None:
    outcome = str(_oracle(item).get("outcome") or "unresolved")
    if outcome == "passed":
        return 1
    if outcome == "failed":
        return 0
    return None


def _trial_total_tokens(item: Mapping[str, object]) -> int | None:
    usage = item.get("usage")
    if not isinstance(usage, Mapping):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    input_tokens = next(
        (usage.get(field) for field in ("input_tokens", "prompt_tokens") if isinstance(usage.get(field), int)),
        None,
    )
    output_tokens = next(
        (usage.get(field) for field in ("output_tokens", "completion_tokens") if isinstance(usage.get(field), int)),
        None,
    )
    return input_tokens + output_tokens if input_tokens is not None or output_tokens is not None else None


def _routing_aggregate(
    scenario_by_id: Mapping[str, Mapping[str, object]],
    grouped: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
    members: Sequence[str],
) -> RoutingAggregate | None:
    observations: list[tuple[str, list[str], str]] = []
    correctness: list[bool] = []
    preferred: list[bool] = []
    for (scenario_id, condition_kind), trials in grouped.items():
        if condition_kind != "combined":
            continue
        expectation = scenario_by_id[scenario_id].get("routing_expectation")
        for trial in trials:
            sequence = _trace_member_sequence(trial.get("trace"), members)
            label = _trial_routing_label(trial, members)
            if label is None:
                continue
            outcome = str(_oracle(trial).get("outcome") or "unresolved")
            observations.append((label, sequence, outcome))
            if isinstance(expectation, Mapping):
                expected = str(expectation.get("expected_routing") or "")
                valid = label == expected or expected == "either" and label in {"a", "b"}
                correctness.append(valid)
                if expected in {"a", "b"}:
                    preferred.append(label == expected)
    if not observations:
        return None
    labels = [item[0] for item in observations]
    a_success = [item[2] == "passed" for item in observations if "a" in item[1]]
    b_success = [item[2] == "passed" for item in observations if "b" in item[1]]
    stability_values = [
        _mode_rate([_trial_routing_label(item, members) or "unknown" for item in trials])
        for (scenario_id, condition_kind), trials in grouped.items()
        if condition_kind == "combined" and len(trials) > 1
    ]
    return RoutingAggregate(
        observed_trial_count=len(observations),
        a_selected_count=sum("a" in item[1] for item in observations),
        b_selected_count=sum("b" in item[1] for item in observations),
        both_selected_count=labels.count("both"),
        neither_selected_count=labels.count("neither"),
        first_selected_a_count=sum(bool(item[1]) and item[1][0] == "a" for item in observations),
        first_selected_b_count=sum(bool(item[1]) and item[1][0] == "b" for item in observations),
        repeated_activation_count=sum(len(item[1]) != len(set(item[1])) for item in observations),
        selection_share_a=sum("a" in item[1] for item in observations) / len(observations),
        selection_share_b=sum("b" in item[1] for item in observations) / len(observations),
        dual_activation_rate=labels.count("both") / len(observations),
        missed_activation_rate=labels.count("neither") / len(observations),
        preferred_skill_selection_rate=sum(preferred) / len(preferred) if preferred else None,
        routing_correctness=sum(correctness) / len(correctness) if correctness else None,
        routing_stability_rate=mean(stability_values) if stability_values else None,
        success_given_a_selected=sum(a_success) / len(a_success) if a_success else None,
        success_given_b_selected=sum(b_success) / len(b_success) if b_success else None,
        routing_regression_rate=(1 - sum(correctness) / len(correctness)) if correctness else None,
    )


def _scenario_routing_aggregates(
    scenario_by_id: Mapping[str, Mapping[str, object]],
    grouped: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
    members: Sequence[str],
) -> list[ScenarioRoutingAggregate]:
    result: list[ScenarioRoutingAggregate] = []
    for (scenario_id, condition_kind), trials in sorted(grouped.items()):
        if condition_kind != "combined":
            continue
        ordered = sorted(trials, key=lambda item: int(item.get("repetition_index", 1)))
        sequences = [_trace_member_sequence(item.get("trace"), members) for item in ordered]
        labels = [_trial_routing_label(item, members) for item in ordered]
        known = [label for label in labels if label is not None]
        if not known:
            continue
        repeated_support = len(ordered) > 1
        denominator = len(known)
        result.append(ScenarioRoutingAggregate(
            scenario_id=scenario_id,
            scenario_category=str(scenario_by_id[scenario_id]["category"]),
            repetition_count=len(ordered),
            observed_trial_count=denominator,
            unavailable_trial_count=len(ordered) - denominator,
            a_selected_count=sum("a" in sequence for sequence in sequences),
            b_selected_count=sum("b" in sequence for sequence in sequences),
            both_selected_count=known.count("both"),
            neither_selected_count=known.count("neither"),
            first_a_count=sum(bool(sequence) and sequence[0] == "a" for sequence in sequences),
            first_b_count=sum(bool(sequence) and sequence[0] == "b" for sequence in sequences),
            a_empirical_share=(sum("a" in sequence for sequence in sequences) / denominator if repeated_support else None),
            b_empirical_share=(sum("b" in sequence for sequence in sequences) / denominator if repeated_support else None),
            both_empirical_share=(known.count("both") / denominator if repeated_support else None),
            neither_empirical_share=(known.count("neither") / denominator if repeated_support else None),
            routing_stability=_mode_rate(known) if repeated_support else None,
        ))
    return result


def _failure_patterns(conditions: Sequence[Mapping[str, object]]) -> list[FailurePattern]:
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    assertion_support: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for trial in conditions:
        oracle = _oracle(trial)
        condition_kind = str(trial.get("condition_kind") or "unknown")
        scenario_id = str(trial.get("scenario_id") or "unknown")
        assertions = oracle.get("assertions")
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if not isinstance(assertion, Mapping):
                continue
            failure_type = str(assertion.get("name") or "unresolved")
            status = str(assertion.get("status") or "unresolved")
            assertion_support[(failure_type, condition_kind, scenario_id)].append(status)
            if status not in {"failed", "unresolved"}:
                continue
            bucket = grouped.setdefault(
                (failure_type, condition_kind, status),
                {"scenarios": set(), "refs": set(), "count": 0},
            )
            bucket["count"] += 1
            bucket["scenarios"].add(scenario_id)
            for ref in trial.get("evidence_refs", []):
                if isinstance(ref, str) and ref:
                    bucket["refs"].add(ref)
            for ref in oracle.get("evidence_refs", []):
                if isinstance(ref, str) and ref:
                    bucket["refs"].add(ref)
    patterns: list[FailurePattern] = []
    for (failure_type, condition_kind, status), values in sorted(grouped.items()):
        refs = sorted(values["refs"])
        if not refs:
            continue
        scenarios = sorted(values["scenarios"])
        supports = [
            assertion_support[(failure_type, condition_kind, scenario_id)]
            for scenario_id in scenarios
        ]
        if status == "unresolved":
            stability = "unresolved"
        elif all(len(items) == 1 for items in supports):
            stability = "single_run_anomaly"
        elif all(all(item == "failed" for item in items) for items in supports):
            stability = "stable_repeated_failure"
        else:
            stability = "intermittent"
        patterns.append(FailurePattern(
            failure_type=failure_type,
            condition_kind=condition_kind,
            frequency=int(values["count"]),
            affected_scenario_ids=scenarios,
            evidence_refs=refs,
            assertion_status=status,
            affected_scenario_count=len(scenarios),
            affected_trial_count=int(values["count"]),
            stability=stability,
        ))
    return patterns


def _failure_incidence(conditions: Sequence[Mapping[str, object]]) -> list[FailureIncidence]:
    grouped: dict[str, list[tuple[Mapping[str, object], Mapping[str, object]]]] = defaultdict(list)
    for trial in conditions:
        assertions = _oracle(trial).get("assertions")
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if isinstance(assertion, Mapping) and isinstance(assertion.get("failure_type"), str):
                grouped[str(assertion["failure_type"])].append((trial, assertion))
    result: list[FailureIncidence] = []
    for failure_type, rows in sorted(grouped.items()):
        failed = [(trial, assertion) for trial, assertion in rows if assertion.get("status") == "failed"]
        resolved = [(trial, assertion) for trial, assertion in rows if assertion.get("status") in {"passed", "failed"}]
        unresolved = [(trial, assertion) for trial, assertion in rows if assertion.get("status") == "unresolved"]
        refs = {
            ref
            for trial, _ in failed
            for ref in [*trial.get("evidence_refs", []), *_oracle(trial).get("evidence_refs", [])]
            if isinstance(ref, str) and ref
        }
        result.append(FailureIncidence(
            failure_type=failure_type,
            failure_count=len(failed),
            applicable_scenario_count=len({str(trial.get("scenario_id")) for trial, _ in rows}),
            applicable_trial_count=len(rows),
            resolved_trial_count=len(resolved),
            unresolved_count=len(unresolved),
            observed_rate=len(failed) / len(resolved) if resolved else None,
            affected_scenario_ids=sorted({str(trial.get("scenario_id")) for trial, _ in failed}),
            affected_conditions=sorted({str(trial.get("condition_kind")) for trial, _ in failed}),
            evidence_refs=sorted(refs),
        ))
    return result


def _oracle_scope_aggregate(conditions: Sequence[Mapping[str, object]]) -> OracleScopeAggregate | None:
    scoped = []
    scopes: set[str] = set()
    limitations: set[str] = set()
    for trial in conditions:
        oracle = _oracle(trial)
        declared = oracle.get("verification_scopes")
        if not isinstance(declared, list) or not declared:
            continue
        scoped.append(trial)
        scopes.update(str(item) for item in declared)
        raw_limitations = oracle.get("scope_limitations")
        if isinstance(raw_limitations, list):
            limitations.update(str(item) for item in raw_limitations if isinstance(item, str) and item)
    if not scoped:
        return None
    return OracleScopeAggregate(
        declared_scopes=sorted(scopes),
        scoped_trial_count=len(scoped),
        total_trial_count=len(conditions),
        unscoped_trial_count=len(conditions) - len(scoped),
        limitations=sorted(limitations),
    )


def _trace_member_sequence(value: object, members: Sequence[str]) -> list[str]:
    if not isinstance(value, list) or len(members) != 2:
        return []
    aliases = {members[0]: "a", members[1]: "b"}
    sequence: list[str] = []
    for event in value:
        if not isinstance(event, Mapping):
            continue
        event_members: list[str] = []
        event_type = event.get("event_type")
        if event_type == "skill_a_completed":
            event_members.append("a")
        elif event_type == "skill_b_completed":
            event_members.append("b")
        candidates: list[object] = [
            event.get("skill_name", event.get("component_name")),
            event.get("selected_skill"),
        ]
        selected_skills = event.get("selected_skills")
        if isinstance(selected_skills, list):
            candidates.extend(selected_skills)
        for member in candidates:
            if isinstance(member, str) and member in aliases:
                alias = aliases[member]
                if alias not in event_members:
                    event_members.append(alias)
        sequence.extend(event_members)
    return sequence


def _routing_label(sequence: Sequence[str]) -> str | None:
    if not sequence:
        return None
    unique = set(sequence)
    if unique == {"a", "b"}:
        return "both"
    if unique == {"a"}:
        return "a"
    if unique == {"b"}:
        return "b"
    return "neither"


def _trial_routing_label(trial: Mapping[str, object], members: Sequence[str]) -> str | None:
    trace = trial.get("trace")
    if not isinstance(trace, list) or not trace:
        return None
    sequence = _trace_member_sequence(trace, members)
    if sequence:
        return _routing_label(sequence)
    for event in trace:
        if not isinstance(event, Mapping) or event.get("event_type") != "routing_decision":
            continue
        selected = event.get("selected_skills")
        if isinstance(selected, list) and not selected:
            return "neither"
        if event.get("selected_skill") in {None, "neither"}:
            return "neither"
    return None


def _mode_rate(values: Sequence[str]) -> float:
    return max(Counter(values).values()) / len(values)


def _numeric_summary(values: Sequence[float]) -> NumericSummary:
    return NumericSummary(
        count=len(values),
        total=sum(values),
        mean=mean(values) if values else None,
        minimum=min(values) if values else None,
        maximum=max(values) if values else None,
    )


def _token_sum(trials: Sequence[Mapping[str, object]], keys: Sequence[str]) -> int | None:
    values: list[int] = []
    for trial in trials:
        usage = trial.get("usage")
        if not isinstance(usage, Mapping):
            continue
        value = next((usage[key] for key in keys if isinstance(usage.get(key), int)), None)
        if value is not None:
            values.append(int(value))
    return sum(values) if values else None


def _metrics(trial: Mapping[str, object]) -> Mapping[str, object]:
    value = trial.get("metrics")
    if not isinstance(value, Mapping):
        raise ValueError("Suite aggregation requires trial metrics.")
    return value


def _oracle(trial: Mapping[str, object]) -> Mapping[str, object]:
    value = trial.get("oracle")
    if not isinstance(value, Mapping):
        raise ValueError("Suite aggregation requires an Oracle result.")
    return value


def _observations(trial: Mapping[str, object]) -> Mapping[str, object]:
    value = trial.get("observations")
    return value if isinstance(value, Mapping) else {}


def _evidence_complete(trial: Mapping[str, object]) -> bool:
    return bool(
        trial.get("evidence_refs")
        and trial.get("trace")
        and trial.get("output") is not None
        and _oracle(trial).get("status") == "verified"
    )


__all__ = [
    "ScenarioSuiteConfig",
    "CategoryAggregate",
    "EvaluationCoverage",
    "EvaluationSuiteAggregate",
    "FailurePattern",
    "FailureIncidence",
    "NumericSummary",
    "OracleScopeAggregate",
    "RepetitionSummary",
    "ScenarioRoutingAggregate",
    "RoutingAggregate",
    "allocate_repetitions",
    "build_evaluation_suite_aggregate",
    "default_scenario_suite_config",
    "scenario_category_sequence",
]
