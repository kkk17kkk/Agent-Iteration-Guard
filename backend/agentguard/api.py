import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .domain import (
    EnvironmentCheck,
    HistoricalReplayEvidence,
    MemoryDependency,
    MemoryEntry,
    NativeHarnessContract,
    ProductContractRevision,
    ProviderBinding,
    RuntimeEnvironmentContract,
    RuntimeEnvironmentPreflight,
    TaskVerifierContract,
)
from .evaluation_memory import EvaluationKnowledge
from .evolution import EvolutionIntakeError
from .evaluation_request import EvaluationRequest, EvaluationRequestValidationError
from .evaluation_planning import EvaluationPlan, build_evolution_evaluation_plan
from .evaluation_scenario_generator import LLMEvaluationScenarioGenerator, ScenarioEvidenceRequirementsGenerator
from .product_evaluation_report import (
    ProductEvaluationReport as GenericProductEvaluationReport,
    product_evaluation_report_api_payload,
)
from .project_intelligence import ProjectIntelligenceError, ProjectIntelligenceRegistration
from .project_scanner import ProjectScanRequest
from .release_decision_gate import evaluate_release_decision
from .scenario_contracts import check_evaluation_plan_readiness
from .skill_pair_evaluation import build_skill_pair_evaluation_change, build_skill_pair_evaluation_target
from .provider_runtime import build_control_plane_client
from .semantic_reporting import ProductDefinition
from .semantic_reporting import ProductEvaluationReport, product_evaluation_api_payload
from .service import AssistantInputError, ProductNotFoundError, Service


app = FastAPI(title="Agent Iteration Guard", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def service() -> Service:
    return Service(os.getenv("AGENTGUARD_DB", "data/agentguard.db"))


class CreateProduct(BaseModel):
    name: str
    description: str = ""


class ProjectIntelligenceRequest(BaseModel):
    agent_manifest: dict[str, object]
    capabilities: list[dict[str, object]]
    runtime_profile: dict[str, object]
    baseline_version: str
    initial_evaluation_history: list[str] = []
    snapshot_version: str | None = None
    benchmark_evidence: list[dict[str, object]] = []


class ProjectScanRequestBody(BaseModel):
    source_kind: Literal["repository", "package", "docker_image"]
    source_ref: str
    version: str
    entrypoint: str | None = None
    runtime_kind: Literal["native_http", "native_command", "package", "docker"] | None = None
    declaration_file: str | None = None


class BenchmarkEvidenceRequest(BaseModel):
    result: dict[str, object]
    source_ref: str = "api:benchmark-import"


class EvaluationKnowledgeRequest(BaseModel):
    component_pattern: str
    common_risks: list[str] = []
    recommended_dimensions: list[str] = []
    scenario_templates: list[str] = []
    source_evaluation_ids: list[str]
    evidence_refs: list[str]
    evidence_level: Literal["observed", "inferred", "mixed"] = "observed"
    sample_count: int = 1


class EvaluationRequestBody(BaseModel):
    component_type: Literal["skill", "skill_pair", "tool"]
    component_name: str
    change_type: Literal["add", "remove", "modify", "replace"]
    candidate_version: str
    baseline_version: str
    candidate_available: bool = False
    candidate_component_name: str | None = None


class ImportVersion(BaseModel):
    source: str
    label: str


class EvolutionIntakeRequest(BaseModel):
    source: str
    baseline_ref: str
    candidate_ref: str
    repository_url: str | None = None
    declared_entrypoint: str | None = None


class MemoryEntryRequest(BaseModel):
    kind: str
    content: str
    evidence_level: str
    evidence_refs: list[str] = []
    applicable_revision_ids: list[str] = []
    status: str = "candidate"
    recorded_by: str = "human"


class MemoryDependencyRequest(BaseModel):
    memory_id: str
    dependent_kind: str
    dependent_id: str
    component_paths: list[str] = []
    component_fingerprints: list[str] = []


class ProductContractRevisionRequest(BaseModel):
    applicable_revision_ids: list[str] = []
    goals: list[str] = []
    non_goals: list[str] = []
    requirements: list[str] = []
    risks: list[str] = []
    evidence_refs: list[str] = []
    status: str = "candidate"


class ProviderBindingRequest(BaseModel):
    role: str
    provider: Literal["deepseek", "openai", "vllm"]
    base_url: str | None = None
    model: str
    expected_environment_variable: str
    credential_source_ref: str
    batch_budget_usd: float
    timeout_seconds: int
    allowed_hosts: list[str] = []
    data_retention_policy: str
    max_model_calls: int = 8
    max_tool_calls: int = 12
    max_wall_time_seconds: int = 360
    max_output_tokens: int = 512
    temperature: float = 0.0
    input_price_per_million_usd: float | None = None
    output_price_per_million_usd: float | None = None
    cache_hit_price_per_million_usd: float | None = None
    pricing_source: str | None = None
    pricing_verified_at: str | None = None


class NativeHarnessContractRequest(BaseModel):
    baseline_entrypoint: str
    candidate_entrypoint: str
    adapter_ref: str
    trace_schema_ref: str
    behavior_mode: str = "reconstruction"
    status: str = "incomplete"


class RuntimeEnvironmentContractRequest(BaseModel):
    docker_ref: str | None = None
    dependency_lock_ref: str | None = None
    model_config_ref: str | None = None
    tools_manifest_ref: str | None = None
    reset_command_ref: str | None = None
    initial_state_ref: str | None = None
    status: str = "incomplete"


class TaskVerifierContractRequest(BaseModel):
    task_spec_ref: str
    verifier_ref: str
    pass_iff: str
    initial_state_ref: str
    trace_evidence_ref: str
    status: str = "incomplete"


class RuntimePreflightRequest(BaseModel):
    environment_contract_id: str
    checks: list[EnvironmentCheck]


class HistoricalReplayEvidenceRequest(BaseModel):
    revision_id: str
    trace_sha256: str
    tool_result_sha256: str
    execution_log_sha256: str
    initial_state_sha256: str
    verifier_evidence_ref: str


class ProductEvaluationReportRequest(BaseModel):
    report: dict[str, object]


class GenericProductEvaluationReportRequest(BaseModel):
    report: dict[str, object]


class EvaluationReadinessRequest(BaseModel):
    evaluation_plan: dict[str, object]
    fixture_root: str | None = None


class EvaluationPlanRequest(BaseModel):
    evaluation_request_id: str
    provider_binding_id: str
    evaluation_name: str
    product_definition: dict[str, object]
    knowledge_pattern: str | None = None


def _unprocessable(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _provider_api_key(binding: ProviderBinding) -> str:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    api_key = os.getenv(binding.expected_environment_variable)
    if not api_key:
        raise ValueError(f"{binding.expected_environment_variable} is required at runtime")
    return api_key


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "product": "agent-iteration-guard"}


@app.get("/api/v1/products")
def products():
    return service().products()


@app.post("/api/v1/products")
def create(body: CreateProduct):
    product, version = service().create(body.name, body.description)
    return {"product": product, "version": version}


@app.post("/api/v1/projects/{project_id}/intelligence")
def register_project_intelligence(project_id: str, body: ProjectIntelligenceRequest):
    try:
        registration = ProjectIntelligenceRegistration.model_validate({
            "project_id": project_id,
            **body.model_dump(exclude={"benchmark_evidence"}),
        })
        app_service = service()
        intelligence = app_service.register_project_intelligence(registration)
        imported = [
            app_service.import_benchmark_evidence(
                project_id,
                item.get("result") if isinstance(item.get("result"), dict) else item,
                source_ref=str(item.get("source_ref") or "api:benchmark-import"),
            )
            for item in body.benchmark_evidence
        ]
        return {"intelligence": intelligence, "benchmark_evidence": imported}
    except (ProjectIntelligenceError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/snapshots")
def register_project_snapshot(project_id: str, body: ProjectIntelligenceRequest):
    try:
        registration = ProjectIntelligenceRegistration.model_validate({
            "project_id": project_id,
            **body.model_dump(exclude={"benchmark_evidence"}),
        })
        result = service().register_project_snapshot(registration)
        return result
    except (ProjectIntelligenceError, ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/scan")
def scan_project(project_id: str, body: ProjectScanRequestBody):
    try:
        return service().scan_project(ProjectScanRequest(
            project_id=project_id,
            source_kind=body.source_kind,
            source_ref=body.source_ref,
            version=body.version,
            entrypoint=body.entrypoint,
            runtime_kind=body.runtime_kind,
            declaration_file=body.declaration_file,
        ))
    except (ProjectIntelligenceError, ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/scans")
def project_scans(project_id: str):
    try:
        if service().project_intelligence(project_id) is None and not service().project_scans(project_id):
            raise HTTPException(status_code=404, detail="project not found")
        return service().project_scans(project_id)
    except HTTPException:
        raise
    except ValueError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/runtime-preflight")
def runtime_preflight(project_id: str, version: str, source_root: str | None = None):
    try:
        return service().runtime_preflight(
            project_id,
            version,
            source_root=Path(source_root) if source_root else None,
        )
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ValueError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/runtime-comparability")
def runtime_comparability(
    project_id: str,
    baseline_version: str,
    candidate_version: str,
    baseline_source_root: str | None = None,
    candidate_source_root: str | None = None,
):
    try:
        return service().runtime_comparability(
            project_id,
            baseline_version,
            candidate_version,
            baseline_source_root=Path(baseline_source_root) if baseline_source_root else None,
            candidate_source_root=Path(candidate_source_root) if candidate_source_root else None,
        )
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ValueError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluation-knowledge")
def get_evaluation_knowledge(project_id: str, component_pattern: str | None = None):
    try:
        if service().project_intelligence(project_id) is None:
            raise HTTPException(status_code=404, detail="project intelligence not found")
        return service().evaluation_knowledge(project_id, component_pattern)
    except HTTPException:
        raise
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/evaluation-knowledge")
def record_evaluation_knowledge(project_id: str, body: EvaluationKnowledgeRequest):
    try:
        return service().record_evaluation_knowledge(
            EvaluationKnowledge(project_id=project_id, **body.model_dump())
        )
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/benchmark-evidence")
def get_benchmark_evidence(project_id: str):
    try:
        if service().project_intelligence(project_id) is None:
            raise HTTPException(status_code=404, detail="project intelligence not found")
        return service().benchmark_evidence(project_id)
    except HTTPException:
        raise
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/benchmark-evidence")
def import_benchmark_evidence(project_id: str, body: BenchmarkEvidenceRequest):
    try:
        return service().import_benchmark_evidence(
            project_id, body.result, source_ref=body.source_ref
        )
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/intelligence")
def get_project_intelligence(project_id: str):
    try:
        intelligence = service().project_intelligence(project_id)
        if intelligence is None:
            raise HTTPException(status_code=404, detail="project intelligence not found")
        return intelligence
    except ProjectIntelligenceError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/evaluations")
def create_evaluation_request(project_id: str, body: EvaluationRequestBody):
    try:
        request = EvaluationRequest.model_validate({
            "project_id": project_id,
            **body.model_dump(exclude={"candidate_available", "candidate_component_name"}),
        })
        return service().create_evaluation_request(
            request,
            candidate_available=body.candidate_available,
            candidate_component_name=body.candidate_component_name,
        )
    except (ProductNotFoundError, EvaluationRequestValidationError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/provider-bindings")
def record_project_provider_binding(project_id: str, body: ProviderBindingRequest):
    try:
        return service().record_provider_binding(
            project_id,
            ProviderBinding(project_id=project_id, **body.model_dump()),
        )
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/{request_id}")
def get_evaluation_request(project_id: str, request_id: str):
    request = service().evaluation_request(project_id, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="evaluation request not found")
    return request


@app.post("/api/v1/projects/{project_id}/evaluations/plan")
def generate_evaluation_plan(project_id: str, body: EvaluationPlanRequest):
    """Generate and persist a control-plane Evaluation Plan for a registered Pair."""

    try:
        app_service = service()
        intelligence = app_service.project_intelligence(project_id)
        if intelligence is None:
            raise HTTPException(status_code=404, detail="project intelligence not found")
        request = app_service.evaluation_request(project_id, body.evaluation_request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="evaluation request not found")
        product_definition = ProductDefinition.model_validate(body.product_definition)
        target = build_skill_pair_evaluation_target(intelligence, request.component_name, product_definition)
        target = target.model_copy(update={
            "component_pattern": body.knowledge_pattern or target.component_type,
            "evaluation_knowledge": app_service.evaluation_knowledge_for_target(
                project_id,
                component_pattern=body.knowledge_pattern or target.component_type,
                component_type=target.component_type,
            )
        })
        change = build_skill_pair_evaluation_change(request, evaluation_name=body.evaluation_name)
        binding = app_service.provider_binding(project_id, body.provider_binding_id)
        if binding.role != "control_plane":
            raise ValueError("Evaluation Plan generation requires a control_plane ProviderBinding.")
        provider = build_control_plane_client(binding, _provider_api_key(binding))
        plan = build_evolution_evaluation_plan(
            target,
            change,
            scenario_generator=LLMEvaluationScenarioGenerator(provider, binding),
            evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
        )
        return app_service.save_evaluation_plan(plan)
    except HTTPException:
        raise
    except (ProjectIntelligenceError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/plans/{plan_id}")
def get_evaluation_plan(project_id: str, plan_id: str):
    plan = service().evaluation_plan(project_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="evaluation plan not found")
    return plan


@app.post("/api/v1/projects/{project_id}/evaluations/readiness")
def evaluation_readiness(project_id: str, body: EvaluationReadinessRequest):
    try:
        intelligence = service().project_intelligence(project_id)
        if intelligence is None:
            raise HTTPException(status_code=404, detail="project intelligence not found")
        raw_plan = body.evaluation_plan.get("evaluation_plan", body.evaluation_plan)
        if not isinstance(raw_plan, dict):
            raise ValueError("Evaluation readiness input must contain an EvaluationPlan object.")
        plan = EvaluationPlan.model_validate(raw_plan)
        if plan.project_id != project_id:
            raise ValueError("Evaluation Plan project_id does not match the project path.")
        return check_evaluation_plan_readiness(
            plan,
            intelligence.runtime_profile.fixture_catalog,
            fixture_root=Path(body.fixture_root) if body.fixture_root else None,
        )
    except HTTPException:
        raise
    except (ProjectIntelligenceError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/release-decision")
def release_decision(project_id: str, body: GenericProductEvaluationReportRequest):
    try:
        report = GenericProductEvaluationReport.model_validate(body.report)
        if report.subject.product_id != project_id:
            raise ValueError("Product Evaluation Report project does not match the project path.")
        return evaluate_release_decision(report)
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{product_id}/versions")
def import_version(product_id: str, body: ImportVersion):
    try:
        return {"version": service().import_version(product_id, Path(body.source), body.label)}
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error


@app.post("/api/v1/products/{project_id}/evolution/intake")
def intake_agent_evolution(project_id: str, body: EvolutionIntakeRequest):
    try:
        return service().intake_agent_evolution(project_id, Path(body.source), body.baseline_ref, body.candidate_ref, repository_url=body.repository_url, declared_entrypoint=body.declared_entrypoint).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except EvolutionIntakeError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/products/{project_id}/evolution/reports/{report_id}")
def evolution_report(project_id: str, report_id: str):
    try:
        return service().evolution_report(project_id, report_id)
    except (ProductNotFoundError, AssistantInputError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/reports/product")
def product_evaluation_report(project_id: str, body: ProductEvaluationReportRequest):
    """Validate and return the portable ProductEvaluationReport schema."""
    try:
        report = ProductEvaluationReport.model_validate(body.report)
        if report.subject.get("product_id") != project_id:
            raise ValueError("ProductEvaluationReport belongs to a different project.")
        return product_evaluation_api_payload(report)
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evaluation-reports")
def generic_product_evaluation_report(project_id: str, body: GenericProductEvaluationReportRequest):
    """Validate and return the portable generic ProductEvaluationReport."""
    try:
        report = GenericProductEvaluationReport.model_validate(body.report)
        if report.subject.product_id != project_id:
            raise ValueError("ProductEvaluationReport belongs to a different project.")
        return product_evaluation_report_api_payload(report)
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/contracts")
def record_product_contract_revision(project_id: str, body: ProductContractRevisionRequest):
    try:
        return service().record_product_contract_revision(project_id, ProductContractRevision(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/provider-bindings")
def record_provider_binding(project_id: str, body: ProviderBindingRequest):
    try:
        return service().record_provider_binding(project_id, ProviderBinding(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/memory")
def record_memory_entry(project_id: str, body: MemoryEntryRequest):
    try:
        return service().record_memory_entry(project_id, MemoryEntry(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/memory/dependencies")
def record_memory_dependency(project_id: str, body: MemoryDependencyRequest):
    try:
        return service().record_memory_dependency(project_id, MemoryDependency(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{changeset_id}/propagate-stale")
def propagate_evolution_stale(project_id: str, changeset_id: str):
    try:
        return service().propagate_evolution_stale(project_id, changeset_id)
    except (ProductNotFoundError, EvolutionIntakeError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/native-harness-contract")
def record_native_harness_contract(project_id: str, case_id: str, body: NativeHarnessContractRequest):
    try:
        return service().record_native_harness_contract(project_id, NativeHarnessContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/environment-contract")
def record_runtime_environment_contract(project_id: str, case_id: str, body: RuntimeEnvironmentContractRequest):
    try:
        return service().record_runtime_environment_contract(project_id, RuntimeEnvironmentContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/task-verifier-contract")
def record_task_verifier_contract(project_id: str, case_id: str, body: TaskVerifierContractRequest):
    try:
        return service().record_task_verifier_contract(project_id, TaskVerifierContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/environment-preflight")
def record_runtime_preflight(project_id: str, case_id: str, body: RuntimePreflightRequest):
    try:
        return service().record_runtime_preflight(project_id, RuntimeEnvironmentPreflight(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/replay-evidence")
def record_historical_replay_evidence(project_id: str, case_id: str, body: HistoricalReplayEvidenceRequest):
    try:
        return service().record_historical_replay_evidence(project_id, HistoricalReplayEvidence(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/admission")
def assess_evolution_admission(project_id: str, case_id: str):
    try:
        return service().assess_evolution_admission(project_id, case_id).as_dict()
    except (ProductNotFoundError, EvolutionIntakeError) as error:
        raise _unprocessable(error) from error
