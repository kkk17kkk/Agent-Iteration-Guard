"""Canonical AIG v1 Evaluation Run failure taxonomy."""

from __future__ import annotations

from subprocess import TimeoutExpired
from typing import Literal

from .interaction_runner import InteractionRunnerError, OracleExecutionError, TargetExecutionError
from .provider_runtime import ProviderRuntimeError
from .targets import TargetInfrastructureError


FailureClassification = Literal[
    "target_behavior_failure",
    "oracle_failure",
    "provider_failure",
    "runner_failure",
    "environment_failure",
    "budget_or_timeout",
    "evidence_incomplete",
]


class EvidenceIncompleteError(ValueError):
    """Execution finished but could not satisfy the immutable evidence contract."""


def classify_run_failure(error: BaseException) -> FailureClassification:
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, (TimeoutError, TimeoutExpired)):
        return "budget_or_timeout"
    if isinstance(cause, OracleExecutionError):
        return "oracle_failure"
    if isinstance(cause, ProviderRuntimeError):
        return "provider_failure"
    if isinstance(cause, TargetInfrastructureError):
        return "environment_failure"
    if isinstance(cause, TargetExecutionError):
        return "target_behavior_failure"
    if isinstance(cause, EvidenceIncompleteError) or isinstance(error, EvidenceIncompleteError):
        return "evidence_incomplete"
    if isinstance(cause, InteractionRunnerError):
        return "runner_failure"
    return "runner_failure"


def classify_report_failure(error: BaseException) -> FailureClassification:
    """Keep Analyst/provider failures out of target and runner regressions."""

    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    return "provider_failure" if isinstance(cause, ProviderRuntimeError) else "evidence_incomplete"


__all__ = [
    "EvidenceIncompleteError",
    "FailureClassification",
    "classify_report_failure",
    "classify_run_failure",
]
