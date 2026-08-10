from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .artifacts import snapshot_manifest
from .domain import (
    EvolutionAgentRun,
    HistoricalReplayEvidence,
    IntakeReviewReport,
    MemoryDependency,
    MemoryEntry,
    NativeHarnessContract,
    Product,
    ProductContractRevision,
    ProviderBinding,
    ReportManifest,
    ReportNarrative,
    RuntimeEnvironmentContract,
    RuntimeEnvironmentPreflight,
    SkillAblationAnalysis,
    SkillAblationEvidence,
    SkillAblationVerification,
    SkillContract,
    TaskVerifierContract,
    Version,
)
from .evolution import EvaluationAdmissionResult, EvolutionIntakeError, EvolutionIntakeResult, EvolutionService
from .evolution_evidence import recompute_evolution_comparison
from .evolution_runtime import EvolutionAgentRuntime, ToolCallingProvider
from .evaluation_request import (
    EvaluationRequest,
    EvaluationRequestRepository,
    validate_evaluation_request,
    validate_skill_artifacts,
)
from .evaluation_execution_config import (
    EvaluationExecutionConfiguration,
    EvaluationExecutionConfigurationRepository,
)
from .evaluation_memory import EvaluationKnowledge, EvaluationKnowledgeRepository, knowledge_from_report
from .benchmark_evidence import BenchmarkEvidence, BenchmarkEvidenceRepository
from .content_identity import canonical_fingerprint
from .reporting import REPORT_SYSTEM_PROMPT, ReportNarrativeAdapter, build_report_manifest, record_blocked_report
from .project_intelligence import (
    CapabilityRecord,
    ProjectIntelligence,
    ProjectIntelligenceRegistration,
    ProjectIntelligenceRepository,
    ProjectSnapshotRegistrationResult,
)
from .project_scanner import (
    ProjectScanRecord,
    ProjectScanRequest,
    ProjectScanResult,
    ProjectScanner,
    ProjectScannerRepository,
    _safe_extract_archive,
)
from .runtime_onboarding import (
    RuntimeConfigurationDraft,
    RuntimeConfigurationDraftRepository,
    RuntimeDraftReview,
    build_runtime_draft,
)
from .target_onboarding import (
    TargetEnvironmentCache,
    TargetInteractionSpec,
    TargetManifest,
    TargetRuntimeSpec,
    TargetSourceSpec,
    source_working_tree_fingerprint,
)
from .runtime_comparability import RuntimeComparabilityResult, RuntimePreflightResult, compare_runtime_snapshots, preflight_runtime
from .evaluation_planning import EvaluationPlan
from .evaluation_run import EvaluationRun, EvaluationRunRepository
from .evaluation_report import EvaluationReportRecord, EvaluationReportRepository
from .project_upload import ProjectUpload, ProjectUploadRepository
from .skill_ablation import record_skill_ablation
from .skill_ablation_analysis import SKILL_ABLATION_ANALYSIS_SYSTEM_PROMPT, SkillAblationEvidenceAdapter
from .store import Store
from .targets import EvidenceReviewAdapter


class ProductNotFoundError(KeyError):
    pass


class AssistantInputError(ValueError):
    pass


def _model_fingerprint(model) -> str:
    return canonical_fingerprint(model.model_dump(mode="json"))


class Service:
    """Application boundary for the current evolution and evidence workflows."""

    def __init__(self, db: str) -> None:
        self.store = Store(db)
        self.evolution = EvolutionService(self.store)
        self.project_intelligence_store = ProjectIntelligenceRepository(self.store)
        self.evaluation_memory_store = EvaluationKnowledgeRepository(self.store)
        self.benchmark_evidence_store = BenchmarkEvidenceRepository(self.store)
        self.evaluation_request_store = EvaluationRequestRepository(self.store)
        self.evaluation_execution_config_store = EvaluationExecutionConfigurationRepository(self.store)
        self.evaluation_run_store = EvaluationRunRepository(self.store)
        self.evaluation_report_store = EvaluationReportRepository(self.store)
        self.project_upload_store = ProjectUploadRepository(self.store)
        self.runtime_draft_store = RuntimeConfigurationDraftRepository(self.store)
        self.project_scanner = ProjectScanner()
        self.project_scan_store = ProjectScannerRepository(self.store)

    def register_project_intelligence(
        self, registration: ProjectIntelligenceRegistration
    ) -> ProjectIntelligence:
        return self.project_intelligence_store.register(registration)

    def project_intelligence(self, project_id: str) -> ProjectIntelligence | None:
        return self.project_intelligence_store.get(project_id)

    def project_intelligences(self) -> list[ProjectIntelligence]:
        return self.project_intelligence_store.list()

    def register_project_snapshot(
        self, registration: ProjectIntelligenceRegistration
    ) -> ProjectSnapshotRegistrationResult:
        return self.project_intelligence_store.register_snapshot(registration)

    def scan_project(self, request: ProjectScanRequest) -> ProjectScanResult:
        """Scan a source and register the resulting immutable snapshot when ready."""

        result = self.project_scanner.scan(request)
        if result.registration is None:
            self.project_scan_store.save(result.scan)
            return result

        existing = self.project_intelligence(request.project_id)
        registration = result.registration
        if existing is None:
            intelligence = self.register_project_intelligence(registration)
            snapshot_diff = None
        else:
            registration = registration.model_copy(update={
                "baseline_version": existing.baseline_snapshot.baseline_version,
                "snapshot_version": request.version,
            })
            snapshot_result = self.register_project_snapshot(registration)
            intelligence = snapshot_result.intelligence
            snapshot_diff = snapshot_result.diff
        latest_snapshot = intelligence.latest_snapshot
        scan_record = result.scan.model_copy(update={
            "registration_fingerprint": _model_fingerprint(registration),
            "registered_snapshot_id": latest_snapshot.snapshot_id if latest_snapshot else None,
        })
        scan_record = self.project_scan_store.save(scan_record)
        return result.model_copy(
            update={
                "scan": scan_record,
                "registration": registration,
                "intelligence": intelligence,
                "snapshot_diff": snapshot_diff,
            }
    )
    def project_scans(self, project_id: str) -> list[ProjectScanRecord]:
        return self.project_scan_store.list(project_id)

    def runtime_comparability(
        self,
        project_id: str,
        baseline_version: str,
        candidate_version: str,
        *,
        baseline_source_root: Path | None = None,
        candidate_source_root: Path | None = None,
    ) -> RuntimeComparabilityResult:
        intelligence = self.project_intelligence(project_id)
        if intelligence is None:
            raise ProductNotFoundError(project_id)
        baseline = next((item for item in intelligence.snapshot_history if item.version == baseline_version), None)
        candidate = next((item for item in intelligence.snapshot_history if item.version == candidate_version), None)
        if baseline is None or candidate is None:
            missing = baseline_version if baseline is None else candidate_version
            raise ValueError(f"Snapshot version {missing} is not registered for project {project_id}.")
        return compare_runtime_snapshots(
            baseline,
            candidate,
            baseline_source_root=baseline_source_root,
            candidate_source_root=candidate_source_root,
        )

    def runtime_preflight(
        self,
        project_id: str,
        version: str,
        *,
        source_root: Path | None = None,
    ) -> RuntimePreflightResult:
        intelligence = self.project_intelligence(project_id)
        if intelligence is None:
            raise ProductNotFoundError(project_id)
        snapshot = next((item for item in intelligence.snapshot_history if item.version == version), None)
        if snapshot is None:
            raise ValueError(f"Snapshot version {version} is not registered for project {project_id}.")
        return preflight_runtime(
            snapshot.runtime_profile,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            source_root=source_root,
        )

    def evaluation_knowledge(
        self, project_id: str, component_pattern: str | None = None
    ) -> list[EvaluationKnowledge]:
        return self.evaluation_memory_store.list(project_id, component_pattern)

    def evaluation_knowledge_for_target(
        self, project_id: str, *, component_pattern: str | None, component_type: str
    ) -> list[EvaluationKnowledge]:
        """Return exact pattern knowledge, falling back to a type-level pattern."""

        exact = self.evaluation_knowledge(project_id, component_pattern)
        if exact:
            return exact
        if component_pattern != component_type:
            return self.evaluation_knowledge(project_id, component_type)
        return exact

    def record_evaluation_knowledge(self, knowledge: EvaluationKnowledge) -> EvaluationKnowledge:
        if self.project_intelligence(knowledge.project_id) is None:
            raise ProductNotFoundError(knowledge.project_id)
        return self.evaluation_memory_store.record(knowledge)

    def record_evaluation_knowledge_from_report(
        self, project_id: str, report, *, component_pattern: str
    ) -> EvaluationKnowledge:
        if self.project_intelligence(project_id) is None:
            raise ProductNotFoundError(project_id)
        knowledge = knowledge_from_report(report, component_pattern=component_pattern)
        if knowledge.project_id != project_id:
            raise AssistantInputError("Report project does not match the requested project.")
        return self.evaluation_memory_store.record(knowledge)

    def import_benchmark_evidence(
        self,
        project_id: str,
        payload: dict[str, object],
        *,
        source_ref: str,
        source_bytes: bytes | None = None,
    ) -> BenchmarkEvidence:
        if self.project_intelligence(project_id) is None:
            raise ProductNotFoundError(project_id)
        return self.benchmark_evidence_store.import_result(
            project_id, payload, source_ref=source_ref, source_bytes=source_bytes
        )

    def benchmark_evidence(self, project_id: str) -> list[BenchmarkEvidence]:
        return self.benchmark_evidence_store.list(project_id)

    def create_evaluation_request(
        self,
        request: EvaluationRequest,
        *,
        candidate_available: bool,
        candidate_component_name: str | None = None,
        skill_artifacts: list[object] | None = None,
    ) -> EvaluationRequest:
        intelligence = self.project_intelligence(request.project_id)
        if intelligence is None:
            raise ProductNotFoundError(request.project_id)
        validated = validate_evaluation_request(
            request,
            intelligence,
            candidate_available=candidate_available,
            candidate_component_name=candidate_component_name,
        )
        if skill_artifacts is not None:
            validate_skill_artifacts(validated, skill_artifacts)
        return self.evaluation_request_store.save(validated)

    def evaluation_request(self, project_id: str, request_id: str) -> EvaluationRequest | None:
        return self.evaluation_request_store.get(project_id, request_id)

    def evaluation_requests(self, project_id: str) -> list[EvaluationRequest]:
        if self.project_intelligence(project_id) is None:
            raise ProductNotFoundError(project_id)
        return self.evaluation_request_store.list(project_id)

    def bind_evaluation_request_scope(self, request: EvaluationRequest, scope_id: str) -> EvaluationRequest:
        return self.evaluation_request_store.bind_scope(request, scope_id)

    def save_evaluation_plan(self, plan: EvaluationPlan) -> EvaluationPlan:
        """Persist one immutable plan so readiness and reports share its identity."""

        existing = self.store.get("evaluation_plan", plan.plan_id, EvaluationPlan)
        if existing is not None and existing.model_dump(mode="json") != plan.model_dump(mode="json"):
            raise ValueError("Evaluation Plan already exists with different contents.")
        self.store.save("evaluation_plan", plan.plan_id, plan.project_id, plan)
        return plan

    def evaluation_plan(self, project_id: str, plan_id: str) -> EvaluationPlan | None:
        plan = self.store.get("evaluation_plan", plan_id, EvaluationPlan)
        if plan is None or plan.project_id != project_id:
            return None
        return plan

    def save_evaluation_run(self, run: EvaluationRun) -> EvaluationRun:
        return self.evaluation_run_store.save(run)

    def evaluation_run(self, project_id: str, run_id: str) -> EvaluationRun | None:
        return self.evaluation_run_store.get(project_id, run_id)

    def evaluation_runs(self, project_id: str, evaluation_request_id: str) -> list[EvaluationRun]:
        return self.evaluation_run_store.list_for_request(project_id, evaluation_request_id)

    def save_evaluation_report(self, record: EvaluationReportRecord) -> EvaluationReportRecord:
        return self.evaluation_report_store.save(record)

    def evaluation_report(self, project_id: str, report_id: str) -> EvaluationReportRecord | None:
        return self.evaluation_report_store.get(project_id, report_id)

    def evaluation_report_for_run(self, project_id: str, run_id: str) -> EvaluationReportRecord | None:
        return self.evaluation_report_store.by_run(project_id, run_id)

    def evaluation_reports(self, project_id: str) -> list[EvaluationReportRecord]:
        return self.evaluation_report_store.list(project_id)

    def save_evaluation_execution_configuration(
        self, config: EvaluationExecutionConfiguration
    ) -> EvaluationExecutionConfiguration:
        if self.project_intelligence(config.project_id) is None and self.product(config.project_id) is None:
            raise ProductNotFoundError(config.project_id)
        return self.evaluation_execution_config_store.save(config)

    def evaluation_execution_configuration(
        self, project_id: str, config_id: str
    ) -> EvaluationExecutionConfiguration | None:
        return self.evaluation_execution_config_store.get(project_id, config_id)

    def evaluation_execution_configurations(
        self, project_id: str
    ) -> list[EvaluationExecutionConfiguration]:
        if self.project_intelligence(project_id) is None and self.product(project_id) is None:
            raise ProductNotFoundError(project_id)
        return self.evaluation_execution_config_store.list(project_id)

    def runtime_configuration_draft(
        self, project_id: str, snapshot_version: str
    ) -> RuntimeConfigurationDraft:
        intelligence = self.project_intelligence(project_id)
        if intelligence is None:
            raise ProductNotFoundError(project_id)
        snapshot = next((item for item in intelligence.snapshot_history if item.version == snapshot_version), None)
        if snapshot is None and intelligence.baseline_snapshot.baseline_version != snapshot_version:
            raise ValueError(f"Snapshot {snapshot_version} was not found.")
        runtime = snapshot.runtime_profile if snapshot is not None else intelligence.baseline_snapshot.runtime_snapshot
        existing = self.runtime_draft_store.latest(project_id, runtime.source_fingerprint or "")
        if existing:
            return existing
        source_root = self._materialize_runtime_source(project_id, runtime.source_fingerprint)
        draft = build_runtime_draft(
            intelligence, snapshot_version=snapshot_version, source_root=source_root,
        )
        return self.runtime_draft_store.save(draft)

    def save_reviewed_runtime_configuration(
        self,
        project_id: str,
        draft_id: str,
        review: RuntimeDraftReview,
    ) -> EvaluationExecutionConfiguration:
        intelligence = self.project_intelligence(project_id)
        if intelligence is None:
            raise ProductNotFoundError(project_id)
        drafts = [item for item in self.store.list("runtime_configuration_draft", RuntimeConfigurationDraft, project_id) if item.draft_id == draft_id]
        if not drafts:
            raise ValueError("Runtime configuration draft was not found for this project.")
        draft = drafts[0]
        source_root = self._materialize_runtime_source(project_id, draft.source_fingerprint)
        entrypoint_path = _entrypoint_path(review.entrypoint)
        if entrypoint_path is None or not (source_root / entrypoint_path).is_file():
            raise ValueError("Reviewed entrypoint must name an existing source file.")
        manifest_root = _runtime_storage_root() / "manifests" / project_id / draft.snapshot_fingerprint
        manifest_root.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_root / f"{draft.draft_id}.json"
        runtime_command = _runtime_command(review.entrypoint)
        interaction_command = _materialize_command(review.interaction_command)
        oracle_command = _materialize_command(review.oracle_command)
        manifest = TargetManifest(
            target_id=_target_id(project_id, draft.snapshot_fingerprint),
            source=TargetSourceSpec(path=str(source_root), revision=f"tree:{source_working_tree_fingerprint(source_root)}"),
            runtime=TargetRuntimeSpec(
                kind="native_command",
                command=runtime_command,
                required_source_files=[entrypoint_path],
            ),
            interaction=TargetInteractionSpec(command=interaction_command),
        )
        manifest_path.write_text(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        cache_root = _runtime_storage_root() / "cache" / project_id
        runtime_executable = Path(sys.executable)
        if draft.language == "node":
            node = shutil.which("node")
            if not node:
                raise ValueError("Node runtime is not available on this server; runtime review cannot be saved.")
            runtime_executable = Path(node)
        TargetEnvironmentCache(cache_root).import_environment(manifest_path, runtime_executable)
        config = EvaluationExecutionConfiguration(
            project_id=project_id,
            name=review.name,
            manifest_path=str(manifest_path),
            cache_root=str(cache_root),
            run_root_parent=str(_runtime_storage_root() / "runs" / project_id),
            oracle_command=oracle_command,
            oracle_id=review.oracle_id,
            oracle_type=review.oracle_type,
            oracle_version=review.oracle_version,
            oracle_cwd=str(source_root),
            runtime_draft_id=draft.draft_id,
            snapshot_version=draft.snapshot_version,
            snapshot_fingerprint=draft.snapshot_fingerprint,
        )
        return self.save_evaluation_execution_configuration(config)

    def _materialize_runtime_source(self, project_id: str, source_fingerprint: str | None) -> Path:
        if not source_fingerprint:
            raise ValueError("Runtime source fingerprint is unavailable; rescan the project before configuring runtime.")
        upload = next(
            (item for item in reversed(self.project_uploads(project_id)) if item.source_fingerprint == source_fingerprint),
            None,
        )
        if upload is None or upload.source_kind != "package":
            raise ValueError("Runtime onboarding currently requires the uploaded source package for this snapshot.")
        destination = _runtime_storage_root() / "sources" / project_id / source_fingerprint
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="aig-runtime-source-") as temporary:
                staged = Path(temporary) / "source"
                staged.mkdir()
                _safe_extract_archive(Path(upload.source_path), staged)
                shutil.move(str(staged), str(destination))
        nested = [item for item in destination.iterdir() if item.is_dir()]
        return nested[0] if len(nested) == 1 else destination

    def save_project_upload(self, upload: ProjectUpload) -> ProjectUpload:
        """Persist an intake artifact before the first project snapshot exists.

        Browser upload is the first step of onboarding, so it cannot require
        Project Intelligence that is only created by the subsequent scan.
        The scan still owns runtime admission and registration.
        """
        return self.project_upload_store.save(upload)

    def project_upload(self, project_id: str, upload_id: str) -> ProjectUpload | None:
        return self.project_upload_store.get(project_id, upload_id)

    def project_uploads(self, project_id: str) -> list[ProjectUpload]:
        return self.project_upload_store.list(project_id)

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

    def intake_agent_evolution(
        self,
        project_id: str,
        source: Path,
        baseline_ref: str,
        candidate_ref: str,
        *,
        repository_url: str | None = None,
        declared_entrypoint: str | None = None,
    ) -> EvolutionIntakeResult:
        if not self.product(project_id):
            raise ProductNotFoundError(project_id)
        return self.evolution.intake(
            project_id=project_id,
            source_path=source,
            baseline_ref=baseline_ref,
            candidate_ref=candidate_ref,
            repository_url=repository_url,
            declared_entrypoint=declared_entrypoint,
        )

    def evolution_report(self, project_id: str, report_id: str) -> IntakeReviewReport:
        report = self.store.get("intake_review_report", report_id, IntakeReviewReport)
        if not report or report.project_id != project_id:
            raise AssistantInputError("Intake review report not found in this project.")
        return report

    def record_product_contract_revision(self, project_id: str, contract: ProductContractRevision) -> ProductContractRevision:
        if not self.product(project_id) or contract.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_product_contract(contract)

    def record_memory_entry(self, project_id: str, memory: MemoryEntry) -> MemoryEntry:
        if not self.product(project_id) or memory.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_memory(memory)

    def record_memory_dependency(self, project_id: str, dependency: MemoryDependency) -> MemoryDependency:
        if not self.product(project_id) or dependency.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_dependency(dependency)

    def record_provider_binding(self, project_id: str, binding: ProviderBinding) -> ProviderBinding:
        if (not self.product(project_id) and self.project_intelligence(project_id) is None) or binding.project_id != project_id:
            raise ProductNotFoundError(project_id)
        self.store.save("provider_binding", binding.provider_binding_id, project_id, binding)
        return binding

    def provider_binding(self, project_id: str, provider_binding_id: str) -> ProviderBinding:
        binding = self.store.get("provider_binding", provider_binding_id, ProviderBinding)
        if not binding or binding.project_id != project_id:
            raise EvolutionIntakeError("ProviderBinding not found in this project.")
        return binding

    def provider_bindings(self, project_id: str) -> list[ProviderBinding]:
        if self.project_intelligence(project_id) is None and self.product(project_id) is None:
            raise ProductNotFoundError(project_id)
        return self.store.list("provider_binding", ProviderBinding, project_id)

    def save_skill_pair(self, project_id: str, name: str, members: list[str]) -> CapabilityRecord:
        intelligence = self.project_intelligence(project_id)
        if intelligence is None:
            raise ProductNotFoundError(project_id)
        skills = {item.name for item in intelligence.capability_registry if item.component_type == "skill"}
        if len(members) != 2 or len(set(members)) != 2 or not set(members).issubset(skills):
            raise ValueError("Reusable Skill Pair must contain two discovered distinct Skills.")
        pair = CapabilityRecord(
            capability_id="skill_pair_" + hashlib.sha256(f"{project_id}:{'|'.join(sorted(members))}".encode("utf-8")).hexdigest()[:16],
            project_id=project_id, component_type="skill_pair", name=name,
            responsibility=f"Coordinate {members[0]} and {members[1]}.", dependencies=members,
            boundary=[], status="declared", source_refs=["user:pair-registration"],
        )
        self.store.save("project_intelligence_capability", pair.capability_id, project_id, pair)
        return pair

    def run_evolution_control_plane_smoke(
        self,
        *,
        project_id: str,
        provider_binding_id: str,
        objective: str,
        evidence_ref: str,
        evidence: dict[str, object],
        provider: ToolCallingProvider,
    ) -> EvolutionAgentRun:
        if not self.product(project_id):
            raise ProductNotFoundError(project_id)
        binding = self.provider_binding(project_id, provider_binding_id)
        return EvolutionAgentRuntime(
            self.store, binding, provider, EvidenceReviewAdapter(evidence_ref, evidence)
        ).start(project_id=project_id, evolution_case_id="control_plane_smoke:approved_evolution_specs", objective=objective)

    def recompute_evolution_comparison(self, project_id: str, evolution_case_id: str):
        if not self.product(project_id):
            raise ProductNotFoundError(project_id)
        return recompute_evolution_comparison(self.store, project_id, evolution_case_id)

    def build_evolution_report_manifest(
        self, project_id: str, evolution_case_id: str, control_plane_run_id: str
    ) -> ReportManifest:
        if not self.product(project_id):
            raise ProductNotFoundError(project_id)
        return build_report_manifest(self.store, project_id, evolution_case_id, control_plane_run_id)

    def report_manifest(self, project_id: str, report_manifest_id: str) -> ReportManifest:
        manifest = self.store.get("report_manifest", report_manifest_id, ReportManifest)
        if not manifest or manifest.project_id != project_id:
            raise EvolutionIntakeError("ReportManifest not found in this project.")
        return manifest

    def report_narrative(self, project_id: str, report_narrative_id: str) -> ReportNarrative:
        narrative = self.store.get("report_narrative", report_narrative_id, ReportNarrative)
        if not narrative or narrative.project_id != project_id:
            raise EvolutionIntakeError("ReportNarrative not found in this project.")
        return narrative

    def run_evolution_report_agent(
        self,
        *,
        project_id: str,
        report_manifest_id: str,
        provider_binding_id: str,
        objective: str,
        output_dir: Path,
        provider_factory: Callable[[ProviderBinding], ToolCallingProvider],
    ) -> tuple[EvolutionAgentRun, ReportNarrative]:
        manifest = self.report_manifest(project_id, report_manifest_id)
        binding = self.provider_binding(project_id, provider_binding_id)
        spent = sum(
            run.spent_cost_usd
            for run in self.store.list("evolution_agent_run", EvolutionAgentRun, project_id)
            if run.provider_binding_id == provider_binding_id
        )
        remaining = binding.batch_budget_usd - spent
        if remaining <= 0:
            raise EvolutionIntakeError("ProviderBinding aggregate budget is exhausted.")
        bounded = binding.model_copy(update={"batch_budget_usd": remaining})
        run = EvolutionAgentRuntime(
            self.store,
            bounded,
            provider_factory(bounded),
            ReportNarrativeAdapter(self.store, manifest, output_dir),
            system_prompt=REPORT_SYSTEM_PROMPT,
        ).start(project_id=project_id, evolution_case_id=manifest.evolution_case_id, objective=objective)
        if run.status != "completed":
            return run, record_blocked_report(self.store, manifest, run)
        return run, self.report_narrative(project_id, run.terminal_artifact_id or "")

    def record_native_harness_contract(self, project_id: str, contract: NativeHarnessContract) -> NativeHarnessContract:
        if not self.product(project_id) or contract.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_native_harness_contract(contract)

    def record_runtime_environment_contract(self, project_id: str, contract: RuntimeEnvironmentContract) -> RuntimeEnvironmentContract:
        if not self.product(project_id) or contract.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_runtime_environment_contract(contract)

    def record_task_verifier_contract(self, project_id: str, contract: TaskVerifierContract) -> TaskVerifierContract:
        if not self.product(project_id) or contract.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_task_verifier_contract(contract)

    def record_runtime_preflight(self, project_id: str, preflight: RuntimeEnvironmentPreflight) -> RuntimeEnvironmentPreflight:
        if not self.product(project_id) or preflight.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_runtime_preflight(preflight)

    def record_historical_replay_evidence(self, project_id: str, evidence: HistoricalReplayEvidence) -> HistoricalReplayEvidence:
        if not self.product(project_id) or evidence.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return self.evolution.record_historical_replay_evidence(evidence)

    def verify_skill_ablation(
        self, project_id: str, contract: SkillContract, evidence: SkillAblationEvidence
    ) -> SkillAblationVerification:
        if not self.product(project_id) or contract.project_id != project_id or evidence.project_id != project_id:
            raise ProductNotFoundError(project_id)
        return record_skill_ablation(self.store, contract, evidence)

    def skill_contract(self, project_id: str, skill_contract_id: str) -> SkillContract:
        contract = self.store.get("skill_ablation_contract", skill_contract_id, SkillContract)
        if not contract or contract.project_id != project_id:
            raise EvolutionIntakeError("SkillContract not found in this project.")
        return contract

    def skill_ablation_evidence(self, project_id: str, evidence_id: str) -> SkillAblationEvidence:
        evidence = self.store.get("skill_ablation_evidence", evidence_id, SkillAblationEvidence)
        if not evidence or evidence.project_id != project_id:
            raise EvolutionIntakeError("SkillAblationEvidence not found in this project.")
        return evidence

    def skill_ablation_analysis(self, project_id: str, analysis_id: str) -> SkillAblationAnalysis:
        analysis = self.store.get("skill_ablation_analysis", analysis_id, SkillAblationAnalysis)
        if not analysis or analysis.project_id != project_id:
            raise EvolutionIntakeError("SkillAblationAnalysis not found in this project.")
        return analysis

    def run_skill_ablation_analysis(
        self,
        *,
        project_id: str,
        skill_contract_id: str,
        skill_ablation_evidence_id: str,
        provider_binding_id: str,
        objective: str,
        provider: ToolCallingProvider,
    ) -> EvolutionAgentRun:
        contract = self.skill_contract(project_id, skill_contract_id)
        evidence = self.skill_ablation_evidence(project_id, skill_ablation_evidence_id)
        if evidence.skill_contract_id != contract.skill_contract_id:
            raise EvolutionIntakeError("SkillAblationEvidence does not belong to the requested SkillContract.")
        binding = self.provider_binding(project_id, provider_binding_id)
        return EvolutionAgentRuntime(
            self.store,
            binding,
            provider,
            SkillAblationEvidenceAdapter(self.store, contract, evidence),
            system_prompt=SKILL_ABLATION_ANALYSIS_SYSTEM_PROMPT,
        ).start(project_id=project_id, evolution_case_id=contract.evolution_case_id, objective=objective)

    def assess_evolution_admission(self, project_id: str, evolution_case_id: str) -> EvaluationAdmissionResult:
        if not self.product(project_id):
            raise ProductNotFoundError(project_id)
        return self.evolution.assess_evaluation_admission(project_id, evolution_case_id)

    def propagate_evolution_stale(self, project_id: str, evolution_changeset_id: str):
        if not self.product(project_id):
            raise ProductNotFoundError(project_id)
        return self.evolution.propagate_stale(project_id, evolution_changeset_id)


def _runtime_storage_root() -> Path:
    return Path(os.getenv("AGENTGUARD_RUNTIME_SOURCE_ROOT", "data/runtime")).resolve()


def _entrypoint_path(entrypoint: str) -> str | None:
    parts = entrypoint.replace("\\", "/").split()
    candidate = next((part for part in reversed(parts) if part.endswith((".py", ".js", ".ts"))), None)
    if candidate is None:
        return None
    path = Path(candidate)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _runtime_command(entrypoint: str) -> list[str]:
    path = _entrypoint_path(entrypoint)
    if path is None:
        raise ValueError("Reviewed entrypoint must be a relative Python or Node source file.")
    return ["{python}", path]


def _materialize_command(command: list[str]) -> list[str]:
    executable = str(Path(sys.executable).resolve())
    node = shutil.which("node")
    if any(item in {"{node}", "node"} for item in command) and not node:
        raise ValueError("Node runtime is not available on this server; choose a Python adapter or configure a Node runtime.")
    return [
        executable if item in {"{python}", "python"} else str(Path(node).resolve()) if item in {"{node}", "node"} else item
        for item in command
    ]


def _target_id(project_id: str, snapshot_fingerprint: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in project_id).strip("-")
    return f"{normalized[:48] or 'project'}-{snapshot_fingerprint[:12]}"
