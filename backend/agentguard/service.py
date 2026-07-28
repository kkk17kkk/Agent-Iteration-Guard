from pathlib import Path

from .artifacts import compare_snapshots, snapshot_manifest
from .domain import (
    Capability,
    ChangeSet,
    ComponentSnapshot,
    EvalCase,
    EvalPlan,
    Evidence,
    ExecutionResult,
    Finding,
    Handoff,
    HarnessRun,
    Product,
    ReleaseDecision,
    Requirement,
    RunEvent,
    VerificationResult,
    Version,
    WorkItem,
)
from .harness import HarnessCoordinator, P0HarnessCoordinator
from .store import Store


class ProductNotFoundError(KeyError):
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


class Service:
    def __init__(self, db: str) -> None:
        self.store = Store(db)
        self.harness = HarnessCoordinator()
        self.p0_harness = P0HarnessCoordinator()

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
