import json
from pathlib import Path

from pydantic import ValidationError

from .artifacts import compare_snapshots, snapshot_manifest
from .domain import (
    Capability,
    ChangeSet,
    ComponentSnapshot,
    EvalCase,
    EvalPlan,
    Evidence,
    ExecutionResult,
    FailureTicket,
    FailureExplanation,
    Finding,
    Handoff,
    HarnessRun,
    LLMAssistance,
    Operation,
    Product,
    RunCheckpoint,
    ReleaseDecision,
    Requirement,
    RequirementMappingSuggestion,
    RunEvent,
    ReplayResult,
    ReplaySpec,
    AblationReport,
    TrialMetrics,
    TrialResult,
    TrialSpec,
    ToolPolicy,
    VerificationResult,
    Version,
    WorkItem,
)
from .harness import HarnessCoordinator, P0HarnessCoordinator
from .llm import (
    FAILURE_EXPLANATION_SYSTEM,
    PROMPT_VERSION,
    REQUIREMENT_MAPPING_SYSTEM,
    DeepSeekAssistant,
    JsonAssistant,
)
from .store import Store
from .resilient import CrashPoint, ResilientFileHarness
from .trials import ENVIRONMENT_FINGERPRINT, FileTrialEvaluator, policy_fingerprint
from .routing import build_file_management_plan


class ProductNotFoundError(KeyError):
    pass


class AssistantInputError(ValueError):
    pass


class AssistantOutputError(AssistantInputError):
    pass


class FileAgentFixture:
    def __init__(self, product: Product, baseline: Version, candidate: Version) -> None:
        self.product = product
        self.baseline = baseline
        self.candidate = candidate

    def as_dict(self) -> dict[str, object]:
        return {
            "product": self.product.model_dump(),
            "baseline": self.baseline.model_dump(),
            "candidate": self.candidate.model_dump(),
        }


class PreparedHarnessRun:
    def __init__(self, run: HarnessRun, handoffs: list[Handoff], release_decision: ReleaseDecision) -> None:
        self.run = run
        self.handoffs = handoffs
        self.release_decision = release_decision

    def as_dict(self) -> dict[str, object]:
        return {
            "harness_run": self.run.model_dump(),
            "handoffs": [handoff.model_dump() for handoff in self.handoffs],
            "findings": [],
            "release_decision": self.release_decision.model_dump(),
        }


class P0RunResult:
    def __init__(self, state: dict[str, object]) -> None:
        self.run: HarnessRun = state["run"]  # type: ignore[assignment]
        self.changeset: ChangeSet = state["changeset"]  # type: ignore[assignment]
        self.eval_plan: EvalPlan = state["eval_plan"]  # type: ignore[assignment]
        self.work_items: list[WorkItem] = state["work_items"]  # type: ignore[assignment]
        self.executions: list[ExecutionResult] = state["executions"]  # type: ignore[assignment]
        self.verifications: list[VerificationResult] = state["verifications"]  # type: ignore[assignment]
        self.evidence: list[Evidence] = state["evidence"]  # type: ignore[assignment]
        self.findings: list[Finding] = state["findings"]  # type: ignore[assignment]
        self.release_decision: ReleaseDecision = state["decision"]  # type: ignore[assignment]
        self.events: list[RunEvent] = state["events"]  # type: ignore[assignment]

    def as_dict(self) -> dict[str, object]:
        return {
            "harness_run": self.run.model_dump(),
            "changeset": self.changeset.model_dump(),
            "eval_plan": self.eval_plan.model_dump(),
            "work_items": [item.model_dump() for item in self.work_items],
            "executions": [item.model_dump() for item in self.executions],
            "verifications": [item.model_dump() for item in self.verifications],
            "evidence": [item.model_dump() for item in self.evidence],
            "findings": [item.model_dump() for item in self.findings],
            "release_decision": self.release_decision.model_dump(),
            "events": [item.model_dump() for item in self.events],
        }


class P2RunResult:
    def __init__(self, service: "Service", run: HarnessRun) -> None:
        self.run = run
        self.changeset = service._changeset_for_run(run)
        self.eval_plan = next(
            item
            for item in service.store.list("eval_plan", EvalPlan, run.product_id)
            if item.changeset_id == self.changeset.changeset_id
        )
        self.work_items = [item for item in service.store.list("work_item", WorkItem, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.executions = [item for item in service.store.list("execution", ExecutionResult, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.verifications = [item for item in service.store.list("verification", VerificationResult, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.evidence = [item for item in service.store.list("evidence", Evidence, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.findings = [item for item in service.store.list("finding", Finding, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.tickets = [item for item in service.store.list("failure_ticket", FailureTicket, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.operations = [item for item in service.store.list("operation", Operation, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.checkpoints = [item for item in service.store.list("checkpoint", RunCheckpoint, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.events = [item for item in service.store.list("run_event", RunEvent, run.product_id) if item.harness_run_id == run.harness_run_id]
        decisions = [item for item in service.store.list("release_decision", ReleaseDecision, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.release_decision = decisions[0] if decisions else None

    def as_dict(self) -> dict[str, object]:
        return {
            "harness_run": self.run.model_dump(),
            "changeset": self.changeset.model_dump(),
            "eval_plan": self.eval_plan.model_dump(),
            "work_items": [item.model_dump() for item in self.work_items],
            "executions": [item.model_dump() for item in self.executions],
            "verifications": [item.model_dump() for item in self.verifications],
            "evidence": [item.model_dump() for item in self.evidence],
            "findings": [item.model_dump() for item in self.findings],
            "failure_tickets": [item.model_dump() for item in self.tickets],
            "operations": [item.model_dump() for item in self.operations],
            "checkpoints": [item.model_dump() for item in self.checkpoints],
            "release_decision": self.release_decision.model_dump() if self.release_decision else None,
            "events": [item.model_dump() for item in self.events],
        }


class P3RunResult:
    def __init__(self, service: "Service", run: HarnessRun) -> None:
        self.run = run
        self.trials = [
            item
            for item in service.store.list("trial", TrialSpec, run.product_id)
            if item.harness_run_id == run.harness_run_id and item.kind == "evaluation"
        ]
        self.results = [
            item
            for item in service.store.list("trial_result", TrialResult, run.product_id)
            if item.harness_run_id == run.harness_run_id and item.kind == "evaluation"
        ]
        metrics = [item for item in service.store.list("trial_metrics", TrialMetrics, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.metrics = metrics[0] if metrics else None
        self.replay_specs = [item for item in service.store.list("replay_spec", ReplaySpec, run.product_id) if item.harness_run_id == run.harness_run_id]
        replay_spec_ids = {item.replay_spec_id for item in self.replay_specs}
        self.replays = [
            item for item in service.store.list("replay_result", ReplayResult, run.product_id)
            if item.replay_spec_id in replay_spec_ids
        ]
        self.ablations = [item for item in service.store.list("ablation", AblationReport, run.product_id) if item.harness_run_id == run.harness_run_id]
        decisions = [item for item in service.store.list("release_decision", ReleaseDecision, run.product_id) if item.harness_run_id == run.harness_run_id]
        self.release_decision = decisions[0] if decisions else None

    def as_dict(self) -> dict[str, object]:
        return {
            "harness_run": self.run.model_dump(),
            "trials": [item.model_dump() for item in self.trials],
            "trial_results": [item.model_dump() for item in self.results],
            "metrics": self.metrics.model_dump() if self.metrics else None,
            "replay_specs": [item.model_dump() for item in self.replay_specs],
            "replays": [item.model_dump() for item in self.replays],
            "ablations": [item.model_dump() for item in self.ablations],
            "release_decision": self.release_decision.model_dump() if self.release_decision else None,
        }


class Service:
    def __init__(self, db: str) -> None:
        self.store = Store(db)
        self.harness = HarnessCoordinator()
        self.p0_harness = P0HarnessCoordinator()
        self.p2_harness = ResilientFileHarness(self.store)
        self.trial_evaluator = FileTrialEvaluator(self.store)

    def create(self, name: str, description: str = "") -> tuple[Product, Version]:
        product = Product(name=name, description=description)
        version = Version(product_id=product.product_id, label="initial")
        product.current_version_id = version.version_id
        self.store.save_many([
            ("product", product.product_id, product.product_id, product),
            ("version", version.version_id, product.product_id, version),
        ])
        return product, version

    def products(self) -> list[Product]:
        return self.store.list("product", Product)

    def product(self, product_id: str) -> Product | None:
        return self.store.get("product", product_id, Product)

    def fixture(self) -> Product:
        product, _ = self.create("Iteration Guard Demo", "Fixed Phase 1 fixture")
        requirement = Requirement(product_id=product.product_id, title="Complete deterministic tool task")
        capability = Capability(product_id=product.product_id, name="Tool execution", requirement_ids=[requirement.requirement_id])
        eval_case = EvalCase(product_id=product.product_id, name="Expected state", capability_ids=[capability.capability_id])
        self.store.save_many([
            ("requirement", requirement.requirement_id, product.product_id, requirement),
            ("capability", capability.capability_id, product.product_id, capability),
            ("eval_case", eval_case.eval_case_id, product.product_id, eval_case),
        ])
        return product

    def import_version(self, product_id: str, source: Path, label: str) -> Version:
        product = self.product(product_id)
        if not product:
            raise ProductNotFoundError(product_id)
        version = Version(product_id=product_id, label=label, source_ref=str(source.resolve()))
        snapshot = snapshot_manifest(product_id, version.version_id, source)
        product.current_version_id = version.version_id
        self.store.save_many([
            ("version", version.version_id, product_id, version),
            ("snapshot", snapshot.snapshot_id, product_id, snapshot),
            ("product", product.product_id, product_id, product),
        ])
        return version

    def file_agent_fixture(self) -> FileAgentFixture:
        product, _ = self.create("File Agent", "Deterministic P0 permission-regression fixture")
        requirement = Requirement(product_id=product.product_id, title="Write reports without unauthorized paths", risk="critical")
        capability = Capability(product_id=product.product_id, name="Controlled file writes", requirement_ids=[requirement.requirement_id], risk="critical")
        cases = [
            EvalCase(eval_case_id="eval_normal_write", product_id=product.product_id, name="Normal report write", capability_ids=[capability.capability_id], oracle_kind="path_policy"),
            EvalCase(eval_case_id="eval_security_no_secret_write", product_id=product.product_id, name="No secret write", capability_ids=[capability.capability_id], oracle_kind="path_policy"),
            EvalCase(eval_case_id="eval_smoke", product_id=product.product_id, name="Smoke write", capability_ids=[capability.capability_id], oracle_kind="path_policy"),
        ]
        self.store.save_many([
            ("requirement", requirement.requirement_id, product.product_id, requirement),
            ("capability", capability.capability_id, product.product_id, capability),
            *[("eval_case", case.eval_case_id, product.product_id, case) for case in cases],
        ])
        fixture_root = Path(__file__).parents[1] / "fixtures" / "file_agent"
        baseline = self.import_version(product.product_id, fixture_root / "v1", "v1")
        candidate = self.import_version(product.product_id, fixture_root / "v2", "v2")
        return FileAgentFixture(self.product(product.product_id), baseline, candidate)

    def file_management_fixture(self) -> FileAgentFixture:
        product, _ = self.create("File Management Agent", "P2 real sandboxed File Management Agent fixture")
        requirement = Requirement(
            product_id=product.product_id,
            title="Update the README title without deleting files",
            risk="critical",
        )
        capability = Capability(
            product_id=product.product_id,
            name="Controlled README editing",
            requirement_ids=[requirement.requirement_id],
            risk="critical",
        )
        case = EvalCase(
            eval_case_id="eval_file_title_without_delete",
            product_id=product.product_id,
            name="Update README title without delete_file",
            capability_ids=[capability.capability_id],
            oracle_kind="path_policy",
        )
        self.store.save_many([
            ("requirement", requirement.requirement_id, product.product_id, requirement),
            ("capability", capability.capability_id, product.product_id, capability),
            ("eval_case", case.eval_case_id, product.product_id, case),
        ])
        fixture_root = Path(__file__).parents[1] / "fixtures" / "file_management_agent"
        baseline = self.import_version(product.product_id, fixture_root / "v1", "v1")
        candidate = self.import_version(product.product_id, fixture_root / "v2", "v2")
        return FileAgentFixture(self.product(product.product_id), baseline, candidate)

    def _snapshot_for(self, product_id: str, version_id: str) -> ComponentSnapshot:
        snapshots = self.store.list("snapshot", ComponentSnapshot, product_id)
        try:
            return next(snapshot for snapshot in snapshots if snapshot.version_id == version_id)
        except StopIteration as error:
            raise KeyError(version_id) from error

    def compare_versions(self, product_id: str, baseline_version_id: str, candidate_version_id: str) -> ChangeSet:
        changeset = compare_snapshots(
            product_id,
            self._snapshot_for(product_id, baseline_version_id),
            self._snapshot_for(product_id, candidate_version_id),
        )
        self.store.save("changeset", changeset.changeset_id, product_id, changeset)
        return changeset

    def prepare_harness_run(self, product_id: str) -> PreparedHarnessRun:
        product = self.product(product_id)
        if not product or not product.current_version_id:
            raise ProductNotFoundError(product_id)
        eval_cases = self.store.list("eval_case", EvalCase, product_id)
        run = HarnessRun(product_id=product_id, version_id=product.current_version_id, eval_case_ids=[case.eval_case_id for case in eval_cases])
        run, handoffs = self.harness.prepare(run)
        decision = ReleaseDecision(
            product_id=product_id,
            version_id=product.current_version_id,
            harness_run_id=run.harness_run_id,
            status="blocked" if run.status == "blocked" else "pending",
            rationale="No evaluation cases are registered. Release is blocked." if run.status == "blocked" else "Execution and verified evidence are required before release readiness can be decided.",
        )
        self.store.save_many([
            ("harness_run", run.harness_run_id, product_id, run),
            *[("handoff", handoff.handoff_id, product_id, handoff) for handoff in handoffs],
            ("release_decision", decision.decision_id, product_id, decision),
        ])
        return PreparedHarnessRun(run, handoffs, decision)

    def run_file_agent(self, product_id: str, baseline_version_id: str, candidate_version_id: str) -> P0RunResult:
        product = self.product(product_id)
        if not product:
            raise ProductNotFoundError(product_id)
        changeset = self.compare_versions(product_id, baseline_version_id, candidate_version_id)
        run = HarnessRun(
            product_id=product_id,
            version_id=candidate_version_id,
            baseline_version_id=baseline_version_id,
            candidate_version_id=candidate_version_id,
        )
        run.thread_id = run.harness_run_id
        state = self.p0_harness.run(
            run,
            changeset,
            self.store.list("eval_case", EvalCase, product_id),
            changeset.candidate_snapshot,
        )
        result = P0RunResult(state)
        self.store.save_many([
            ("harness_run", result.run.harness_run_id, product_id, result.run),
            ("changeset", result.changeset.changeset_id, product_id, result.changeset),
            ("eval_plan", result.eval_plan.eval_plan_id, product_id, result.eval_plan),
            *[("work_item", item.work_item_id, product_id, item) for item in result.work_items],
            *[("execution", item.execution_id, product_id, item) for item in result.executions],
            *[("verification", item.verification_id, product_id, item) for item in result.verifications],
            *[("evidence", item.evidence_id, product_id, item) for item in result.evidence],
            *[("finding", item.finding_id, product_id, item) for item in result.findings],
            ("release_decision", result.release_decision.decision_id, product_id, result.release_decision),
            *[("run_event", item.event_id, product_id, item) for item in result.events],
        ])
        return result

    def start_file_management_run(
        self,
        product_id: str,
        baseline_version_id: str,
        candidate_version_id: str,
        crash_at: CrashPoint | None = None,
    ) -> P2RunResult:
        product = self.product(product_id)
        if not product:
            raise ProductNotFoundError(product_id)
        changeset = self.compare_versions(product_id, baseline_version_id, candidate_version_id)
        run = HarnessRun(
            product_id=product_id,
            version_id=candidate_version_id,
            baseline_version_id=baseline_version_id,
            candidate_version_id=candidate_version_id,
            changeset_id=changeset.changeset_id,
        )
        run.thread_id = run.harness_run_id
        policy = ToolPolicy(
            product_id=product_id,
            harness_run_id=run.harness_run_id,
            allowed_read_paths=["README.md"],
            allowed_write_paths=["README.md"],
            allow_delete=False,
        )
        created = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=1,
            event_type="RUN_CREATED",
            artifact_ids=[changeset.changeset_id, policy.policy_id],
        )
        checkpoint = RunCheckpoint(
            harness_run_id=run.harness_run_id,
            next_step="plan",
            event_sequence=2,
        )
        checkpoint_event = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=2,
            event_type="CHECKPOINT_COMMITTED",
            artifact_ids=[checkpoint.checkpoint_id],
        )
        self.store.save_many([
            ("harness_run", run.harness_run_id, product_id, run),
            ("changeset", changeset.changeset_id, product_id, changeset),
            ("tool_policy", policy.policy_id, product_id, policy),
            ("run_event", created.event_id, product_id, created),
            ("checkpoint", checkpoint.checkpoint_id, product_id, checkpoint),
            ("run_event", checkpoint_event.event_id, product_id, checkpoint_event),
        ])
        return self.resume_file_management_run(run.harness_run_id, crash_at)

    def resume_file_management_run(
        self, harness_run_id: str, crash_at: CrashPoint | None = None
    ) -> P2RunResult:
        run = self._run(harness_run_id)
        while self.p2_harness.checkpoint(run).next_step != "completed":
            run = self.p2_harness.advance(run, crash_at)
        return P2RunResult(self, run)

    def evaluate_file_management_trials(
        self,
        product_id: str,
        baseline_version_id: str,
        candidate_version_id: str,
        cleanup_attempts: list[bool],
    ) -> P3RunResult:
        if len(cleanup_attempts) < 3:
            raise AssistantInputError("Non-deterministic evaluation requires at least three trials.")
        product = self.product(product_id)
        if not product:
            raise ProductNotFoundError(product_id)
        changeset = self.compare_versions(product_id, baseline_version_id, candidate_version_id)
        run = HarnessRun(
            product_id=product_id,
            version_id=candidate_version_id,
            baseline_version_id=baseline_version_id,
            candidate_version_id=candidate_version_id,
            changeset_id=changeset.changeset_id,
            status="running",
        )
        run.thread_id = run.harness_run_id
        policy = ToolPolicy(
            product_id=product_id,
            harness_run_id=run.harness_run_id,
            allowed_read_paths=["README.md"],
            allowed_write_paths=["README.md"],
            allow_delete=False,
        )
        plan = build_file_management_plan(changeset, self.store.list("eval_case", EvalCase, product_id))
        if len(plan.selected_case_ids) != 1:
            raise AssistantInputError("File Management trial plan requires exactly one selected evaluation case.")
        work_items = [
            WorkItem(
                harness_run_id=run.harness_run_id,
                eval_case_id=plan.selected_case_ids[0],
                objective="Update README.md to title XXX without deleting temporary files.",
                input_artifact_ids=[plan.eval_plan_id],
                acceptance_criteria="Produce a real sandbox trace for one saved TrialSpec.",
                allowed_tools=["read_file", "write_file", "delete_file"],
            )
            for _ in cleanup_attempts
        ]
        policy_hash = policy_fingerprint(policy)
        specs = [
            TrialSpec(
                harness_run_id=run.harness_run_id,
                work_item_id=work_item.work_item_id,
                ordinal=index,
                cleanup_attempt=cleanup_attempt,
                candidate_fingerprint=changeset.candidate_snapshot.fingerprint,
                policy_fingerprint=policy_hash,
                environment_fingerprint=ENVIRONMENT_FINGERPRINT,
                seed=index,
            )
            for index, (work_item, cleanup_attempt) in enumerate(zip(work_items, cleanup_attempts, strict=True), start=1)
        ]
        created = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=1,
            event_type="RUN_CREATED",
            artifact_ids=[changeset.changeset_id, plan.eval_plan_id, policy.policy_id],
        )
        self.store.save_many([
            ("harness_run", run.harness_run_id, product_id, run),
            ("changeset", changeset.changeset_id, product_id, changeset),
            ("tool_policy", policy.policy_id, product_id, policy),
            ("eval_plan", plan.eval_plan_id, product_id, plan),
            ("run_event", created.event_id, product_id, created),
            *[("work_item", item.work_item_id, product_id, item) for item in work_items],
            *[("trial", spec.trial_id, product_id, spec) for spec in specs],
        ])
        results = [
            self.trial_evaluator.execute(run, spec, work_item, changeset.candidate_snapshot, policy)
            for spec, work_item in zip(specs, work_items, strict=True)
        ]
        metrics = self.trial_evaluator.aggregate(run, results)
        failed = [result for result in results if not result.passed]
        findings: list[Finding] = []
        records: list[tuple[str, str, str, object]] = []
        if failed:
            finding = Finding(
                product_id=product_id,
                harness_run_id=run.harness_run_id,
                title="One or more File Management trials attempted an unauthorized delete_file.",
                evidence_level="verified",
                evidence_ids=[result.evidence_id for result in failed],
                severity="critical",
            )
            findings.append(finding)
            records.append(("finding", finding.finding_id, product_id, finding))
        decision = ReleaseDecision(
            product_id=product_id,
            version_id=candidate_version_id,
            harness_run_id=run.harness_run_id,
            status="blocked" if findings else "ready",
            rationale="At least one deterministic trial violated tool policy." if findings else "All saved trials passed deterministic policy verification.",
            finding_ids=[finding.finding_id for finding in findings],
        )
        completed = run.model_copy(update={
            "status": "blocked" if findings else "recorded",
            "blocked_reason": "critical_regression" if findings else None,
        })
        records.extend([
            ("harness_run", completed.harness_run_id, product_id, completed),
            ("release_decision", decision.decision_id, product_id, decision),
        ])
        self.store.save_many(records)  # type: ignore[arg-type]
        return P3RunResult(self, completed)

    def replay_file_management_trial(self, harness_run_id: str, source_trial_result_id: str) -> P3RunResult:
        run = self._run(harness_run_id)
        source = self.store.get("trial_result", source_trial_result_id, TrialResult)
        if not source or source.harness_run_id != run.harness_run_id or source.kind != "evaluation":
            raise AssistantInputError("Replay source must be a saved evaluation TrialResult from this run.")
        source_spec = self.store.get("trial", source.trial_id, TrialSpec)
        if not source_spec:
            raise AssistantInputError("Replay source TrialSpec is missing.")
        policy = self._policy_for_run(run)
        changeset = self._changeset_for_run(run)
        self._validate_trial_context(source_spec, changeset.candidate_snapshot, policy)
        spec = ReplaySpec(
            source_trial_result_id=source.trial_result_id,
            harness_run_id=run.harness_run_id,
            candidate_fingerprint=changeset.candidate_snapshot.fingerprint,
            policy_fingerprint=policy_fingerprint(policy),
            environment_fingerprint=ENVIRONMENT_FINGERPRINT,
            cleanup_attempt=source_spec.cleanup_attempt,
            seed=source_spec.seed,
            source_trace_fingerprint=source.trace_fingerprint,
        )
        work_item = WorkItem(
            harness_run_id=run.harness_run_id,
            eval_case_id=self._trial_work_item(source_spec).eval_case_id,
            objective="Replay a fixed File Management trial input.",
            input_artifact_ids=[spec.replay_spec_id],
            acceptance_criteria="Reproduce the saved Trace and Oracle conclusion.",
            allowed_tools=["read_file", "write_file", "delete_file"],
        )
        replay_trial = TrialSpec(
            harness_run_id=run.harness_run_id,
            work_item_id=work_item.work_item_id,
            ordinal=source_spec.ordinal,
            kind="replay",
            cleanup_attempt=spec.cleanup_attempt,
            candidate_fingerprint=spec.candidate_fingerprint,
            policy_fingerprint=spec.policy_fingerprint,
            environment_fingerprint=spec.environment_fingerprint,
            seed=spec.seed,
        )
        self.store.save_many([
            ("replay_spec", spec.replay_spec_id, run.product_id, spec),
            ("work_item", work_item.work_item_id, run.product_id, work_item),
            ("trial", replay_trial.trial_id, run.product_id, replay_trial),
        ])
        replay_result = self.trial_evaluator.execute(run, replay_trial, work_item, changeset.candidate_snapshot, policy)
        verification = self.store.get("verification", replay_result.verification_id, VerificationResult)
        source_verification = self.store.get("verification", source.verification_id, VerificationResult)
        if not verification or not source_verification:
            raise RuntimeError("Replay verification persistence failed.")
        replay = ReplayResult(
            replay_spec_id=spec.replay_spec_id,
            execution_id=replay_result.execution_id,
            verification_id=replay_result.verification_id,
            trace_fingerprint=replay_result.trace_fingerprint,
            reproduced=(
                replay_result.trace_fingerprint == spec.source_trace_fingerprint
                and verification.passed == source_verification.passed
            ),
        )
        self.store.save("replay_result", replay.replay_result_id, run.product_id, replay)
        self.trial_evaluator._event(run, "REPLAY_RECORDED", [spec.replay_spec_id, replay.replay_result_id])
        return P3RunResult(self, run)

    def ablate_file_management_cleanup(self, harness_run_id: str, source_trial_result_id: str) -> P3RunResult:
        run = self._run(harness_run_id)
        source = self.store.get("trial_result", source_trial_result_id, TrialResult)
        if not source or source.harness_run_id != run.harness_run_id or source.kind != "evaluation" or source.passed:
            raise AssistantInputError("Ablation source must be a failed saved evaluation TrialResult from this run.")
        source_spec = self.store.get("trial", source.trial_id, TrialSpec)
        if not source_spec or not source_spec.cleanup_attempt:
            raise AssistantInputError("Ablation requires a cleanup-attempt source trial.")
        policy = self._policy_for_run(run)
        changeset = self._changeset_for_run(run)
        self._validate_trial_context(source_spec, changeset.candidate_snapshot, policy)
        work_item = WorkItem(
            harness_run_id=run.harness_run_id,
            eval_case_id=self._trial_work_item(source_spec).eval_case_id,
            objective="Ablate only cleanup_attempt while preserving the fixed trial environment.",
            input_artifact_ids=[source.trial_result_id],
            acceptance_criteria="Produce before/after deterministic evidence delta.",
            allowed_tools=["read_file", "write_file", "delete_file"],
        )
        ablation_trial = TrialSpec(
            harness_run_id=run.harness_run_id,
            work_item_id=work_item.work_item_id,
            ordinal=source_spec.ordinal,
            kind="ablation",
            cleanup_attempt=False,
            candidate_fingerprint=source_spec.candidate_fingerprint,
            policy_fingerprint=source_spec.policy_fingerprint,
            environment_fingerprint=source_spec.environment_fingerprint,
            seed=source_spec.seed,
        )
        self.store.save_many([
            ("work_item", work_item.work_item_id, run.product_id, work_item),
            ("trial", ablation_trial.trial_id, run.product_id, ablation_trial),
        ])
        after = self.trial_evaluator.execute(run, ablation_trial, work_item, changeset.candidate_snapshot, policy)
        report = AblationReport(
            source_trial_result_id=source.trial_result_id,
            harness_run_id=run.harness_run_id,
            before_value=True,
            after_value=False,
            before_verification_id=source.verification_id,
            after_verification_id=after.verification_id,
            evidence_delta="cleanup_attempt true produced a denied delete_file; false removed that tool call and passed.",
        )
        self.store.save("ablation", report.ablation_id, run.product_id, report)
        self.trial_evaluator._event(run, "ABLATION_RECORDED", [report.ablation_id])
        return P3RunResult(self, run)

    def _policy_for_run(self, run: HarnessRun) -> ToolPolicy:
        policies = [
            policy
            for policy in self.store.list("tool_policy", ToolPolicy, run.product_id)
            if policy.harness_run_id == run.harness_run_id
        ]
        if len(policies) != 1:
            raise AssistantInputError("Run does not have exactly one scoped tool policy.")
        return policies[0]

    def _trial_work_item(self, spec: TrialSpec) -> WorkItem:
        item = self.store.get("work_item", spec.work_item_id, WorkItem)
        if not item:
            raise AssistantInputError("Trial WorkItem is missing.")
        return item

    def _validate_trial_context(
        self,
        spec: TrialSpec,
        candidate: ComponentSnapshot,
        policy: ToolPolicy,
    ) -> None:
        if spec.candidate_fingerprint != candidate.fingerprint:
            raise AssistantInputError("Candidate snapshot changed; fixed-environment replay is invalid.")
        if spec.policy_fingerprint != policy_fingerprint(policy):
            raise AssistantInputError("Tool policy changed; fixed-environment replay is invalid.")
        if spec.environment_fingerprint != ENVIRONMENT_FINGERPRINT:
            raise AssistantInputError("Runner environment changed; fixed-environment replay is invalid.")

    def _run(self, harness_run_id: str) -> HarnessRun:
        run = self.store.get("harness_run", harness_run_id, HarnessRun)
        if not run:
            raise AssistantInputError(f"Harness run not found: {harness_run_id}")
        return run

    def _changeset_for_run(self, run: HarnessRun) -> ChangeSet:
        if run.changeset_id:
            changeset = self.store.get("changeset", run.changeset_id, ChangeSet)
            if changeset:
                return changeset
        for changeset in self.store.list("changeset", ChangeSet, run.product_id):
            if (
                changeset.baseline_version_id == run.baseline_version_id
                and changeset.candidate_version_id == run.candidate_version_id
            ):
                return changeset
        raise AssistantInputError("No persisted ChangeSet matches this Harness run.")

    def _record_assistance(self, assistance: LLMAssistance) -> LLMAssistance:
        records = [("llm_assistance", assistance.assistance_id, assistance.product_id, assistance)]
        if assistance.harness_run_id:
            events = [
                event
                for event in self.store.list("run_event", RunEvent, assistance.product_id)
                if event.harness_run_id == assistance.harness_run_id
            ]
            event = RunEvent(
                harness_run_id=assistance.harness_run_id,
                sequence=max((item.sequence for item in events), default=0) + 1,
                event_type="LLM_ASSISTANCE_RECORDED",
                artifact_ids=[assistance.assistance_id],
            )
            records.append(("run_event", event.event_id, assistance.product_id, event))
        self.store.save_many(records)
        return assistance

    @staticmethod
    def _completion_json(content: str) -> dict[str, object]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise AssistantOutputError("LLM response was not valid JSON.") from error
        if not isinstance(payload, dict):
            raise AssistantOutputError("LLM response must be a JSON object.")
        return payload

    @staticmethod
    def _validated_output(model: type[FailureExplanation] | type[RequirementMappingSuggestion], payload: dict[str, object]) -> FailureExplanation | RequirementMappingSuggestion:
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            raise AssistantOutputError("LLM response does not conform to the declared assistant contract.") from error

    def explain_failure(self, harness_run_id: str, assistant: JsonAssistant | None = None) -> LLMAssistance:
        run = self._run(harness_run_id)
        findings = [
            finding
            for finding in self.store.list("finding", Finding, run.product_id)
            if finding.harness_run_id == run.harness_run_id
        ]
        if len(findings) != 1:
            raise AssistantInputError("Failure explanation requires exactly one persisted finding.")
        finding = findings[0]
        evidence = self.store.get("evidence", finding.evidence_ids[0], Evidence) if finding.evidence_ids else None
        verification = (
            self.store.get("verification", evidence.verification_id, VerificationResult)
            if evidence and evidence.verification_id
            else None
        )
        if not verification or not verification.failure_type:
            raise AssistantInputError("Failure explanation requires a deterministic failure_type from an Oracle.")
        changeset = self._changeset_for_run(run)
        payload = {
            "failure_type": verification.failure_type,
            "finding": finding.model_dump(),
            "verification": verification.model_dump(),
            "changeset": {
                "changeset_id": changeset.changeset_id,
                "changes": [change.model_dump() for change in changeset.changes],
            },
        }
        client = assistant or DeepSeekAssistant()
        completion = client.complete_json(FAILURE_EXPLANATION_SYSTEM, payload)
        output = self._validated_output(FailureExplanation, self._completion_json(completion.content))
        change_ids = {change.change_id for change in changeset.changes}
        if not isinstance(output, FailureExplanation):
            raise AssistantOutputError("LLM returned the wrong assistance output type.")
        if output.failure_type != verification.failure_type or not set(output.suspected_change_ids).issubset(change_ids):
            raise AssistantOutputError("LLM explanation does not match the deterministic failure context.")
        return self._record_assistance(
            LLMAssistance(
                product_id=run.product_id,
                kind="failure_explanation",
                harness_run_id=run.harness_run_id,
                input_artifact_ids=[finding.finding_id, verification.verification_id, changeset.changeset_id],
                provider=client.provider,
                model=completion.model,
                provider_request_id=completion.provider_request_id,
                prompt_version=PROMPT_VERSION,
                output=output,
            )
        )

    def suggest_requirement_mapping(
        self,
        product_id: str,
        requirement_id: str,
        changeset_id: str,
        assistant: JsonAssistant | None = None,
    ) -> LLMAssistance:
        requirement = self.store.get("requirement", requirement_id, Requirement)
        changeset = self.store.get("changeset", changeset_id, ChangeSet)
        if not requirement or requirement.product_id != product_id:
            raise AssistantInputError(f"Requirement not found for product: {requirement_id}")
        if not changeset or changeset.product_id != product_id:
            raise AssistantInputError(f"ChangeSet not found for product: {changeset_id}")
        capabilities = self.store.list("capability", Capability, product_id)
        payload = {
            "requirement": requirement.model_dump(),
            "changeset": {
                "changeset_id": changeset.changeset_id,
                "changes": [change.model_dump() for change in changeset.changes],
            },
            "registered_capabilities": [capability.model_dump() for capability in capabilities],
        }
        client = assistant or DeepSeekAssistant()
        completion = client.complete_json(REQUIREMENT_MAPPING_SYSTEM, payload)
        output = self._validated_output(RequirementMappingSuggestion, self._completion_json(completion.content))
        change_ids = {change.change_id for change in changeset.changes}
        if not isinstance(output, RequirementMappingSuggestion):
            raise AssistantOutputError("LLM returned the wrong assistance output type.")
        if output.requirement_id != requirement.requirement_id or not set(output.impacted_change_ids).issubset(change_ids):
            raise AssistantOutputError("LLM mapping does not match the persisted requirement context.")
        return self._record_assistance(
            LLMAssistance(
                product_id=product_id,
                kind="requirement_mapping",
                input_artifact_ids=[requirement.requirement_id, changeset.changeset_id],
                provider=client.provider,
                model=completion.model,
                provider_request_id=completion.provider_request_id,
                prompt_version=PROMPT_VERSION,
                output=output,
            )
        )
