import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import File, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

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
from .copilot import CopilotMessageRequest, CopilotService, ProviderCopilotReasoner
from .evaluation_memory import EvaluationKnowledge
from .evaluation_execution_config import (
    EvaluationExecutionConfiguration,
    EvaluationExecutionConfigurationMetadata,
    metadata as execution_configuration_metadata,
)
from .evolution import EvolutionIntakeError
from .evaluation_request import EvaluationRequest, EvaluationRequestValidationError
from .evaluation_planning import EvaluationPlan, bind_evaluation_scope
from .evaluation_dispatch import EvaluationDispatchError, build_evaluation_plan_for_request
from .evaluation_scope import freeze_evaluation_scope
from .evaluation_suite import ScenarioSuiteConfig, default_scenario_suite_config
from .evaluation_orchestration import (
    adapt_evaluation_run_evidence,
    build_product_evaluation_report,
    execute_evaluation_run,
    planned_trial_count,
)
from .evaluation_run import EvaluationRun, append_event, content_ref
from .evaluation_report import EvaluationReportRecord
from .project_upload import ProjectUpload, fingerprint_file
from .evaluation_scenario_generator import LLMEvaluationScenarioGenerator, ScenarioEvidenceRequirementsGenerator
from .product_evaluation_report import (
    ProductEvaluationReport as GenericProductEvaluationReport,
    assemble_product_evaluation_report,
    product_evaluation_report_api_payload,
)
from .project_intelligence import ProjectIntelligenceError, ProjectIntelligenceRegistration
from .project_scanner import ProjectScanRequest
from .release_decision_gate import evaluate_release_decision
from .scenario_contracts import ScenarioReadinessCheck, check_evaluation_plan_readiness
from .provider_runtime import ProviderRuntimeError, build_control_plane_client
from .semantic_reporting import ProductDefinition
from .runtime_onboarding import RuntimeDraftReview
from .target_onboarding import TargetEnvironmentCache
from .semantic_reporting import ProductEvaluationReport, product_evaluation_api_payload
from .product_evaluation_renderers import render_product_evaluation_html, render_product_evaluation_markdown
from .product_report_template import default_product_report_template
from .report_view_model import normalize_product_evaluation_report, project_context_from_intelligence
from .service import AssistantInputError, ProductNotFoundError, Service
from .targets import TargetInfrastructureError
from .interaction_runner import InteractionRunnerError, OracleExecutionError, TargetExecutionError


app = FastAPI(title="Agent Iteration Guard", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:5175", "http://127.0.0.1:5175",
    ],
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
    pair_members: list[str] = Field(default_factory=list, max_length=2)
    scenario_suite: ScenarioSuiteConfig | None = None
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


class ProviderBindingMetadata(BaseModel):
    provider_binding_id: str
    role: Literal["control_plane", "sut_native"]
    provider: Literal["deepseek", "openai", "vllm"]
    model: str
    expected_environment_variable: str | None = None
    allowed_hosts: list[str]
    batch_budget_usd: float
    timeout_seconds: int
    max_model_calls: int
    max_tool_calls: int
    max_output_tokens: int
    status: Literal["available", "unavailable"]
    pricing_verified: bool


class ProviderOnboardingRequest(BaseModel):
    provider: Literal["deepseek", "openai", "vllm"]
    model: str = Field(min_length=1)
    base_url: str | None = None
    credential_environment_variable: str = Field(min_length=1, max_length=128)
    role: Literal["control_plane", "sut_native"] = "control_plane"
    batch_budget_usd: float = Field(default=1.0, ge=0)
    timeout_seconds: int = Field(default=60, gt=0, le=900)
    max_model_calls: int = Field(default=8, gt=0, le=100)
    max_tool_calls: int = Field(default=12, gt=0, le=100)
    max_wall_time_seconds: int = Field(default=360, gt=0, le=3600)
    max_output_tokens: int = Field(default=512, gt=0, le=16384)
    temperature: float = Field(default=0.0, ge=0, le=2)


class CredentialAvailability(BaseModel):
    provider: Literal["deepseek", "openai", "vllm"]
    environment_variable: str
    status: Literal["available", "unavailable"]


class RuntimeDraftSaveRequest(RuntimeDraftReview):
    pass


class SkillPairSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    members: list[str] = Field(min_length=2, max_length=2)


class ProjectUploadMetadata(BaseModel):
    upload_id: str
    project_id: str
    original_filename: str
    source_kind: Literal["repository", "package"]
    source_ref: str
    source_fingerprint: str
    size_bytes: int
    media_type: str | None = None
    status: Literal["stored"]
    created_at: str


class ProjectSummary(BaseModel):
    project_id: str
    agent_name: str
    purpose: str
    status: str
    baseline_version: str
    latest_version: str
    latest_scan_id: str | None = None
    latest_diff_id: str | None = None
    changed_component_count: int


class EvaluationExecutionConfigurationRequest(BaseModel):
    name: str
    manifest_path: str
    cache_root: str
    run_root_parent: str
    oracle_command: list[str] = Field(min_length=1, max_length=32)
    oracle_id: str
    oracle_type: Literal["rule_based", "frozen_lookup", "structured_state"] = "rule_based"
    oracle_version: str = "1.0"
    oracle_cwd: str | None = None
    target_provider_binding_id: str | None = None


class EvaluationReportMetadata(BaseModel):
    report_id: str
    project_id: str
    source: Literal["run", "import"]
    run_id: str | None = None
    evaluation_plan_id: str | None = None
    scope_id: str | None = None
    created_at: str


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
    execution_config_id: str | None = None


class EvaluationPlanRequest(BaseModel):
    evaluation_request_id: str
    provider_binding_id: str
    evaluation_name: str
    product_definition: dict[str, object]
    knowledge_pattern: str | None = None
    target_provider_binding_id: str | None = None


class EvaluationRunRequest(BaseModel):
    evaluation_plan_id: str
    execution_config_id: str | None = None
    manifest_path: str | None = None
    cache_root: str | None = None
    run_root: str | None = None
    fixture_root: str | None = None
    evaluation_id: str | None = None
    oracle_command: list[str] = Field(default_factory=list)
    oracle_id: str | None = None
    oracle_type: Literal["rule_based", "frozen_lookup", "structured_state"] = "rule_based"
    oracle_version: str = "1.0"
    oracle_cwd: str | None = None
    target_provider_binding_id: str | None = None

    @model_validator(mode="after")
    def validate_execution_source(self) -> "EvaluationRunRequest":
        if self.execution_config_id:
            return self
        required = {
            "manifest_path": self.manifest_path,
            "cache_root": self.cache_root,
            "run_root": self.run_root,
            "oracle_id": self.oracle_id,
        }
        missing = [name for name, value in required.items() if not value]
        if not self.oracle_command:
            missing.append("oracle_command")
        if missing:
            raise ValueError(
                "Evaluation Run requires execution_config_id or legacy execution fields: "
                + ", ".join(missing)
            )
        return self


class EvaluationRunReportRequest(BaseModel):
    run_id: str
    provider_binding_id: str
    product_definition: dict[str, object]


class ProjectReportImportRequest(BaseModel):
    report: dict[str, object]


def _unprocessable(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _provider_api_key(binding: ProviderBinding) -> str:
    _load_server_credentials()
    api_key = os.getenv(binding.expected_environment_variable)
    if not api_key:
        raise ValueError(f"{binding.expected_environment_variable} is required at runtime")
    return api_key


def _provider_binding_metadata(binding: ProviderBinding) -> ProviderBindingMetadata:
    _load_server_credentials()
    return ProviderBindingMetadata(
        provider_binding_id=binding.provider_binding_id,
        role=binding.role,
        provider=binding.provider,
        model=binding.model,
        expected_environment_variable=binding.expected_environment_variable,
        allowed_hosts=list(binding.allowed_hosts),
        batch_budget_usd=binding.batch_budget_usd,
        timeout_seconds=binding.timeout_seconds,
        max_model_calls=binding.max_model_calls,
        max_tool_calls=binding.max_tool_calls,
        max_output_tokens=binding.max_output_tokens,
        status=("available" if os.getenv(binding.expected_environment_variable) else "unavailable"),
        pricing_verified=bool(binding.pricing_source and binding.pricing_verified_at),
    )


def _load_server_credentials() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env", override=False)


_PROVIDER_DEFAULTS = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    "vllm": ("VLLM_API_KEY", "http://127.0.0.1:8000/v1"),
}
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _provider_from_onboarding(project_id: str, body: ProviderOnboardingRequest) -> ProviderBinding:
    if not _ENVIRONMENT_NAME.fullmatch(body.credential_environment_variable):
        raise ValueError("Credential environment variable must be a valid environment-variable name.")
    default_env, default_base_url = _PROVIDER_DEFAULTS[body.provider]
    base_url = (body.base_url or default_base_url).rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provider endpoint must be an absolute http(s) URL.")
    return ProviderBinding(
        project_id=project_id,
        role=body.role,
        provider=body.provider,
        base_url=base_url,
        model=body.model,
        expected_environment_variable=body.credential_environment_variable or default_env,
        credential_source_ref=f"env:{body.credential_environment_variable or default_env}",
        batch_budget_usd=body.batch_budget_usd,
        timeout_seconds=body.timeout_seconds,
        allowed_hosts=[parsed.hostname],
        data_retention_policy="provider-configured; review before production data",
        max_model_calls=body.max_model_calls,
        max_tool_calls=body.max_tool_calls,
        max_wall_time_seconds=body.max_wall_time_seconds,
        max_output_tokens=body.max_output_tokens,
        temperature=body.temperature,
    )


def _upload_root() -> Path:
    return Path(os.getenv("AGENTGUARD_UPLOAD_ROOT", "D:/codexdata/agentguard-uploads")).resolve()


def _is_supported_package_filename(filename: str) -> bool:
    normalized = filename.lower()
    return normalized.endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"))


def _upload_metadata(upload: ProjectUpload) -> ProjectUploadMetadata:
    return ProjectUploadMetadata(
        upload_id=upload.upload_id,
        project_id=upload.project_id,
        original_filename=upload.original_filename,
        source_kind=upload.source_kind,
        source_ref=upload.source_ref,
        source_fingerprint=upload.source_fingerprint,
        size_bytes=upload.size_bytes,
        media_type=upload.media_type,
        status=upload.status,
        created_at=upload.created_at,
    )


def _project_summary(app_service: Service, intelligence) -> ProjectSummary:
    scans = app_service.project_scans(intelligence.project_id)
    latest_scan = scans[-1] if scans else None
    changed_count = sum(
        1
        for item in (intelligence.latest_diff.component_changes if intelligence.latest_diff else [])
        if item.status in {"added", "removed", "changed"}
    )
    return ProjectSummary(
        project_id=intelligence.project_id,
        agent_name=intelligence.agent_manifest.agent_name,
        purpose=intelligence.agent_manifest.purpose,
        status=intelligence.status,
        baseline_version=intelligence.baseline_snapshot.baseline_version,
        latest_version=(intelligence.latest_snapshot.version if intelligence.latest_snapshot else intelligence.baseline_snapshot.baseline_version),
        latest_scan_id=latest_scan.scan_id if latest_scan else None,
        latest_diff_id=intelligence.latest_diff.diff_id if intelligence.latest_diff else None,
        changed_component_count=changed_count,
    )


def _report_metadata(record: EvaluationReportRecord) -> EvaluationReportMetadata:
    return EvaluationReportMetadata(
        report_id=record.report_id,
        project_id=record.project_id,
        source=record.source,
        run_id=record.run_id,
        evaluation_plan_id=record.evaluation_plan_id,
        scope_id=record.scope_id,
        created_at=record.created_at,
    )


def _demo_report_bundle() -> tuple[GenericProductEvaluationReport, dict[str, str], object]:
    report_path = Path(__file__).parents[2] / "examples" / "reports" / "lighttable-product-evaluation.zh-CN" / "product-evaluation-report.json"
    report = GenericProductEvaluationReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    context = {
        "project_id": "lighttable-pair-nutrition",
        "project_name": "LightTable",
        "purpose": "当前已加载 LightTable 项目，可查看版本、能力变化与评估结果。",
        "baseline": "main-fa774ef",
        "candidate": "candidate-gui-v2-20260806",
        "runtime": "native_command",
    }
    return report, context, evaluate_release_decision(report)


def _run_failure_classification(error: Exception) -> str:
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, OracleExecutionError):
        return "oracle_failure"
    if isinstance(cause, (TargetExecutionError, InteractionRunnerError)):
        return "target_failure"
    if isinstance(cause, TargetInfrastructureError):
        return "infrastructure_failure"
    if isinstance(cause, ProviderRuntimeError):
        return "provider_failure"
    return "validation_failure"


def _run_evidence(app_service: Service, project_id: str, run: EvaluationRun):
    if run.artifact is None or run.status not in {"completed", "failed"}:
        raise ValueError("Evaluation Run does not contain completed execution evidence.")
    plan = app_service.evaluation_plan(project_id, run.evaluation_plan_id)
    if plan is None:
        raise ValueError("Evaluation Plan for the Evaluation Run was not found.")
    request = app_service.evaluation_request(project_id, run.evaluation_request_id)
    if request is None:
        raise ValueError("Evaluation Request for the Evaluation Run was not found.")
    return adapt_evaluation_run_evidence(
        plan,
        request,
        run_id=run.run_id,
        scope_id=run.scope_id,
        artifact=run.artifact,
    )


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


@app.get("/api/v1/projects")
def list_projects():
    app_service = service()
    return [_project_summary(app_service, item) for item in app_service.project_intelligences()]


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


@app.post("/api/v1/projects/{project_id}/uploads")
def upload_project_source(
    project_id: str,
    file: UploadFile = File(...),
    source_kind: Literal["package"] = "package",
):
    """Store one browser-uploaded package under a server-owned immutable ref."""

    try:
        app_service = service()
        original_filename = Path(file.filename or "").name
        if not original_filename or original_filename in {".", ".."}:
            raise ValueError("Uploaded project package must have a filename.")
        if not _is_supported_package_filename(original_filename):
            raise ValueError("Unsupported source package; upload a .zip, .tar, .tar.gz, .tgz, .tar.bz2, or .tar.xz archive.")
        upload = ProjectUpload(
            project_id=project_id,
            original_filename=original_filename,
            source_kind=source_kind,
            source_path=str(_upload_root() / "pending"),
            source_ref="pending",
            source_fingerprint="0" * 64,
            size_bytes=0,
            media_type=file.content_type,
        )
        target = _upload_root() / upload.upload_id / original_filename
        target.parent.mkdir(parents=True, exist_ok=False)
        try:
            with target.open("wb") as handle:
                for chunk in iter(lambda: file.file.read(1024 * 1024), b""):
                    handle.write(chunk)
        except OSError:
            if target.exists():
                target.unlink()
            target.parent.rmdir()
            raise
        fingerprint, size = fingerprint_file(target)
        stored = upload.model_copy(update={
            "source_path": str(target.resolve()),
            "source_ref": f"upload://{upload.upload_id}",
            "source_fingerprint": fingerprint,
            "size_bytes": size,
        })
        return _upload_metadata(app_service.save_project_upload(stored))
    except HTTPException:
        raise
    except (ProductNotFoundError, ValueError, OSError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/uploads")
def list_project_uploads(project_id: str):
    try:
        return [_upload_metadata(item) for item in service().project_uploads(project_id)]
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error


@app.get("/api/v1/projects/{project_id}/uploads/{upload_id}")
def get_project_upload(project_id: str, upload_id: str):
    upload = service().project_upload(project_id, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="uploaded source not found")
    return _upload_metadata(upload)


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
        app_service = service()
        source_ref = body.source_ref
        if source_ref.startswith("upload://"):
            upload_id = source_ref.removeprefix("upload://")
            upload = app_service.project_upload(project_id, upload_id)
            if upload is None:
                raise HTTPException(status_code=404, detail="uploaded source not found")
            if upload.source_kind != body.source_kind:
                raise ValueError("Scan source_kind does not match the uploaded source.")
            source_ref = upload.source_path
        return app_service.scan_project(ProjectScanRequest(
            project_id=project_id,
            source_kind=body.source_kind,
            source_ref=source_ref,
            version=body.version,
            entrypoint=body.entrypoint,
            runtime_kind=body.runtime_kind,
            declaration_file=body.declaration_file,
        ))
    except HTTPException:
        raise
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


@app.post("/api/v1/projects/{project_id}/copilot/messages")
def copilot_message(project_id: str, body: CopilotMessageRequest):
    app_service = service()
    try:
        reasoner = None
        if body.provider_binding_id:
            binding = app_service.provider_binding(project_id, body.provider_binding_id)
            if binding.role != "control_plane":
                raise ValueError("Copilot requires a control_plane ProviderBinding.")
            reasoner = ProviderCopilotReasoner(
                build_control_plane_client(binding, _provider_api_key(binding))
            )
        return CopilotService(app_service).message(project_id, body, reasoner=reasoner)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except (EvaluationRequestValidationError, ProviderRuntimeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/copilot/actions/{action_id}/confirm")
def confirm_copilot_action(project_id: str, action_id: str):
    try:
        return CopilotService(service()).confirm(project_id, action_id)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except (EvaluationRequestValidationError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/copilot/actions/{action_id}/cancel")
def cancel_copilot_action(project_id: str, action_id: str):
    try:
        return CopilotService(service()).cancel(project_id, action_id)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/provider-bindings")
def record_project_provider_binding(project_id: str, body: ProviderBindingRequest):
    try:
        binding = service().record_provider_binding(
            project_id,
            ProviderBinding(project_id=project_id, **body.model_dump()),
        )
        return _provider_binding_metadata(binding)
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/provider-bindings")
def list_project_provider_bindings(project_id: str):
    try:
        return [_provider_binding_metadata(item) for item in service().provider_bindings(project_id)]
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error


@app.get("/api/v1/projects/{project_id}/provider-bindings/{provider_binding_id}")
def get_project_provider_binding(project_id: str, provider_binding_id: str):
    try:
        return _provider_binding_metadata(service().provider_binding(project_id, provider_binding_id))
    except EvolutionIntakeError as error:
        raise HTTPException(status_code=404, detail="provider binding not found") from error


@app.get("/api/v1/projects/{project_id}/provider-credentials")
def provider_credential_availability(project_id: str):
    if service().project_intelligence(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    _load_server_credentials()
    return [
        CredentialAvailability(
            provider=provider,
            environment_variable=environment_variable,
            status="available" if os.getenv(environment_variable) else "unavailable",
        )
        for provider, (environment_variable, _) in _PROVIDER_DEFAULTS.items()
    ]


@app.post("/api/v1/projects/{project_id}/provider-bindings/onboard")
def onboard_provider_binding(project_id: str, body: ProviderOnboardingRequest):
    """Validate a natural model configuration and persist its internal binding.

    This checks only endpoint shape and credential presence. It deliberately
    does not send an unbounded test prompt or disclose the credential.
    """
    try:
        _load_server_credentials()
        binding = _provider_from_onboarding(project_id, body)
        if not os.getenv(binding.expected_environment_variable):
            raise ValueError(
                f"Credential environment variable {binding.expected_environment_variable} is unavailable to the server."
            )
        return _provider_binding_metadata(service().record_provider_binding(project_id, binding))
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/runtime-drafts")
def get_runtime_draft(project_id: str, snapshot_version: str):
    try:
        return service().runtime_configuration_draft(project_id, snapshot_version)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/runtime-drafts/{draft_id}/save")
def save_reviewed_runtime_draft(project_id: str, draft_id: str, body: RuntimeDraftSaveRequest):
    try:
        config = service().save_reviewed_runtime_configuration(
            project_id, draft_id, RuntimeDraftReview.model_validate(body.model_dump()),
        )
        return execution_configuration_metadata(config)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/skill-pairs")
def save_reusable_skill_pair(project_id: str, body: SkillPairSaveRequest):
    try:
        return service().save_skill_pair(project_id, body.name, body.members)
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/evaluation-execution-configurations")
def register_evaluation_execution_configuration(
    project_id: str,
    body: EvaluationExecutionConfigurationRequest,
):
    try:
        config = EvaluationExecutionConfiguration(project_id=project_id, **body.model_dump())
        if config.target_provider_binding_id:
            binding = service().provider_binding(project_id, config.target_provider_binding_id)
            if binding.role != "sut_native":
                raise ValueError("Execution Configuration target binding must be sut_native.")
        return execution_configuration_metadata(service().save_evaluation_execution_configuration(config))
    except HTTPException:
        raise
    except (EvolutionIntakeError, ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluation-execution-configurations")
def list_evaluation_execution_configurations(project_id: str):
    try:
        return [
            execution_configuration_metadata(item)
            for item in service().evaluation_execution_configurations(project_id)
        ]
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error


@app.get("/api/v1/projects/{project_id}/evaluations/scenario-suite-defaults")
def get_scenario_suite_defaults(project_id: str):
    if service().project_intelligence(project_id) is None:
        raise HTTPException(status_code=404, detail="project intelligence not found")
    return {
        "skill": default_scenario_suite_config("skill").model_dump(mode="json"),
        "skill_pair": default_scenario_suite_config("skill_pair").model_dump(mode="json"),
    }


@app.get("/api/v1/projects/{project_id}/evaluations/{request_id}")
def get_evaluation_request(project_id: str, request_id: str):
    request = service().evaluation_request(project_id, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="evaluation request not found")
    return request


@app.post("/api/v1/projects/{project_id}/evaluations/plan")
def generate_evaluation_plan(project_id: str, body: EvaluationPlanRequest):
    """Generate and persist a control-plane Evaluation Plan for one request."""

    try:
        app_service = service()
        intelligence = app_service.project_intelligence(project_id)
        if intelligence is None:
            raise HTTPException(status_code=404, detail="project intelligence not found")
        request = app_service.evaluation_request(project_id, body.evaluation_request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="evaluation request not found")
        product_definition = ProductDefinition.model_validate(body.product_definition)
        pattern = body.knowledge_pattern or request.component_type
        binding = app_service.provider_binding(project_id, body.provider_binding_id)
        if binding.role != "control_plane":
            raise ValueError("Evaluation Plan generation requires a control_plane ProviderBinding.")
        target_binding = None
        if body.target_provider_binding_id:
            target_binding = app_service.provider_binding(project_id, body.target_provider_binding_id)
            if target_binding.role != "sut_native":
                raise ValueError("Evaluation Scope target binding requires a sut_native ProviderBinding.")
        provider = build_control_plane_client(binding, _provider_api_key(binding))
        plan = build_evaluation_plan_for_request(
            request,
            intelligence,
            product_definition,
            evaluation_name=body.evaluation_name,
            scenario_generator=LLMEvaluationScenarioGenerator(provider, binding),
            evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
            knowledge_pattern=pattern,
            evaluation_knowledge=app_service.evaluation_knowledge_for_target(
                project_id,
                component_pattern=pattern,
                component_type=request.component_type,
            ),
        )
        plan = bind_evaluation_scope(
            plan,
            freeze_evaluation_scope(
                request,
                intelligence,
                binding,
                planned_trial_count=planned_trial_count(plan),
                target_binding=target_binding,
            ),
        )
        app_service.bind_evaluation_request_scope(request, plan.evaluation_scope.scope_id)
        return app_service.save_evaluation_plan(plan)
    except HTTPException:
        raise
    except (ProjectIntelligenceError, EvaluationDispatchError, ProviderRuntimeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/plans/{plan_id}")
def get_evaluation_plan(project_id: str, plan_id: str):
    plan = service().evaluation_plan(project_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="evaluation plan not found")
    return plan


@app.post("/api/v1/projects/{project_id}/evaluations/runs")
def run_evaluation(project_id: str, body: EvaluationRunRequest):
    """Execute a frozen Skill or Skill Pair plan through the shared runner."""

    app_service = service()
    plan = app_service.evaluation_plan(project_id, body.evaluation_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="evaluation plan not found")
    request = app_service.evaluation_request(project_id, plan.change_id)
    if request is None:
        raise HTTPException(status_code=404, detail="evaluation request not found")
    intelligence = app_service.project_intelligence(project_id)
    if intelligence is None:
        raise HTTPException(status_code=404, detail="project intelligence not found")
    if plan.evaluation_scope is None:
        raise _unprocessable(ValueError("Evaluation execution requires a frozen Evaluation Scope."))
    run = EvaluationRun(
        evaluation_id=body.evaluation_id or f"evaluation_{plan.plan_id}",
        project_id=project_id,
        evaluation_request_id=request.request_id,
        evaluation_plan_id=plan.plan_id,
        execution_config_id=body.execution_config_id,
        scope_id=plan.evaluation_scope.scope_id,
        status="running",
    )
    run = append_event(
        run,
        stage="execution",
        status="running",
        detail="Evaluation target execution started.",
    )
    app_service.save_evaluation_run(run)
    try:
        execution_config = None
        if body.execution_config_id:
            execution_config = app_service.evaluation_execution_configuration(
                project_id,
                body.execution_config_id,
            )
            if execution_config is None:
                raise ValueError("Evaluation execution configuration was not found for this project.")
            if execution_config.snapshot_version and execution_config.snapshot_version != plan.evaluation_scope.candidate_version:
                raise ValueError("Project runtime configuration is stale for this Evaluation Plan snapshot; review the runtime again.")
            if body.target_provider_binding_id and body.target_provider_binding_id != execution_config.target_provider_binding_id:
                raise ValueError("Target ProviderBinding does not match the Execution Configuration.")
            manifest_path = Path(execution_config.manifest_path)
            cache_root = Path(execution_config.cache_root)
            run_root = Path(execution_config.run_root_parent) / run.evaluation_id
            oracle_command = tuple(execution_config.oracle_command)
            oracle_id = execution_config.oracle_id
            oracle_type = execution_config.oracle_type
            oracle_version = execution_config.oracle_version
            oracle_cwd = Path(execution_config.oracle_cwd) if execution_config.oracle_cwd else None
            target_provider_binding_id = execution_config.target_provider_binding_id
        else:
            manifest_path = Path(body.manifest_path)
            cache_root = Path(body.cache_root)
            run_root = Path(body.run_root)
            oracle_command = tuple(body.oracle_command)
            oracle_id = body.oracle_id
            oracle_type = body.oracle_type
            oracle_version = body.oracle_version
            oracle_cwd = Path(body.oracle_cwd) if body.oracle_cwd else None
            target_provider_binding_id = body.target_provider_binding_id
        target_binding = None
        if target_provider_binding_id:
            target_binding = app_service.provider_binding(project_id, target_provider_binding_id)
            if target_binding.role != "sut_native":
                raise ValueError("Target execution requires a sut_native ProviderBinding.")
        if plan.evaluation_scope.target_provider_binding_id != target_provider_binding_id:
            raise ValueError("Target ProviderBinding does not match the frozen Evaluation Scope.")
        artifact = execute_evaluation_run(
            plan,
            intelligence,
            manifest_path=manifest_path,
            cache_root=cache_root,
            fixture_root=Path(body.fixture_root) if body.fixture_root else None,
            run_root=run_root,
            evaluation_id=run.evaluation_id,
            oracle_command=oracle_command,
            oracle_id=oracle_id,
            oracle_type=oracle_type,
            oracle_version=oracle_version,
            oracle_cwd=oracle_cwd,
            target_binding=target_binding,
        )
        artifact_payload = artifact.model_dump(mode="json")
        evidence = adapt_evaluation_run_evidence(
            plan,
            request,
            run_id=run.run_id,
            scope_id=run.scope_id,
            artifact=artifact_payload,
        )
        completed = run.model_copy(update={
            "status": "completed",
            "current_stage": "evidence",
            "readiness_ref": content_ref(artifact_payload["scenario_readiness"]),
            "matrix_artifact_ref": str(artifact_payload["artifact_manifest_hash"]),
            "evidence_bundle_ref": content_ref(evidence.model_dump(mode="json")),
            "artifact": artifact_payload,
            "evidence_refs": list(artifact.evidence_refs),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        completed = append_event(
            completed,
            stage="evidence",
            status="completed",
            detail="Target matrix completed and Evidence Bundle was sealed.",
        )
        return app_service.save_evaluation_run(completed)
    except (ValueError, TargetInfrastructureError, OSError) as error:
        failed = run.model_copy(update={
            "status": "failed",
            "current_stage": "failed",
            "failure_classification": _run_failure_classification(error),
            "error": str(error),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        failed = append_event(
            failed,
            stage="failed",
            status="failed",
            detail=str(error),
        )
        app_service.save_evaluation_run(failed)
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/runs/{run_id}")
def get_evaluation_run(project_id: str, run_id: str):
    run = service().evaluation_run(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    return run


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs")
def list_evaluation_runs(project_id: str, evaluation_request_id: str):
    if service().evaluation_request(project_id, evaluation_request_id) is None:
        raise HTTPException(status_code=404, detail="evaluation request not found")
    return service().evaluation_runs(project_id, evaluation_request_id)


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs/{run_id}")
def get_evaluation_request_run(project_id: str, evaluation_request_id: str, run_id: str):
    run = service().evaluation_run(project_id, run_id)
    if run is None or run.evaluation_request_id != evaluation_request_id:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    return run


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs/{run_id}/events")
def get_evaluation_run_events(project_id: str, evaluation_request_id: str, run_id: str):
    run = get_evaluation_request_run(project_id, evaluation_request_id, run_id)
    return run.events


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs/{run_id}/matrix")
def get_evaluation_run_matrix(project_id: str, evaluation_request_id: str, run_id: str):
    run = get_evaluation_request_run(project_id, evaluation_request_id, run_id)
    if run.artifact is None or run.status not in {"completed", "failed"}:
        raise HTTPException(status_code=404, detail="evaluation matrix not available")
    return run.artifact


@app.post("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs")
def run_evaluation_for_request(project_id: str, evaluation_request_id: str, body: EvaluationRunRequest):
    if body.evaluation_plan_id == evaluation_request_id:
        raise _unprocessable(ValueError("Run path must use an Evaluation Request ID, not a Plan ID."))
    app_service = service()
    request = app_service.evaluation_request(project_id, evaluation_request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="evaluation request not found")
    plan = app_service.evaluation_plan(project_id, body.evaluation_plan_id)
    if plan is None or plan.change_id != evaluation_request_id:
        raise _unprocessable(ValueError("Evaluation Plan does not belong to the requested Evaluation Request."))
    return run_evaluation(project_id, body)


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/status")
def get_evaluation_status(project_id: str, evaluation_request_id: str):
    runs = service().evaluation_runs(project_id, evaluation_request_id)
    if not runs:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    return runs[-1]


@app.post("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/run")
def run_evaluation_singular(project_id: str, evaluation_request_id: str, body: EvaluationRunRequest):
    return run_evaluation_for_request(project_id, evaluation_request_id, body)


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/report")
def get_latest_evaluation_report(project_id: str, evaluation_request_id: str):
    app_service = service()
    runs = app_service.evaluation_runs(project_id, evaluation_request_id)
    if not runs:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    for run in reversed(runs):
        record = app_service.evaluation_report_for_run(project_id, run.run_id)
        if record is not None:
            return record.report
    raise HTTPException(status_code=404, detail="evaluation report not found")


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/evidence")
def get_latest_evaluation_evidence(project_id: str, evaluation_request_id: str):
    app_service = service()
    runs = app_service.evaluation_runs(project_id, evaluation_request_id)
    if not runs:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    for run in reversed(runs):
        if run.artifact is not None:
            try:
                return _run_evidence(app_service, project_id, run)
            except ValueError as error:
                raise _unprocessable(error) from error
    raise HTTPException(status_code=404, detail="evaluation evidence not found")


@app.get("/api/v1/projects/{project_id}/evaluations/runs/{run_id}/evidence")
def get_evaluation_run_evidence(project_id: str, run_id: str):
    app_service = service()
    run = app_service.evaluation_run(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    try:
        return _run_evidence(app_service, project_id, run)
    except ValueError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs/{run_id}/evidence")
def get_evaluation_request_run_evidence(project_id: str, evaluation_request_id: str, run_id: str):
    run = get_evaluation_request_run(project_id, evaluation_request_id, run_id)
    try:
        return _run_evidence(service(), project_id, run)
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/projects/{project_id}/evaluations/runs/{run_id}/report")
def build_evaluation_run_report(project_id: str, run_id: str, body: EvaluationRunReportRequest):
    app_service = service()
    run = app_service.evaluation_run(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    if body.run_id != run_id:
        raise _unprocessable(ValueError("Report request run_id does not match the path."))
    plan = app_service.evaluation_plan(project_id, run.evaluation_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="evaluation plan not found")
    try:
        product_definition = ProductDefinition.model_validate(body.product_definition)
        binding = app_service.provider_binding(project_id, body.provider_binding_id)
        if binding.role != "control_plane":
            raise ValueError("Product Evaluation Report requires a control_plane ProviderBinding.")
        evidence = _run_evidence(app_service, project_id, run)
        report = build_product_evaluation_report(
            plan,
            product_definition,
            evidence,
            provider=build_control_plane_client(binding, _provider_api_key(binding)),
            binding=binding,
            forbidden_tokens={run.run_id},
        )
        report_payload = product_evaluation_report_api_payload(report)
        existing_report = app_service.evaluation_report(project_id, report.report_id)
        if existing_report is None:
            app_service.save_evaluation_report(EvaluationReportRecord(
                report_id=report.report_id,
                project_id=project_id,
                run_id=run.run_id,
                evaluation_plan_id=plan.plan_id,
                scope_id=run.scope_id,
                report=report_payload,
            ))
        elif (
            existing_report.run_id != run.run_id
            or existing_report.scope_id != run.scope_id
            or existing_report.report != report_payload
        ):
            raise ValueError("Persisted Evaluation Report does not match this Evaluation Run.")
        app_service.save_evaluation_run(run.model_copy(update={
            "current_stage": "completed",
            "report_ref": report.report_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }))
        return report_payload
    except (EvolutionIntakeError, ProviderRuntimeError, ValueError, ProductNotFoundError) as error:
        app_service.save_evaluation_run(run.model_copy(update={
            "status": "failed",
            "current_stage": "failed",
            "failure_classification": "report_failure",
            "error": str(error),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }))
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/runs/{run_id}/report")
def get_evaluation_run_report(project_id: str, run_id: str):
    app_service = service()
    run = app_service.evaluation_run(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    record = app_service.evaluation_report_for_run(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="evaluation report not found")
    if run.report_ref != record.report_id:
        raise _unprocessable(ValueError("Evaluation Run report reference does not match the persisted report."))
    return record.report


@app.get("/api/v1/projects/{project_id}/evaluations/{evaluation_request_id}/runs/{run_id}/report")
def get_evaluation_request_run_report(project_id: str, evaluation_request_id: str, run_id: str):
    get_evaluation_request_run(project_id, evaluation_request_id, run_id)
    return get_evaluation_run_report(project_id, run_id)


@app.get("/api/v1/projects/{project_id}/reports/{report_id}")
def get_evaluation_report(project_id: str, report_id: str):
    record = service().evaluation_report(project_id, report_id)
    if record is None:
        raise HTTPException(status_code=404, detail="evaluation report not found")
    return record.report


@app.get("/api/v1/projects/{project_id}/reports/{report_id}/view")
def get_evaluation_report_view(project_id: str, report_id: str):
    app_service = service()
    record = app_service.evaluation_report(project_id, report_id)
    if record is None:
        raise HTTPException(status_code=404, detail="evaluation report not found")
    report = GenericProductEvaluationReport.model_validate(record.report)
    intelligence = app_service.project_intelligence(project_id)
    context = project_context_from_intelligence(intelligence) if intelligence is not None else None
    view = normalize_product_evaluation_report(report, project_context=context, gate=evaluate_release_decision(report))
    return view.model_dump(mode="json")


@app.get("/api/v1/demo/reports/lighttable")
def get_lighttable_demo_report():
    report, context, gate = _demo_report_bundle()
    view = normalize_product_evaluation_report(report, project_context=context, gate=gate)
    return {
        "report": report.model_dump(mode="json"),
        "evidence": report.evidence.model_dump(mode="json"),
        "gate": gate.model_dump(mode="json"),
        "view": view.model_dump(mode="json"),
    }


@app.get("/api/v1/demo/reports/lighttable/export")
def export_lighttable_demo_report(format: Literal["json", "md", "html"] = "html"):
    report, context, gate = _demo_report_bundle()
    if format == "json":
        return Response(content=report.model_dump_json(indent=2), media_type="application/json")
    if format == "md":
        return Response(
            content=render_product_evaluation_markdown(report, default_product_report_template(), project_context=context, gate=gate),
            media_type="text/markdown; charset=utf-8",
        )
    return Response(
        content=render_product_evaluation_html(report, default_product_report_template(), project_context=context, gate=gate),
        media_type="text/html; charset=utf-8",
    )


@app.get("/api/v1/projects/{project_id}/reports/{report_id}/evidence")
def get_evaluation_report_evidence(project_id: str, report_id: str):
    app_service = service()
    record = app_service.evaluation_report(project_id, report_id)
    if record is None:
        raise HTTPException(status_code=404, detail="evaluation report not found")
    evidence = record.report.get("evidence")
    if not isinstance(evidence, dict):
        raise _unprocessable(ValueError("Persisted Evaluation Report does not contain an Evidence Bundle."))
    return evidence


@app.get("/api/v1/projects/{project_id}/reports")
def list_evaluation_reports(project_id: str):
    try:
        return [_report_metadata(item) for item in service().evaluation_reports(project_id)]
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error


@app.get("/api/v1/projects/{project_id}/reports/{report_id}/export")
def export_evaluation_report(
    project_id: str,
    report_id: str,
    format: Literal["json", "md", "html"] = "json",
):
    record = service().evaluation_report(project_id, report_id)
    if record is None:
        raise HTTPException(status_code=404, detail="evaluation report not found")
    report = GenericProductEvaluationReport.model_validate(record.report)
    intelligence = service().project_intelligence(project_id)
    context = project_context_from_intelligence(intelligence) if intelligence is not None else None
    gate = evaluate_release_decision(report)
    if format == "json":
        return Response(
            content=json.dumps(record.report, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    if format == "md":
        return Response(
            content=render_product_evaluation_markdown(report, default_product_report_template(), project_context=context, gate=gate),
            media_type="text/markdown; charset=utf-8",
        )
    return Response(
        content=render_product_evaluation_html(report, default_product_report_template(), project_context=context, gate=gate),
        media_type="text/html; charset=utf-8",
    )


@app.post("/api/v1/projects/{project_id}/reports")
def import_project_evaluation_report(project_id: str, body: ProjectReportImportRequest):
    try:
        report = GenericProductEvaluationReport.model_validate(body.report)
        if report.subject.product_id != project_id:
            raise ValueError("Product Evaluation Report project does not match the project path.")
        payload = product_evaluation_report_api_payload(report)
        service().save_evaluation_report(EvaluationReportRecord(
            report_id=report.report_id,
            project_id=project_id,
            source="import",
            evaluation_plan_id=report.evaluation_plan.plan_id if report.evaluation_plan else None,
            scope_id=report.evidence.scope_id,
            report=payload,
        ))
        return {
            "report": payload,
            "gate": evaluate_release_decision(report),
        }
    except ValueError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/projects/{project_id}/evaluations/runs/{run_id}/gate")
def get_evaluation_run_gate(project_id: str, run_id: str):
    app_service = service()
    run = app_service.evaluation_run(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    record = app_service.evaluation_report_for_run(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="evaluation report not found")
    try:
        return evaluate_release_decision(GenericProductEvaluationReport.model_validate(record.report))
    except ValueError as error:
        raise _unprocessable(error) from error


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
        readiness = check_evaluation_plan_readiness(
            plan,
            intelligence.runtime_profile.fixture_catalog,
            fixture_root=Path(body.fixture_root) if body.fixture_root else None,
        )
        runtime_checks: list[ScenarioReadinessCheck] = []
        runtime_blockers: list[str] = []
        if body.execution_config_id:
            config = service().evaluation_execution_configuration(project_id, body.execution_config_id)
            if config is None:
                runtime_blockers.append("Selected project runtime configuration no longer exists.")
                runtime_checks.append(ScenarioReadinessCheck(name="runtime_configuration", status="blocked", detail=runtime_blockers[-1]))
            elif config.snapshot_version != plan.evaluation_scope.candidate_version:
                runtime_blockers.append("Project runtime configuration belongs to a different source snapshot; review it again.")
                runtime_checks.append(ScenarioReadinessCheck(name="runtime_snapshot", status="blocked", detail=runtime_blockers[-1]))
            else:
                preflight = TargetEnvironmentCache(Path(config.cache_root)).preflight(Path(config.manifest_path))
                for item in preflight["checks"]:
                    status = "passed" if item["status"] == "passed" else "blocked"
                    runtime_checks.append(ScenarioReadinessCheck(name=f"runtime:{item['name']}", status=status, detail=str(item["detail"])))
                    if status == "blocked":
                        runtime_blockers.append(str(item["detail"]))
                binding = service().provider_binding(project_id, plan.evaluation_scope.provider_binding_id)
                credential_available = bool(os.getenv(binding.expected_environment_variable))
                runtime_checks.append(ScenarioReadinessCheck(
                    name="planner_credential", status="passed" if credential_available else "blocked",
                    detail="Planner credential is available to the server." if credential_available else "Planner credential is unavailable to the server.",
                ))
                if not credential_available:
                    runtime_blockers.append("Planner credential is unavailable to the server.")
        else:
            runtime_blockers.append("Project runtime setup is required before Run.")
            runtime_checks.append(ScenarioReadinessCheck(name="runtime_configuration", status="blocked", detail=runtime_blockers[-1]))
        if runtime_blockers:
            return readiness.model_copy(update={
                "status": "blocked",
                "runtime_checks": runtime_checks,
                "blocking_reasons": [*readiness.blocking_reasons, *runtime_blockers],
            })
        return readiness.model_copy(update={"runtime_checks": runtime_checks})
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
