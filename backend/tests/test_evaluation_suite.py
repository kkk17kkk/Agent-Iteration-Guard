from types import SimpleNamespace

import pytest

from agentguard.evaluation_suite import (
    ScenarioSuiteConfig,
    allocate_repetitions,
    build_evaluation_suite_aggregate,
    scenario_category_sequence,
)


def _config(**updates):
    payload = {
        "scenarios_per_category": 2,
        "max_scenarios": 8,
        "max_trials": 30,
        "default_repetitions": 1,
        "stability_sample_per_category": 1,
        "stability_repetitions": 3,
        "trial_timeout_seconds": 60,
    }
    return ScenarioSuiteConfig(**{**payload, **updates})


def _trial(scenario_id, category, condition_kind, repetition_index, outcome, *, selected=None):
    trace = [{"event_type": "task_started"}]
    if selected is not None:
        trace.append({"event_type": "routing_decision", "selected_skill": selected})
    return {
        "scenario_id": scenario_id,
        "category": category,
        "condition_kind": condition_kind,
        "repetition_id": f"{scenario_id}:{condition_kind}:r{repetition_index}",
        "repetition_index": repetition_index,
        "observations": {},
        "trace": trace,
        "output": {"ok": outcome == "passed"},
        "metrics": {"latency_ms": 10 * repetition_index, "cost_usd": 0.01},
        "oracle": {"status": "verified", "outcome": outcome},
        "evidence_refs": [f"evidence:{scenario_id}:{condition_kind}:{repetition_index}"],
        "provider_request_ids": [f"request:{scenario_id}:{condition_kind}:{repetition_index}"],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "output_artifact_ref": f"artifact:{scenario_id}:{condition_kind}:{repetition_index}",
    }


def test_category_sequence_and_repetition_allocation_respect_shared_budget():
    config = _config(max_trials=18)
    categories = scenario_category_sequence(("normal", "boundary"), config)
    scenarios = [SimpleNamespace(scenario_id=f"s{index}", category=category) for index, category in enumerate(categories)]

    repetitions = allocate_repetitions(scenarios, config, condition_count=3)

    assert categories == ("normal", "boundary", "normal", "boundary")
    assert list(repetitions.values()).count(3) == 1
    assert sum(repetitions.values()) * 3 == 18


def test_shared_aggregation_derives_skill_rates_usage_and_stability():
    scenarios = [
        {"scenario_id": "s1", "category": "normal", "repetition_count": 2},
        {"scenario_id": "s2", "category": "normal", "repetition_count": 1},
    ]
    outcomes = {
        "baseline": ("passed", "passed", "passed"),
        "removal": ("failed", "failed", "failed"),
        "replacement": ("passed", "failed", "passed"),
    }
    conditions = []
    for condition_kind, values in outcomes.items():
        conditions.extend([
            _trial("s1", "normal", condition_kind, 1, values[0]),
            _trial("s1", "normal", condition_kind, 2, values[1]),
            _trial("s2", "normal", condition_kind, 1, values[2]),
        ])

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("baseline", "removal", "replacement"),
        evaluation_type="skill_ablation",
        suite_config=_config(max_trials=9, scenarios_per_category=2, stability_repetitions=2),
    )

    assert aggregate.coverage.status == "full"
    assert aggregate.coverage.executed_trial_count == 9
    assert aggregate.derived_metrics["baseline_observed_pass_rate"] == 1.0
    assert aggregate.derived_metrics["removal_delta_vs_baseline"] == -1.0
    assert aggregate.derived_metrics["replacement_observed_pass_rate"] == 2 / 3
    assert aggregate.condition_aggregates[0].resolved_count == 3
    assert aggregate.condition_aggregates[0].resolution_rate == 1.0
    assert aggregate.condition_aggregates[0].total_tokens == 45
    assert next(item for item in aggregate.repetitions if item.scenario_id == "s1" and item.condition_kind == "replacement").observed_outcome_stability_rate == 0.5


def test_coverage_is_partial_when_the_scenario_cap_truncates_requested_density():
    scenarios = [{"scenario_id": "s1", "category": "normal", "repetition_count": 1}]
    conditions = [
        _trial("s1", "normal", kind, 1, "passed")
        for kind in ("baseline", "removal", "replacement")
    ]

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("baseline", "removal", "replacement"),
        evaluation_type="skill_ablation",
        suite_config=_config(max_trials=9, scenarios_per_category=2),
    )

    assert aggregate.coverage.intended_scenario_count == 2
    assert aggregate.coverage.status == "partial"


def test_pair_routing_comes_only_from_structured_trace_and_keeps_repetitions():
    scenarios = [{
        "scenario_id": "route-a",
        "category": "a_preferred",
        "repetition_count": 3,
        "routing_expectation": {"expected_routing": "a", "rationale": "A owns this responsibility."},
    }]
    conditions = []
    for kind in ("a_only", "b_only"):
        conditions.extend(_trial("route-a", "a_preferred", kind, index, "passed") for index in range(1, 4))
    conditions.extend([
        _trial("route-a", "a_preferred", "combined", 1, "passed", selected="skill_a"),
        _trial("route-a", "a_preferred", "combined", 2, "passed", selected="skill_a"),
        _trial("route-a", "a_preferred", "combined", 3, "failed", selected="skill_b"),
    ])

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("a_only", "b_only", "combined"),
        evaluation_type="skill_pair_evaluation",
        suite_config=_config(max_trials=9),
        component_members=("skill_a", "skill_b"),
    )

    assert aggregate.routing is not None
    assert aggregate.routing.routing_correctness == 2 / 3
    assert aggregate.routing.routing_stability_rate == 2 / 3
    assert aggregate.routing.success_given_a_selected == 1.0
    assert aggregate.routing.success_given_b_selected == 0.0
    assert aggregate.derived_metrics["delta_a_given_b"] == pytest.approx(-1 / 3)
    assert aggregate.derived_metrics["delta_b_given_a"] == pytest.approx(-1 / 3)
    assert aggregate.derived_metrics["token_delta"] == 0
    combined = next(item for item in aggregate.repetitions if item.condition_kind == "combined")
    assert combined.routing_sequence == ["a", "a", "b"]


def test_pair_contribution_metrics_use_only_resolved_matched_triples():
    scenarios = [{"scenario_id": "s1", "category": "synergy", "repetition_count": 2}]
    conditions = [
        _trial("s1", "synergy", "a_only", 1, "passed"),
        _trial("s1", "synergy", "b_only", 1, "failed"),
        _trial("s1", "synergy", "combined", 1, "passed"),
        _trial("s1", "synergy", "a_only", 2, "unresolved"),
        _trial("s1", "synergy", "b_only", 2, "passed"),
        _trial("s1", "synergy", "combined", 2, "failed"),
    ]

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("a_only", "b_only", "combined"),
        evaluation_type="skill_pair_evaluation",
        suite_config=_config(
            max_trials=6,
            scenarios_per_category=1,
            default_repetitions=2,
            stability_repetitions=2,
        ),
        component_members=("skill_a", "skill_b"),
    )

    metrics = aggregate.derived_metrics
    assert metrics["a_only_observed_pass_rate"] == 1.0
    assert metrics["b_only_observed_pass_rate"] == 0.5
    assert metrics["ab_observed_pass_rate"] == 0.5
    assert metrics["matched_triple_count"] == 2
    assert metrics["resolved_matched_triple_count"] == 1
    assert metrics["matched_triple_resolution_rate"] == 0.5
    assert metrics["delta_a_given_b"] == 1.0
    assert metrics["delta_b_given_a"] == 0.0
    assert metrics["pair_gain"] == 0.0
    assert metrics["ab_equal_to_best_single_rate"] == 1.0


def test_skill_deltas_use_only_resolved_matched_triples():
    scenarios = [{"scenario_id": "s1", "category": "normal", "repetition_count": 2}]
    conditions = [
        _trial("s1", "normal", "baseline", 1, "passed"),
        _trial("s1", "normal", "removal", 1, "failed"),
        _trial("s1", "normal", "replacement", 1, "passed"),
        _trial("s1", "normal", "baseline", 2, "unresolved"),
        _trial("s1", "normal", "removal", 2, "passed"),
        _trial("s1", "normal", "replacement", 2, "failed"),
    ]

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("baseline", "removal", "replacement"),
        evaluation_type="skill_ablation",
        suite_config=_config(max_trials=6, scenarios_per_category=1, default_repetitions=2),
    )

    metrics = aggregate.derived_metrics
    assert metrics["baseline_observed_pass_rate"] == 1.0
    assert metrics["removal_observed_pass_rate"] == 0.5
    assert metrics["replacement_observed_pass_rate"] == 0.5
    assert metrics["matched_triple_count"] == 2
    assert metrics["resolved_matched_triple_count"] == 1
    assert metrics["removal_delta_vs_baseline"] == -1.0
    assert metrics["replacement_delta_vs_baseline"] == 0.0


def test_typed_failure_incidence_uses_oracle_declared_applicable_support_and_scope():
    scenarios = [
        {"scenario_id": "s1", "category": "normal", "repetition_count": 1},
        {"scenario_id": "s2", "category": "normal", "repetition_count": 1},
    ]
    conditions = []
    for scenario_id, status in (("s1", "failed"), ("s2", "unresolved")):
        trial = _trial(scenario_id, "normal", "baseline", 1, "failed" if status == "failed" else "unresolved")
        trial["oracle"].update({
            "verification_scopes": ["behavioral"],
            "scope_limitations": ["Domain correctness was not evaluated."],
            "failure_types_evaluated": ["contradiction"],
            "assertions": [{"name": "contradiction_check", "status": status, "detail": "checked", "failure_type": "contradiction"}],
        })
        conditions.append(trial)

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("baseline",),
        evaluation_type="tool_regression",
        suite_config=None,
    )

    incidence = aggregate.failure_incidence[0]
    assert incidence.failure_type == "contradiction"
    assert incidence.failure_count == 1
    assert incidence.applicable_trial_count == 2
    assert incidence.resolved_trial_count == 1
    assert incidence.unresolved_count == 1
    assert incidence.observed_rate == 1.0
    assert aggregate.oracle_scope is not None
    assert aggregate.oracle_scope.declared_scopes == ["behavioral"]
    assert aggregate.oracle_scope.unscoped_trial_count == 0


def test_scenario_routing_shares_require_repeated_support():
    scenarios = [
        {"scenario_id": "repeat", "category": "a_preferred", "repetition_count": 3},
        {"scenario_id": "single", "category": "b_preferred", "repetition_count": 1},
    ]
    conditions = []
    for scenario_id, repetitions in (("repeat", 3), ("single", 1)):
        for kind in ("a_only", "b_only"):
            conditions.extend(_trial(scenario_id, scenarios[0 if scenario_id == "repeat" else 1]["category"], kind, index, "passed") for index in range(1, repetitions + 1))
    conditions.extend([
        _trial("repeat", "a_preferred", "combined", 1, "passed", selected="skill_a"),
        _trial("repeat", "a_preferred", "combined", 2, "passed", selected="skill_b"),
        _trial("repeat", "a_preferred", "combined", 3, "passed", selected="skill_a"),
        _trial("single", "b_preferred", "combined", 1, "passed", selected="skill_b"),
    ])

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("a_only", "b_only", "combined"),
        evaluation_type="skill_pair_evaluation",
        suite_config=None,
        component_members=("skill_a", "skill_b"),
    )

    repeated = next(item for item in aggregate.scenario_routing if item.scenario_id == "repeat")
    single = next(item for item in aggregate.scenario_routing if item.scenario_id == "single")
    assert repeated.a_empirical_share == pytest.approx(2 / 3)
    assert repeated.b_empirical_share == pytest.approx(1 / 3)
    assert repeated.routing_stability == pytest.approx(2 / 3)
    assert single.b_selected_count == 1
    assert single.b_empirical_share is None
    assert single.routing_stability is None


def test_scenario_routing_accepts_generic_exact_skill_arm_trace_events():
    scenarios = [{"scenario_id": "s1", "category": "complementary", "repetition_count": 1}]
    conditions = [_trial("s1", "complementary", kind, 1, "passed") for kind in ("a_only", "b_only", "combined")]
    combined = conditions[-1]
    combined["trace"] = [
        {"event_type": "skill_a_completed"},
        {"event_type": "skill_b_completed"},
    ]

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("a_only", "b_only", "combined"),
        evaluation_type="skill_pair_evaluation",
        suite_config=None,
        component_members=("skill_a", "skill_b"),
    )

    routing = aggregate.scenario_routing[0]
    assert routing.both_selected_count == 1
    assert routing.a_empirical_share is None


def test_failure_patterns_keep_status_scenario_and_trial_recurrence_distinct():
    scenarios = [
        {"scenario_id": "stable", "category": "normal", "repetition_count": 2},
        {"scenario_id": "mixed", "category": "normal", "repetition_count": 2},
    ]
    conditions = []
    for scenario_id, statuses in (("stable", ("failed", "failed")), ("mixed", ("failed", "passed"))):
        for index, status in enumerate(statuses, 1):
            trial = _trial(scenario_id, "normal", "baseline", index, "failed" if status == "failed" else "passed")
            trial["oracle"]["assertions"] = [{"name": "delivery", "status": status, "detail": "checked"}]
            trial["oracle"]["evidence_refs"] = [f"oracle:{scenario_id}:{index}"]
            conditions.append(trial)

    aggregate = build_evaluation_suite_aggregate(
        scenarios,
        conditions,
        condition_kinds=("baseline",),
        evaluation_type="tool_regression",
        suite_config=None,
    )

    pattern = aggregate.failure_patterns[0]
    assert pattern.failure_type == "delivery"
    assert pattern.assertion_status == "failed"
    assert pattern.frequency == 3
    assert pattern.affected_trial_count == 3
    assert pattern.affected_scenario_count == 2
    assert pattern.stability == "intermittent"
