from __future__ import annotations

from collections.abc import Callable
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
from .reporting import REPORT_SYSTEM_PROMPT, ReportNarrativeAdapter, build_report_manifest, record_blocked_report
from .skill_ablation import record_skill_ablation
from .skill_ablation_analysis import SKILL_ABLATION_ANALYSIS_SYSTEM_PROMPT, SkillAblationEvidenceAdapter
from .store import Store
from .targets import EvidenceReviewAdapter


class ProductNotFoundError(KeyError):
    pass


class AssistantInputError(ValueError):
    pass


class Service:
    """Application boundary for the current evolution and evidence workflows."""

    def __init__(self, db: str) -> None:
        self.store = Store(db)
        self.evolution = EvolutionService(self.store)

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
        if not self.product(project_id) or binding.project_id != project_id:
            raise ProductNotFoundError(project_id)
        self.store.save("provider_binding", binding.provider_binding_id, project_id, binding)
        return binding

    def provider_binding(self, project_id: str, provider_binding_id: str) -> ProviderBinding:
        binding = self.store.get("provider_binding", provider_binding_id, ProviderBinding)
        if not binding or binding.project_id != project_id:
            raise EvolutionIntakeError("ProviderBinding not found in this project.")
        return binding

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
