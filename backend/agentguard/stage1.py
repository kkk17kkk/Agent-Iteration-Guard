"""Independent, deterministic meta-evaluation primitives for Stage 1.

The router receives only ``Stage1Case``.  Expected outcomes live in the
separate ``Stage1GroundTruth`` records and are consumed only after all raw
results have been saved.  This keeps labels out of the execution path.
"""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from .store import Store


Split = Literal["development", "validation", "hidden"]
Severity = Literal["none", "normal", "severe"]


class Stage1Case(BaseModel):
    case_id: str
    split: Split
    mutation_kinds: tuple[str, ...]
    mutation_position: Literal["planning", "execution", "verification"]
    required_case_ids: tuple[str, ...]
    actual_failure_case_ids: tuple[str, ...] = ()
    cost_usd: float = Field(default=0.0, ge=0)


class Stage1GroundTruth(BaseModel):
    """Evaluation-only labels; never passed to selection or execution."""

    case_id: str
    regression: bool
    severity: Severity
    required_case_ids: tuple[str, ...]


class Stage1RawResult(BaseModel):
    case_id: str
    split: Split
    selected_case_ids: tuple[str, ...]
    full_case_ids: tuple[str, ...]
    observed_failure_case_ids: tuple[str, ...]
    release_status: Literal["ready", "blocked"]
    cost_usd: float = Field(ge=0)


class Stage1Metrics(BaseModel):
    sample_count: int
    regression_precision: float
    regression_recall: float
    regression_f1: float
    severe_regression_recall: float
    false_block_rate: float
    false_ready_rate: float
    selection_precision: float
    selection_recall: float
    selection_reduction: float
    total_cost_usd: float
    severe_miss_case_ids: list[str]
    false_block_case_ids: list[str]


def build_stage1_corpus() -> tuple[list[Stage1Case], list[Stage1GroundTruth]]:
    """Return a deterministic corpus with structural hidden-set separation."""
    rows = [
        ("dev-skill-normal", "development", ("skill",), "planning", ("normal_write",), (), False, "none"),
        ("dev-permission-severe", "development", ("permission",), "execution", ("security",), ("security",), True, "severe"),
        ("dev-workflow-normal", "development", ("workflow",), "verification", ("smoke",), (), False, "none"),
        ("val-tool-severe", "validation", ("tool_schema",), "execution", ("security",), ("security",), True, "severe"),
        ("val-prompt-normal", "validation", ("prompt",), "planning", ("normal_write",), (), False, "none"),
        # Hidden cases use unseen positions and combination mutations, not field substitutions.
        ("hidden-skill-permission", "hidden", ("skill", "permission"), "verification", ("normal_write", "security"), ("security",), True, "severe"),
        ("hidden-tool-workflow", "hidden", ("tool_schema", "workflow"), "planning", ("smoke", "security"), ("security",), True, "normal"),
        ("hidden-benign-combination", "hidden", ("prompt", "skill"), "execution", ("normal_write",), (), False, "none"),
    ]
    cases = [
        Stage1Case(
            case_id=case_id,
            split=split,
            mutation_kinds=kinds,
            mutation_position=position,
            required_case_ids=required,
            actual_failure_case_ids=failures,
        )
        for case_id, split, kinds, position, required, failures, _, _ in rows
    ]
    truth = [
        Stage1GroundTruth(case_id=case_id, regression=regression, severity=severity, required_case_ids=required)
        for case_id, _, _, _, required, _, regression, severity in rows
    ]
    return cases, truth


def select_cases(case: Stage1Case) -> tuple[str, ...]:
    """A deterministic router deliberately independent of Ground Truth."""
    selected = {"smoke"}
    kinds = set(case.mutation_kinds)
    if kinds & {"skill", "prompt"}:
        selected.add("normal_write")
    if kinds & {"permission", "tool_schema", "workflow"}:
        selected.add("security")
    return tuple(sorted(selected))


def execute_case(case: Stage1Case) -> Stage1RawResult:
    """Evaluate selected checks against observed candidate behavior, not labels."""
    selected = select_cases(case)
    full = ("smoke", "normal_write", "security")
    observed = tuple(failure for failure in case.actual_failure_case_ids if failure in selected)
    return Stage1RawResult(
        case_id=case.case_id,
        split=case.split,
        selected_case_ids=selected,
        full_case_ids=full,
        observed_failure_case_ids=observed,
        release_status="blocked" if observed else "ready",
        cost_usd=case.cost_usd,
    )


def compute_metrics(raw_results: list[Stage1RawResult], truth_records: list[Stage1GroundTruth]) -> Stage1Metrics:
    """Recompute all report values from persisted raw results and truth records."""
    truth_by_id = {record.case_id: record for record in truth_records}
    if len(raw_results) != len(truth_by_id) or {raw.case_id for raw in raw_results} != set(truth_by_id):
        raise ValueError("Raw results and Ground Truth must have exactly the same case IDs.")

    predicted = {raw.case_id: raw.release_status == "blocked" for raw in raw_results}
    actual = {case_id: record.regression for case_id, record in truth_by_id.items()}
    tp = sum(predicted[key] and actual[key] for key in actual)
    fp = sum(predicted[key] and not actual[key] for key in actual)
    fn = sum(not predicted[key] and actual[key] for key in actual)
    tn = sum(not predicted[key] and not actual[key] for key in actual)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    severe = [record for record in truth_records if record.severity == "severe"]
    severe_misses = [record.case_id for record in severe if not predicted[record.case_id]]

    selected = Counter(case for raw in raw_results for case in raw.selected_case_ids)
    required = Counter(case for truth in truth_records for case in truth.required_case_ids)
    selected_required = sum(min(selected[key], required[key]) for key in required)
    selection_precision = selected_required / sum(selected.values()) if selected else 1.0
    selection_recall = selected_required / sum(required.values()) if required else 1.0
    full_count = sum(len(raw.full_case_ids) for raw in raw_results)
    selection_reduction = 1 - sum(selected.values()) / full_count if full_count else 0.0
    false_blocks = [case_id for case_id in actual if predicted[case_id] and not actual[case_id]]
    return Stage1Metrics(
        sample_count=len(raw_results),
        regression_precision=precision,
        regression_recall=recall,
        regression_f1=f1,
        severe_regression_recall=(len(severe) - len(severe_misses)) / len(severe) if severe else 1.0,
        false_block_rate=fp / (fp + tn) if fp + tn else 0.0,
        false_ready_rate=fn / (fn + tp) if fn + tp else 0.0,
        selection_precision=selection_precision,
        selection_recall=selection_recall,
        selection_reduction=selection_reduction,
        total_cost_usd=sum(raw.cost_usd for raw in raw_results),
        severe_miss_case_ids=severe_misses,
        false_block_case_ids=false_blocks,
    )


def persist_corpus_run(store: Store, product_id: str) -> Stage1Metrics:
    """Persist execution inputs/results before calculating the report metrics."""
    cases, truth = build_stage1_corpus()
    raw = [execute_case(case) for case in cases]
    store.save_many([
        *[("stage1_case", f"stage1_case__{case.case_id}", product_id, case) for case in cases],
        *[("stage1_ground_truth", f"stage1_truth__{truth_record.case_id}", product_id, truth_record) for truth_record in truth],
        *[("stage1_raw_result", f"stage1_raw__{result.case_id}", product_id, result) for result in raw],
    ])
    saved_raw = store.list("stage1_raw_result", Stage1RawResult, product_id)
    saved_truth = store.list("stage1_ground_truth", Stage1GroundTruth, product_id)
    metrics = compute_metrics(saved_raw, saved_truth)
    store.save("stage1_metrics", "stage1_metrics", product_id, metrics)
    return metrics
