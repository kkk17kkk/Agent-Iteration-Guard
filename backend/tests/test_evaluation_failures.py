from subprocess import TimeoutExpired

from agentguard.evaluation_failures import EvidenceIncompleteError, classify_report_failure, classify_run_failure
from agentguard.interaction_runner import OracleExecutionError, TargetExecutionError
from agentguard.provider_runtime import ProviderRuntimeError
from agentguard.targets import TargetInfrastructureError


def _wrapped(cause: Exception) -> ValueError:
    try:
        raise cause
    except Exception as error:
        try:
            raise ValueError("matrix cell failed") from error
        except ValueError as wrapped:
            return wrapped


def test_run_failure_taxonomy_uses_root_cause_without_merging_failures() -> None:
    assert classify_run_failure(_wrapped(TargetExecutionError("bad target output"))) == "target_behavior_failure"
    assert classify_run_failure(_wrapped(OracleExecutionError("oracle unavailable"))) == "oracle_failure"
    assert classify_run_failure(_wrapped(ProviderRuntimeError("provider unavailable"))) == "provider_failure"
    assert classify_run_failure(_wrapped(TargetInfrastructureError("runtime unavailable"))) == "environment_failure"
    assert classify_run_failure(_wrapped(TimeoutExpired("target", 30))) == "budget_or_timeout"
    assert classify_run_failure(EvidenceIncompleteError("missing records")) == "evidence_incomplete"
    assert classify_run_failure(ValueError("unexpected runner contract")) == "runner_failure"


def test_report_failure_never_becomes_target_or_runner_regression() -> None:
    assert classify_report_failure(ProviderRuntimeError("provider unavailable")) == "provider_failure"
    assert classify_report_failure(ValueError("analyst output missed evidence refs")) == "evidence_incomplete"
