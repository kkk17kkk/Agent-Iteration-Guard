from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


RiskLevel = Literal["low", "medium", "high", "critical"]
OracleKind = Literal["state_assertion", "tool_trace", "test", "path_policy", "ticket_policy"]
HarnessStatus = Literal[
    "created", "planning", "planned", "running", "verifying", "deciding",
    "awaiting_evidence", "blocked", "recorded", "failed",
]
AgentRole = Literal["intake", "planner", "runner", "executor", "verifier", "gatekeeper"]
HandoffKind = Literal["evaluation_scope", "evaluation_plan", "evidence_request", "release_hold", "gate_block"]
EvidenceLevel = Literal["verified", "supported", "inferred", "unresolved"]
ChangeKind = Literal["permission_changed", "tool_capability_expanded", "skill_changed", "prompt_changed", "workflow_changed"]
FailureType = Literal[
    "permission_violation", "duplicate_side_effect", "invalid_state_transition",
    "missing_required_comment", "wrong_owner", "approval_bypass",
]
LLMAssistanceKind = Literal["failure_explanation", "requirement_mapping"]
WorkStatus = Literal["planned", "completed", "blocked"]
RunEventType = Literal[
    "RUN_CREATED", "PLAN_CREATED", "TRIALS_COMPLETED", "VERIFICATION_COMPLETED",
    "FINDING_CREATED", "RELEASE_DECIDED", "RUN_RECORDED", "LLM_ASSISTANCE_RECORDED",
    "CHECKPOINT_COMMITTED", "OPERATION_STARTED", "OPERATION_COMPLETED", "FAILURE_TICKET_CREATED",
    "TRIAL_STARTED", "TRIAL_COMPLETED", "METRICS_RECORDED", "REPLAY_RECORDED", "ABLATION_RECORDED",
    "REPLAN_RECORDED", "ENVIRONMENT_CAPTURE_RECORDED",
    "BATCH_CREATED", "BATCH_ITEM_COMPLETED", "BATCH_CHECKPOINT_COMMITTED", "BATCH_RECORDED",
    "ACTION_PLANNED", "ACTION_COMPLETED", "OBSERVATION_RECORDED", "STAGE2_CHECKPOINT_COMMITTED", "STAGE2_MODEL_RESPONSE_REJECTED",
]
MutationKind = Literal["prompt", "skill", "tool_schema", "permission", "workflow", "retry_idempotency"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ident(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Product(BaseModel):
    product_id: str = Field(default_factory=lambda: ident("product"))
    name: str = Field(min_length=1)
    description: str = ""
    current_version_id: str | None = None
    created_at: str = Field(default_factory=now)


class Version(BaseModel):
    version_id: str = Field(default_factory=lambda: ident("version"))
    product_id: str
    label: str = Field(min_length=1)
    source_ref: str = "manual"
    created_at: str = Field(default_factory=now)


class FileAgentManifest(BaseModel):
    agent_name: str
    skill: str
    requested_write_paths: list[str]
    tool_capabilities: list[str]
    instructions: str = ""
    cleanup_temporary_files: bool = False


TicketFault = Literal[
    "duplicate_create", "illegal_close", "unauthorized_assign", "missing_comment",
    "wrong_owner", "missing_transition", "retry_duplicate_comment", "skip_approval",
]


class TicketAgentManifest(BaseModel):
    agent_name: str
    skill: str
    tool_capabilities: list[str]
    allowed_assignees: list[str] = Field(default_factory=lambda: ["primary-owner", "secondary-owner"])
    required_comment: str = "triaged"
    faults: list[TicketFault] = Field(default_factory=list)


class ComponentSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: ident("snapshot"))
    product_id: str
    version_id: str
    component_type: str = "file_agent_manifest"
    name: str = "agent_manifest.json"
    fingerprint: str
    source_ref: str
    manifest: FileAgentManifest | TicketAgentManifest
    created_at: str = Field(default_factory=now)


class Change(BaseModel):
    change_id: str = Field(default_factory=lambda: ident("change"))
    kind: ChangeKind
    risk: RiskLevel
    before: list[str] | str
    after: list[str] | str


class ChangeSet(BaseModel):
    changeset_id: str = Field(default_factory=lambda: ident("changeset"))
    product_id: str
    baseline_version_id: str
    candidate_version_id: str
    baseline_snapshot: ComponentSnapshot
    candidate_snapshot: ComponentSnapshot
    changes: list[Change]
    created_at: str = Field(default_factory=now)


class Requirement(BaseModel):
    requirement_id: str = Field(default_factory=lambda: ident("req"))
    product_id: str
    title: str
    description: str = ""
    risk: RiskLevel = "medium"


class Capability(BaseModel):
    capability_id: str = Field(default_factory=lambda: ident("cap"))
    product_id: str
    name: str
    requirement_ids: list[str] = Field(default_factory=list)
    risk: RiskLevel = "medium"


class EvalCase(BaseModel):
    eval_case_id: str = Field(default_factory=lambda: ident("eval"))
    product_id: str
    name: str
    capability_ids: list[str] = Field(default_factory=list)
    oracle_kind: OracleKind = "state_assertion"


class EvalPlanItem(BaseModel):
    eval_case_id: str
    selected: bool
    reason: str
    risk: RiskLevel
    oracle_kind: OracleKind


class EvalPlan(BaseModel):
    eval_plan_id: str = Field(default_factory=lambda: ident("plan"))
    product_id: str
    changeset_id: str
    items: list[EvalPlanItem]
    created_at: str = Field(default_factory=now)

    @property
    def selected_case_ids(self) -> list[str]:
        return [item.eval_case_id for item in self.items if item.selected]


class WorkItem(BaseModel):
    work_item_id: str = Field(default_factory=lambda: ident("work"))
    harness_run_id: str
    eval_case_id: str
    owner: Literal["runner"] = "runner"
    objective: str
    input_artifact_ids: list[str]
    expected_output_type: Literal["execution_result", "environment_capture"] = "execution_result"
    acceptance_criteria: str
    allowed_tools: list[str] = Field(default_factory=lambda: ["write_file"])
    status: WorkStatus = "planned"


ReplanTrigger = Literal[
    "incomplete_trace", "unstable_results", "permission_regression",
    "runner_environment_failure", "replay_not_reproduced",
]
ReplanTerminalReason = Literal["applied", "budget_exhausted", "runner_blocked", "unresolved", "no_change"]


class ReplanBudget(BaseModel):
    additional_trial_limit: int = Field(ge=0)
    additional_trial_used: int = Field(default=0, ge=0)
    additional_cost_limit_usd: float = Field(default=0.0, ge=0)
    additional_cost_used_usd: float = Field(default=0.0, ge=0)


class ReplanRecord(BaseModel):
    replan_id: str = Field(default_factory=lambda: ident("replan"))
    harness_run_id: str
    trigger: ReplanTrigger
    before_plan: EvalPlan
    after_plan: EvalPlan
    added_work_item_ids: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    budget_before: ReplanBudget
    budget_after: ReplanBudget
    risk_escalated: bool = False
    terminal_reason: ReplanTerminalReason
    created_at: str = Field(default_factory=now)


class EnvironmentCapture(BaseModel):
    capture_id: str = Field(default_factory=lambda: ident("environment"))
    harness_run_id: str
    replan_id: str
    environment_fingerprint: str
    policy_fingerprint: str
    runner_ref: str
    reason: str
    created_at: str = Field(default_factory=now)


class ToolCall(BaseModel):
    tool_name: Literal[
        "read_file", "write_file", "delete_file", "create_ticket", "add_comment",
        "assign_ticket", "start_ticket", "approve_ticket", "close_ticket", "read_ticket",
    ] = "write_file"
    path: str
    policy_decision: Literal["allowed", "denied", "unauthorized"]
    arguments_hash: str = ""
    side_effect_class: Literal["read", "write", "delete", "create", "update", "comment", "transition", "approval"] = "write"


class ExecutionResult(BaseModel):
    execution_id: str = Field(default_factory=lambda: ident("execution"))
    harness_run_id: str
    work_item_id: str
    status: Literal["completed", "runner_failed"] = "completed"
    tool_calls: list[ToolCall]
    environment_ref: str = "fake-file-agent-v1"
    operation_id: str | None = None
    output_fingerprint: str | None = None
    runner_trace_id: str | None = None
    external_cost_usd: float = Field(default=0.0, ge=0)
    state: dict[str, object] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)


class VerificationResult(BaseModel):
    verification_id: str = Field(default_factory=lambda: ident("verification"))
    harness_run_id: str
    execution_id: str
    oracle_id: Literal["path_policy", "ticket_policy"] = "path_policy"
    expected: str
    observed: str
    passed: bool
    severity: RiskLevel = "low"
    failure_class: str | None = None
    failure_type: FailureType | None = None
    created_at: str = Field(default_factory=now)


class HarnessRun(BaseModel):
    harness_run_id: str = Field(default_factory=lambda: ident("harness"))
    product_id: str
    version_id: str
    baseline_version_id: str | None = None
    candidate_version_id: str | None = None
    changeset_id: str | None = None
    thread_id: str | None = None
    eval_case_ids: list[str] = Field(default_factory=list)
    status: HarnessStatus = "created"
    blocked_reason: str | None = None
    created_at: str = Field(default_factory=now)


class RunEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: ident("event"))
    harness_run_id: str
    sequence: int
    event_type: RunEventType
    artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class Handoff(BaseModel):
    handoff_id: str = Field(default_factory=lambda: ident("handoff"))
    harness_run_id: str
    from_role: AgentRole
    to_role: AgentRole
    kind: HandoffKind
    summary: str
    eval_case_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: ident("evidence"))
    harness_run_id: str
    eval_case_id: str | None = None
    source: Literal["runner", "oracle", "verifier"]
    level: EvidenceLevel
    summary: str
    execution_id: str | None = None
    verification_id: str | None = None
    created_at: str = Field(default_factory=now)


class Finding(BaseModel):
    finding_id: str = Field(default_factory=lambda: ident("finding"))
    product_id: str
    harness_run_id: str
    title: str
    evidence_level: EvidenceLevel = "unresolved"
    evidence_ids: list[str] = Field(default_factory=list)
    severity: RiskLevel = "medium"


class ReleaseDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: ident("decision"))
    product_id: str
    version_id: str
    harness_run_id: str
    status: Literal["pending", "ready", "blocked"] = "pending"
    rationale: str
    finding_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class RunCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: ident("checkpoint"))
    harness_run_id: str
    next_step: Literal["plan", "execute", "verify", "gate", "record", "completed"]
    event_sequence: int
    created_at: str = Field(default_factory=now)


class Operation(BaseModel):
    operation_id: str
    harness_run_id: str
    work_item_id: str
    input_hash: str
    status: Literal["running", "completed", "interrupted"] = "running"
    execution_id: str | None = None
    tool_call_count: int = 0
    created_at: str = Field(default_factory=now)


class ToolPolicy(BaseModel):
    policy_id: str = Field(default_factory=lambda: ident("policy"))
    product_id: str
    harness_run_id: str
    allowed_read_paths: list[str] = Field(default_factory=list)
    allowed_write_paths: list[str] = Field(default_factory=list)
    allow_delete: bool = False
    allowed_actions: list[str] = Field(default_factory=list)
    constraints: dict[str, object] = Field(default_factory=dict)
    sandbox_kind: Literal["temporary_directory", "in_memory_ticket"] = "temporary_directory"
    created_at: str = Field(default_factory=now)


Stage2ActionKind = Literal["read_file", "write_file", "delete_file", "finish"]
Stage2ActionStatus = Literal["planned", "running", "completed", "blocked", "failed"]
Stage2RunStatus = Literal["created", "running", "blocked", "finished", "failed", "budget_exhausted"]


class AgentAction(BaseModel):
    """The only command a Stage 2 model may send to the Harness."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=lambda: ident("action"))
    agent_run_id: str
    step: int = Field(ge=1)
    kind: Stage2ActionKind
    path: str | None = None
    content: str | None = None
    expected_observation_fingerprint: str | None = None
    approval_required: bool = False
    approval_token: str | None = None
    status: Stage2ActionStatus = "planned"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    result: str | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now)


class AgentObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: ident("observation"))
    agent_run_id: str
    step: int = Field(ge=0)
    state_fingerprint: str
    files: dict[str, str] = Field(default_factory=dict)
    changed_paths: list[str] = Field(default_factory=list)
    last_action_id: str | None = None
    last_action_kind: Stage2ActionKind | None = None
    tool_result: str | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now)


class Stage2AgentRun(BaseModel):
    agent_run_id: str = Field(default_factory=lambda: ident("stage2"))
    product_id: str
    harness_run_id: str
    work_item_id: str
    stage1_batch_id: str
    task_kind: str
    fixture_variant: Literal["default", "needs_update", "already_satisfied"] = "default"
    task: dict[str, object] = Field(default_factory=dict)
    tool_manifest: dict[str, object] = Field(default_factory=dict)
    policy_id: str
    sandbox_path: str
    model_kind: Literal["deterministic", "fake", "json", "http_json", "real_llm", "deepseek_tools", "trace_replay"] = "deterministic"
    model_provider: str | None = None
    runtime_batch_id: str | None = None
    status: Stage2RunStatus = "created"
    step_count: int = Field(default=0, ge=0)
    max_steps: int = Field(default=8, ge=1, le=32)
    action_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    checkpoint_id: str | None = None
    terminal_reason: str | None = None
    duplicate_side_effect_count: int = Field(default=0, ge=0)
    resumed_from_checkpoint: bool = False
    retry_mode: Literal["stable_operation_id", "regenerate_operation_id"] = "stable_operation_id"
    retry_mutation_applied: bool = False
    created_at: str = Field(default_factory=now)


class Stage2ModelCall(BaseModel):
    """Auditable evidence for one action proposal at the model boundary."""

    model_call_id: str = Field(default_factory=lambda: ident("stage2_model_call"))
    agent_run_id: str
    step: int = Field(ge=1)
    action_id: str | None = None
    model_kind: Literal["deterministic", "fake", "json", "http_json", "real_llm", "deepseek_tools", "trace_replay"]
    provider: str
    model: str | None = None
    provider_request_id: str | None = None
    observation_fingerprint: str
    prompt_fingerprint: str | None = None
    response_fingerprint: str | None = None
    native_tool_call_id: str | None = None
    finish_reason: str | None = None
    provider_usage_id: str | None = None
    outcome: Literal["accepted", "invalid_response", "provider_error"] = "accepted"
    error: str | None = None
    created_at: str = Field(default_factory=now)


class Stage2RuntimeBatch(BaseModel):
    runtime_batch_id: str = Field(default_factory=lambda: ident("stage2_runtime_batch"))
    stage1_batch_id: str
    product_id: str
    model: str
    budget_limit_usd: float = Field(gt=0)
    max_steps_per_run: int = Field(ge=1, le=32)
    agent_run_ids: list[str] = Field(default_factory=list)
    status: Literal["running", "completed", "blocked", "budget_exhausted", "failed"] = "running"
    created_at: str = Field(default_factory=now)


class Stage2RuntimeBudgetGate(BaseModel):
    runtime_batch_id: str
    status: Literal["PASS", "BLOCKED"]
    budget_limit_usd: float = Field(ge=0)
    observed_cost_usd: float = Field(ge=0)
    provider_usage_ids: list[str] = Field(default_factory=list)
    native_trace_call_ids: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    created_at: str = Field(default_factory=now)


class Stage2ReliabilityCorpus(BaseModel):
    """A persisted, runtime-side-effect corpus; not a synthetic Oracle fixture."""

    corpus_id: str = Field(default_factory=lambda: ident("stage2_reliability_corpus"))
    stage1_batch_id: str
    product_id: str
    mutation_kind: Literal["retry_idempotency"] = "retry_idempotency"
    model_kind: Literal["deterministic", "deepseek_tools"]
    trial_count: int = Field(ge=3)
    runtime_batch_id: str | None = None
    trial_ids: list[str] = Field(default_factory=list)
    replay_id: str | None = None
    ablation_id: str | None = None
    status: Literal["running", "completed", "blocked"] = "running"
    created_at: str = Field(default_factory=now)


class Stage2ReliabilityTrial(BaseModel):
    trial_id: str = Field(default_factory=lambda: ident("stage2_reliability_trial"))
    corpus_id: str
    ordinal: int = Field(ge=1)
    retry_mode: Literal["stable_operation_id", "regenerate_operation_id"]
    agent_run_id: str
    crash_boundary: Literal["after_side_effect_before_commit"] = "after_side_effect_before_commit"
    trace_fingerprint: str
    operation_ids: list[str] = Field(default_factory=list)
    duplicate_side_effect_count: int = Field(ge=0)
    oracle_passed: bool
    release_status: Literal["ready", "blocked"]
    created_at: str = Field(default_factory=now)


class Stage2ReliabilityReplay(BaseModel):
    replay_id: str = Field(default_factory=lambda: ident("stage2_reliability_replay"))
    corpus_id: str
    kind: Literal["replay", "ablation"]
    source_trial_id: str
    agent_run_id: str
    retry_mode: Literal["stable_operation_id", "regenerate_operation_id"]
    source_trace_fingerprint: str
    replay_trace_fingerprint: str
    trace_matches: bool
    duplicate_side_effect_count: int = Field(ge=0)
    oracle_passed: bool
    release_status: Literal["ready", "blocked"]
    created_at: str = Field(default_factory=now)


class Stage2ReliabilityGate(BaseModel):
    corpus_id: str
    status: Literal["PASS", "PASS_WITH_LIMITATIONS", "BLOCKED"]
    baseline_trial_ids: list[str]
    candidate_trial_ids: list[str]
    replay_id: str | None = None
    ablation_id: str | None = None
    criteria: list[dict[str, object]] = Field(default_factory=list)
    limitation: str | None = None
    created_at: str = Field(default_factory=now)


class Stage2Checkpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: ident("stage2_checkpoint"))
    agent_run_id: str
    next_step: int = Field(default=1, ge=1)
    pending_action_id: str | None = None
    observation_id: str | None = None
    committed_action_ids: list[str] = Field(default_factory=list)
    state_fingerprint: str
    created_at: str = Field(default_factory=now)


class Stage2Operation(BaseModel):
    operation_id: str = Field(default_factory=lambda: ident("stage2_operation"))
    agent_run_id: str
    action_id: str
    side_effect_fingerprint: str = ""
    status: Literal["running", "completed"] = "running"
    side_effect_applied: bool = False
    duplicate: bool = False
    created_at: str = Field(default_factory=now)


class Stage2OracleResult(BaseModel):
    oracle_result_id: str = Field(default_factory=lambda: ident("stage2_oracle"))
    agent_run_id: str
    passed: bool
    expected: str
    observed: str
    action_order: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    stale_observation_rejected: bool = True
    created_at: str = Field(default_factory=now)


class Stage2GateCriterion(BaseModel):
    criterion: str
    status: Literal["verified", "partial", "failed", "missing"]
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    supporting_test: str
    failure_reason: str | None = None


class Stage2Gate(BaseModel):
    stage1_batch_id: str
    status: Literal["PASS", "PASS_WITH_LIMITATIONS", "BLOCKED"]
    deterministic_harness_status: Literal["PASS", "BLOCKED"] = "BLOCKED"
    real_llm_integration_status: Literal["verified", "missing", "failed"] = "missing"
    real_llm_case_ids: list[str] = Field(default_factory=list)
    criteria: list[Stage2GateCriterion] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class FailureTicket(BaseModel):
    ticket_id: str = Field(default_factory=lambda: ident("ticket"))
    product_id: str
    harness_run_id: str
    finding_id: str
    evidence_ids: list[str]
    title: str
    reproduction: str
    recommended_action: str
    created_at: str = Field(default_factory=now)


class TrialSpec(BaseModel):
    trial_id: str = Field(default_factory=lambda: ident("trial"))
    harness_run_id: str
    work_item_id: str
    ordinal: int = Field(ge=1)
    kind: Literal["evaluation", "replay", "ablation", "instrumentation", "safety"] = "evaluation"
    cleanup_attempt: bool | None
    decision_source: Literal["fixture", "external_model"] = "fixture"
    candidate_fingerprint: str
    policy_fingerprint: str
    environment_fingerprint: str
    seed: int
    created_at: str = Field(default_factory=now)


class TrialResult(BaseModel):
    trial_result_id: str = Field(default_factory=lambda: ident("trial_result"))
    harness_run_id: str
    trial_id: str
    kind: Literal["evaluation", "replay", "ablation", "instrumentation", "safety"] = "evaluation"
    execution_id: str
    verification_id: str
    evidence_id: str
    passed: bool
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)
    trace_fingerprint: str
    created_at: str = Field(default_factory=now)


class TrialMetrics(BaseModel):
    metrics_id: str = Field(default_factory=lambda: ident("metrics"))
    harness_run_id: str
    trial_result_ids: list[str]
    trial_count: int = Field(ge=1)
    success_rate: float = Field(ge=0, le=1)
    variance: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    created_at: str = Field(default_factory=now)


class ReplaySpec(BaseModel):
    replay_spec_id: str = Field(default_factory=lambda: ident("replay"))
    source_trial_result_id: str
    harness_run_id: str
    candidate_fingerprint: str
    policy_fingerprint: str
    environment_fingerprint: str
    cleanup_attempt: bool
    seed: int
    source_trace_fingerprint: str
    created_at: str = Field(default_factory=now)


class ReplayResult(BaseModel):
    replay_result_id: str = Field(default_factory=lambda: ident("replay_result"))
    replay_spec_id: str
    execution_id: str
    verification_id: str
    trace_fingerprint: str
    reproduced: bool
    created_at: str = Field(default_factory=now)


class AblationReport(BaseModel):
    ablation_id: str = Field(default_factory=lambda: ident("ablation"))
    source_trial_result_id: str
    harness_run_id: str
    changed_field: Literal["cleanup_attempt"] = "cleanup_attempt"
    before_value: bool
    after_value: bool
    before_verification_id: str
    after_verification_id: str
    evidence_delta: str
    created_at: str = Field(default_factory=now)


class MutationPair(BaseModel):
    pair_id: str = Field(default_factory=lambda: ident("mutation"))
    product_id: str
    mutation_kind: MutationKind
    ordinal: int = Field(ge=1)
    baseline_version_id: str
    candidate_version_id: str
    expected_failure_type: FailureType | None = None
    expected_release: Literal["ready", "blocked"]
    valid: bool = True
    rejection_reason: str | None = None
    created_at: str = Field(default_factory=now)


class BatchRun(BaseModel):
    batch_id: str = Field(default_factory=lambda: ident("batch"))
    product_id: str
    pair_ids: list[str]
    trials_per_pair: int = Field(ge=3)
    max_workers: int = Field(ge=1, le=8)
    max_total_cost_usd: float = Field(ge=0)
    status: Literal["created", "running", "interrupted", "completed", "failed"] = "created"
    next_pair_index: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=now)


class BatchItem(BaseModel):
    batch_item_id: str = Field(default_factory=lambda: ident("batch_item"))
    batch_id: str
    pair_id: str
    ordinal: int = Field(ge=1)
    cache_key: str
    status: Literal["pending", "running", "completed", "cached", "failed"] = "pending"
    harness_run_id: str | None = None
    created_at: str = Field(default_factory=now)


class BatchCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: ident("batch_checkpoint"))
    batch_id: str
    next_pair_index: int = Field(ge=0)
    completed_item_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class TrialCacheEntry(BaseModel):
    cache_key: str
    harness_run_id: str
    candidate_fingerprint: str
    policy_fingerprint: str
    environment_fingerprint: str
    trials_per_pair: int = Field(ge=3)
    created_at: str = Field(default_factory=now)


class ProviderUsage(BaseModel):
    usage_id: str = Field(default_factory=lambda: ident("usage"))
    harness_run_id: str
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    input_cache_write_tokens: int = Field(default=0, ge=0)
    input_cache_read_tokens: int = Field(default=0, ge=0)
    input_price_per_million_usd: float = Field(ge=0)
    output_price_per_million_usd: float = Field(ge=0)
    input_cache_write_price_per_million_usd: float = Field(ge=0)
    input_cache_read_price_per_million_usd: float = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    pricing_source: str = Field(min_length=1)
    budget_limit_usd: float = Field(ge=0)
    source: Literal["inspect_eval_log", "provider_response"]
    created_at: str = Field(default_factory=now)


class RunnerTrace(BaseModel):
    runner_trace_id: str = Field(default_factory=lambda: ident("runner_trace"))
    harness_run_id: str
    runner: Literal["inspect_ai"]
    provider: str
    model: str
    inspect_log_location: str = Field(min_length=1)
    output_sha256: str = Field(min_length=1)
    selected_cleanup_attempt: bool | None = None
    created_at: str = Field(default_factory=now)


class RunnerFailure(BaseModel):
    runner_failure_id: str = Field(default_factory=lambda: ident("runner_failure"))
    harness_run_id: str
    runner: Literal["inspect_ai", "local"]
    category: Literal["provider", "budget", "contract", "environment"]
    reason: str = Field(min_length=1)
    created_at: str = Field(default_factory=now)


class ModelDecision(BaseModel):
    model_decision_id: str
    harness_run_id: str
    work_item_id: str
    candidate_fingerprint: str
    status: Literal["running", "completed"] = "running"
    cleanup_attempt: bool | None = None
    provider_usage_id: str | None = None
    runner_trace_id: str | None = None
    created_at: str = Field(default_factory=now)


class FailureExplanation(BaseModel):
    failure_type: FailureType
    explanation: str = Field(min_length=1)
    suspected_change_ids: list[str] = Field(default_factory=list)
    limitation: str = Field(min_length=1)


class RequirementMappingSuggestion(BaseModel):
    requirement_id: str
    candidate_capability: str = Field(min_length=1)
    impacted_change_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]


class LLMAssistance(BaseModel):
    assistance_id: str = Field(default_factory=lambda: ident("assist"))
    product_id: str
    kind: LLMAssistanceKind
    harness_run_id: str | None = None
    input_artifact_ids: list[str]
    evidence_level: Literal["inferred"] = "inferred"
    provider: str
    model: str
    provider_request_id: str
    prompt_version: str
    output: FailureExplanation | RequirementMappingSuggestion
    created_at: str = Field(default_factory=now)


# Stage 6A real-agent evolution records.  These are deliberately separate from
# the controlled fixture Version/ChangeSet records above: a Git revision is not
# evidence that a product contract, runtime, or verifier stayed equivalent.
EvolutionEvidenceStatus = Literal["eligible", "eligible_with_gaps", "deferred", "rejected", "not_observed"]
MemoryStatus = Literal["candidate", "verified", "stale", "superseded"]
MemoryKind = Literal["fact", "event", "experience", "working"]


class AgentSource(BaseModel):
    source_id: str = Field(default_factory=lambda: ident("agent_source"))
    project_id: str
    repository_url: str = Field(min_length=1)
    source_kind: Literal["git"] = "git"
    license_ref: str | None = None
    provenance_note: str = "local read-only intake"
    created_at: str = Field(default_factory=now)


class AgentRevision(BaseModel):
    revision_id: str = Field(default_factory=lambda: ident("agent_revision"))
    project_id: str
    source_id: str
    commit_sha: str = Field(min_length=7)
    tree_sha: str = Field(min_length=7)
    manifest_sha256: str = Field(min_length=16)
    declared_entrypoint: str | None = None
    lock_files: list[str] = Field(default_factory=list)
    source_mode: Literal["live", "frozen", "offline", "simulated"] = "frozen"
    behavior_mode: Literal["production_parity", "reconstruction"] = "reconstruction"
    created_at: str = Field(default_factory=now)


class EvolutionFileChange(BaseModel):
    change_id: str = Field(default_factory=lambda: ident("evolution_change"))
    path: str = Field(min_length=1)
    status: Literal["added", "modified", "deleted", "renamed", "unknown"]
    review_status: Literal["review_required"] = "review_required"
    evidence_ref: str = Field(min_length=1)


class EvolutionChangeSet(BaseModel):
    evolution_changeset_id: str = Field(default_factory=lambda: ident("evolution_changeset"))
    project_id: str
    baseline_revision_id: str
    candidate_revision_id: str
    diff_sha256: str = Field(min_length=16)
    changes: list[EvolutionFileChange] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class ReproductionContract(BaseModel):
    reproduction_contract_id: str = Field(default_factory=lambda: ident("reproduction_contract"))
    project_id: str
    revision_id: str
    entrypoint: str | None = None
    reproduction_command: str | None = None
    environment_mode: Literal["live", "frozen", "offline", "simulated"] = "frozen"
    reset_strategy: str | None = None
    required_credential_names: list[str] = Field(default_factory=list)
    status: Literal["incomplete", "review_required", "approved"] = "incomplete"
    gaps: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class RuntimeParityAssessment(BaseModel):
    parity_assessment_id: str = Field(default_factory=lambda: ident("runtime_parity"))
    project_id: str
    revision_id: str
    status: Literal["unassessed", "preflight_ready", "environment_parity_blocked", "reconstruction", "production_parity"]
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class ProviderBinding(BaseModel):
    provider_binding_id: str = Field(default_factory=lambda: ident("provider_binding"))
    project_id: str
    role: Literal["control_plane", "sut_native"]
    provider: str = Field(min_length=1)
    base_url: str | None = None
    model: str = Field(min_length=1)
    expected_environment_variable: str = Field(min_length=1)
    credential_source_ref: str = Field(min_length=1)
    batch_budget_usd: float = Field(ge=0)
    timeout_seconds: int = Field(gt=0)
    allowed_hosts: list[str] = Field(default_factory=list)
    data_retention_policy: str = Field(min_length=1)
    created_at: str = Field(default_factory=now)


class AgentEvolutionCase(BaseModel):
    evolution_case_id: str = Field(default_factory=lambda: ident("evolution_case"))
    project_id: str
    source_id: str
    baseline_revision_id: str
    candidate_revision_id: str
    evolution_changeset_id: str
    status: Literal["intake_complete", "awaiting_approval", "approved", "blocked", "completed"] = "intake_complete"
    created_at: str = Field(default_factory=now)


class EvolutionPairPlan(BaseModel):
    evolution_pair_plan_id: str = Field(default_factory=lambda: ident("evolution_plan"))
    project_id: str
    evolution_case_id: str
    status: Literal["draft", "awaiting_approval", "approved", "blocked"] = "draft"
    required_contract_ids: list[str] = Field(default_factory=list)
    provider_binding_id: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class EvolutionComparison(BaseModel):
    evolution_comparison_id: str = Field(default_factory=lambda: ident("evolution_comparison"))
    project_id: str
    evolution_case_id: str
    status: Literal["not_run", "awaiting_evidence", "blocked", "compared"] = "not_run"
    conclusion: str = "No live comparison has been run."
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class ProductContractRevision(BaseModel):
    product_contract_revision_id: str = Field(default_factory=lambda: ident("product_contract"))
    project_id: str
    applicable_revision_ids: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    status: MemoryStatus = "candidate"
    created_at: str = Field(default_factory=now)


class MemoryEntry(BaseModel):
    memory_id: str = Field(default_factory=lambda: ident("memory"))
    project_id: str
    kind: MemoryKind
    content: str = Field(min_length=1)
    evidence_level: EvidenceLevel
    evidence_refs: list[str] = Field(default_factory=list)
    applicable_revision_ids: list[str] = Field(default_factory=list)
    invalidated_by: list[str] = Field(default_factory=list)
    status: MemoryStatus = "candidate"
    recorded_by: Literal["human", "system", "llm"] = "human"
    created_at: str = Field(default_factory=now)


class MemoryDependency(BaseModel):
    memory_dependency_id: str = Field(default_factory=lambda: ident("memory_dependency"))
    project_id: str
    memory_id: str
    dependent_kind: Literal["product_contract_revision", "agent_revision", "eval_case", "evidence", "finding"]
    dependent_id: str
    component_paths: list[str] = Field(default_factory=list)
    component_fingerprints: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class EvolutionReviewWorkItem(BaseModel):
    evolution_review_work_item_id: str = Field(default_factory=lambda: ident("evolution_review"))
    project_id: str
    evolution_changeset_id: str
    memory_id: str
    reason: str = Field(min_length=1)
    status: WorkStatus = "planned"
    created_at: str = Field(default_factory=now)


class StalePropagation(BaseModel):
    stale_propagation_id: str = Field(default_factory=lambda: ident("stale_propagation"))
    project_id: str
    evolution_changeset_id: str
    stale_memory_ids: list[str] = Field(default_factory=list)
    review_work_item_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class IntakeReviewClaim(BaseModel):
    claim_id: str = Field(default_factory=lambda: ident("intake_claim"))
    topic: str = Field(min_length=1)
    status: EvolutionEvidenceStatus
    statement: str = Field(min_length=1)
    evidence_level: EvidenceLevel
    evidence_refs: list[str] = Field(min_length=1)
    scope: str = Field(min_length=1)
    unknowns: list[str] = Field(min_length=1)
    next_evidence_action: str = Field(min_length=1)


class IntakeReviewReport(BaseModel):
    intake_review_report_id: str = Field(default_factory=lambda: ident("intake_report"))
    project_id: str
    evolution_case_id: str
    claims: list[IntakeReviewClaim] = Field(min_length=1)
    quality_status: Literal["PASS", "BLOCKED"] = "BLOCKED"
    quality_issues: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


# Evaluation access is an evidence boundary, not a maturity score.  Higher
# levels include the lower-level facts but never upgrade them retroactively.
EvaluationAccessLevel = Literal["L0_artifact_only", "L1_replay", "L2_full_runtime"]


class NativeHarnessContract(BaseModel):
    native_harness_contract_id: str = Field(default_factory=lambda: ident("native_harness_contract"))
    project_id: str
    evolution_case_id: str
    baseline_entrypoint: str = Field(min_length=1)
    candidate_entrypoint: str = Field(min_length=1)
    adapter_ref: str = Field(min_length=1)
    trace_schema_ref: str = Field(min_length=1)
    behavior_mode: Literal["production_parity", "reconstruction"] = "reconstruction"
    status: Literal["incomplete", "approved"] = "incomplete"
    created_at: str = Field(default_factory=now)


class RuntimeEnvironmentContract(BaseModel):
    runtime_environment_contract_id: str = Field(default_factory=lambda: ident("runtime_environment_contract"))
    project_id: str
    evolution_case_id: str
    docker_ref: str | None = None
    dependency_lock_ref: str | None = None
    model_config_ref: str | None = None
    tools_manifest_ref: str | None = None
    reset_command_ref: str | None = None
    initial_state_ref: str | None = None
    status: Literal["incomplete", "approved"] = "incomplete"
    created_at: str = Field(default_factory=now)


class TaskVerifierContract(BaseModel):
    task_verifier_contract_id: str = Field(default_factory=lambda: ident("task_verifier_contract"))
    project_id: str
    evolution_case_id: str
    task_spec_ref: str = Field(min_length=1)
    verifier_ref: str = Field(min_length=1)
    pass_iff: str = Field(min_length=1)
    initial_state_ref: str = Field(min_length=1)
    trace_evidence_ref: str = Field(min_length=1)
    status: Literal["incomplete", "approved"] = "incomplete"
    created_at: str = Field(default_factory=now)


class EnvironmentCheck(BaseModel):
    name: Literal["docker", "dependency", "model_config", "tools", "reset", "initial_state", "verifier"]
    status: Literal["passed", "missing", "failed", "not_run"]
    evidence_ref: str | None = None
    detail: str = Field(min_length=1)


class RuntimeEnvironmentPreflight(BaseModel):
    runtime_environment_preflight_id: str = Field(default_factory=lambda: ident("runtime_preflight"))
    project_id: str
    evolution_case_id: str
    environment_contract_id: str
    status: Literal["passed", "environment_not_satisfied", "not_run"] = "not_run"
    environment_fingerprint: str | None = None
    checks: list[EnvironmentCheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class HistoricalReplayEvidence(BaseModel):
    historical_replay_evidence_id: str = Field(default_factory=lambda: ident("historical_replay"))
    project_id: str
    evolution_case_id: str
    revision_id: str
    trace_sha256: str = Field(min_length=16)
    tool_result_sha256: str = Field(min_length=16)
    execution_log_sha256: str = Field(min_length=16)
    initial_state_sha256: str = Field(min_length=16)
    verifier_evidence_ref: str = Field(min_length=1)
    created_at: str = Field(default_factory=now)


class EvaluationAdmission(BaseModel):
    evaluation_admission_id: str = Field(default_factory=lambda: ident("evaluation_admission"))
    project_id: str
    evolution_case_id: str
    level: EvaluationAccessLevel
    status: Literal["analysis_only", "replay_ready", "runtime_ready", "environment_not_satisfied", "contract_incomplete"]
    allowed_operations: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class EvaluationPipeline(BaseModel):
    evaluation_pipeline_id: str = Field(default_factory=lambda: ident("evaluation_pipeline"))
    project_id: str
    evolution_case_id: str
    admission_id: str
    level: Literal["L2_full_runtime"] = "L2_full_runtime"
    status: Literal["queued", "blocked", "running", "completed"] = "queued"
    stages: list[str] = Field(default_factory=list)
    environment_fingerprint: str = Field(min_length=16)
    created_at: str = Field(default_factory=now)
