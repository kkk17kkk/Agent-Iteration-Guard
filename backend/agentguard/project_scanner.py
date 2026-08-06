"""Read-only Project Intelligence scanners.

The scanner turns a repository, local package, or locally inspectable Docker
image into the same registration boundary used by Project Intelligence.  A
project-neutral ``aig.project.json`` or ``project-registration.json`` may
declare semantic components; otherwise only conventionally named ``skills``
and ``tools`` directories are discovered.  Missing semantic evidence is
reported as unresolved instead of being guessed from arbitrary source code.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .project_intelligence import (
    AgentManifest,
    CapabilityRecord,
    ProjectIntelligence,
    ProjectIntelligenceRegistration,
    ProjectSnapshotDiff,
    RuntimeKind,
    SourceKind,
)
from .store import Store


ScanStatus = Literal["ready", "unresolved", "failed"]


class ProjectScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.project-scan-request.v1"] = "aig.project-scan-request.v1"
    project_id: str = Field(min_length=1)
    source_kind: SourceKind
    source_ref: str = Field(min_length=1)
    version: str = Field(min_length=1)
    entrypoint: str | None = Field(default=None, min_length=1)
    runtime_kind: RuntimeKind | None = None
    declaration_file: str | None = Field(default=None, min_length=1)


class ScanFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    kind: Literal["metadata", "runtime", "component", "warning", "error"]
    detail: str = Field(min_length=1, max_length=500)


class ProjectScanRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.project-scan-record.v1"] = "aig.project-scan-record.v1"
    scan_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_kind: SourceKind
    source_ref: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    scanner_ref: str = Field(min_length=1)
    status: ScanStatus
    findings: list[ScanFinding] = Field(default_factory=list, max_length=1000)
    unresolved_reasons: list[str] = Field(default_factory=list, max_length=32)
    declaration_ref: str | None = None
    registration_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    registered_snapshot_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProjectScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scan: ProjectScanRecord
    registration: ProjectIntelligenceRegistration | None = None
    intelligence: ProjectIntelligence | None = None
    snapshot_diff: ProjectSnapshotDiff | None = None


class ProjectScanError(ValueError):
    """Raised only for malformed scanner input, not for unresolved evidence."""


class ProjectScannerRepository:
    _KIND = "project_scan_record"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, record: ProjectScanRecord) -> ProjectScanRecord:
        existing = self.store.get(self._KIND, record.scan_id, ProjectScanRecord)
        if existing is not None:
            identity = (existing.project_id, existing.version, existing.source_fingerprint)
            if identity != (record.project_id, record.version, record.source_fingerprint):
                raise ProjectScanError(f"Project scan {record.scan_id} already exists with different contents.")
            return existing
        self.store.save(self._KIND, record.scan_id, record.project_id, record)
        return record

    def get(self, project_id: str, scan_id: str) -> ProjectScanRecord | None:
        record = self.store.get(self._KIND, scan_id, ProjectScanRecord)
        return record if record is not None and record.project_id == project_id else None

    def list(self, project_id: str) -> list[ProjectScanRecord]:
        return sorted(
            self.store.list(self._KIND, ProjectScanRecord, project_id),
            key=lambda item: item.created_at,
        )


class ProjectScanner:
    """Scan local source metadata without importing or executing target code."""

    scanner_ref = "agentguard.project-scanner:aig.project-scan.v1"
    declaration_candidates = ("aig.project.json", "project-registration.json")
    ignored_directories = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}

    def scan(self, request: ProjectScanRequest) -> ProjectScanResult:
        if request.source_kind == "docker_image":
            return self._scan_docker(request)
        source = Path(request.source_ref).expanduser().resolve()
        if source.is_dir():
            return self._scan_directory(request, source)
        if request.source_kind == "package" and source.is_file():
            return self._scan_package_archive(request, source)
        return self._failed(request, f"source path does not exist or is not a directory: {source}")

    def _scan_directory(
        self,
        request: ProjectScanRequest,
        source: Path,
        *,
        source_fingerprint_override: str | None = None,
        source_identity_override: str | None = None,
    ) -> ProjectScanResult:
        source_fingerprint = source_fingerprint_override or _tree_fingerprint(source, self.ignored_directories)
        identity = source_identity_override or _repository_identity(source, source_fingerprint)
        source_ref = f"{request.source_kind}:{identity}"
        findings = [ScanFinding(path=".", kind="metadata", detail=f"scanned {source_fingerprint[:16]} source fingerprint")]
        declaration_path, payload = self._load_declaration(source, request.declaration_file)
        if payload is None:
            registration = self._discover_registration(request, source, source_ref, source_fingerprint, findings)
            if registration is None:
                return self._result(
                    request,
                    source_fingerprint=source_fingerprint,
                    source_ref=source_ref,
                    status="unresolved",
                    findings=findings,
                    unresolved_reasons=[
                        "No project-neutral declaration was found and no conventional skills/tools directory yielded a component."
                    ],
                )
        else:
            try:
                registration = self._registration_from_payload(
                    request, payload, source, source_ref, source_fingerprint, findings
                )
            except (TypeError, ValueError, KeyError) as error:
                return self._result(
                    request,
                    source_fingerprint=source_fingerprint,
                    source_ref=source_ref,
                    status="failed",
                    findings=[*findings, ScanFinding(path=declaration_path or "<declaration>", kind="error", detail=str(error))],
                    unresolved_reasons=["Project declaration could not be validated."],
                )
        assert registration is not None
        findings.append(ScanFinding(
            path=declaration_path or "conventional source layout",
            kind="component",
            detail=f"registered {len(registration.capabilities)} semantic components",
        ))
        return self._result(
            request,
            source_fingerprint=source_fingerprint,
            source_ref=source_ref,
            status="ready",
            findings=findings,
            declaration_ref=declaration_path,
            registration=registration,
        )

    def _scan_package_archive(self, request: ProjectScanRequest, archive: Path) -> ProjectScanResult:
        archive_fingerprint = hashlib.sha256(archive.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="aig-package-scan-") as temporary:
            root = Path(temporary)
            try:
                _safe_extract_archive(archive, root)
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                return self._result(
                    request,
                    source_fingerprint=archive_fingerprint,
                    source_ref=f"package:{archive.name}@{archive_fingerprint}",
                    status="failed",
                    findings=[ScanFinding(path=archive.name, kind="error", detail=str(error))],
                    unresolved_reasons=["Package archive could not be safely inspected."],
                )
            roots = [root]
            nested = [item for item in root.iterdir() if item.is_dir()]
            if len(nested) == 1:
                roots.insert(0, nested[0])
            for candidate in roots:
                result = self._scan_directory(
                    request,
                    candidate,
                    source_fingerprint_override=archive_fingerprint,
                    source_identity_override=f"{archive.name}@{archive_fingerprint}",
                )
                if result.registration is not None or result.scan.status == "failed":
                    return result
        return self._result(
            request,
            source_fingerprint=archive_fingerprint,
            source_ref=f"package:{archive.name}@{archive_fingerprint}",
            status="unresolved",
            findings=[ScanFinding(path=archive.name, kind="warning", detail="package contains no semantic declaration")],
            unresolved_reasons=["Package archive contains no project-neutral declaration or discoverable component layout."],
        )

    def _scan_docker(self, request: ProjectScanRequest) -> ProjectScanResult:
        source = Path(request.source_ref).expanduser().resolve()
        if source.is_dir():
            dockerfile = source / "Dockerfile"
            if not dockerfile.is_file():
                return self._failed(request, f"Docker source directory has no Dockerfile: {source}")
            base = self._scan_directory(request.model_copy(update={"runtime_kind": "docker"}), source)
            if base.registration is None:
                return base
            docker_text = dockerfile.read_text(encoding="utf-8", errors="replace")
            runtime = base.registration.runtime_profile.model_copy(update={
                "runtime_kind": "docker",
                "entrypoint": request.entrypoint or _dockerfile_entrypoint(docker_text),
                "image_digest": None,
                "source_kind": "docker_image",
            })
            registration = base.registration.model_copy(update={"runtime_profile": runtime})
            return base.model_copy(update={"registration": registration})
        metadata = _docker_image_metadata(request.source_ref)
        if metadata is None:
            return self._result(
                request,
                source_fingerprint=_fingerprint(request.source_ref),
                source_ref=f"docker:{request.source_ref}",
                status="unresolved",
                findings=[ScanFinding(path=request.source_ref, kind="warning", detail="local Docker image metadata is unavailable")],
                unresolved_reasons=["Docker image must be locally inspectable and carry a project-neutral semantic declaration."],
            )
        labels = ((metadata.get("Config") or {}).get("Labels") or {})
        declaration = labels.get("aig.project.manifest") if isinstance(labels, dict) else None
        if not declaration:
            return self._result(
                request,
                source_fingerprint=_fingerprint(metadata),
                source_ref=f"docker:{request.source_ref}",
                status="unresolved",
                findings=[ScanFinding(path=request.source_ref, kind="warning", detail="Docker image has no aig.project.manifest label")],
                unresolved_reasons=["Docker image does not expose semantic component metadata."],
            )
        try:
            payload = json.loads(declaration)
        except json.JSONDecodeError as error:
            return self._result(
                request,
                source_fingerprint=_fingerprint(metadata),
                source_ref=f"docker:{request.source_ref}",
                status="failed",
                findings=[ScanFinding(path="aig.project.manifest", kind="error", detail=str(error))],
                unresolved_reasons=["Docker semantic declaration is not valid JSON."],
            )
        with tempfile.TemporaryDirectory(prefix="aig-docker-scan-") as temporary:
            root = Path(temporary)
            declaration_path = root / "aig.project.json"
            declaration_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = self._scan_directory(
                request.model_copy(update={"runtime_kind": "docker", "declaration_file": "aig.project.json"}),
                root,
                source_fingerprint_override=_fingerprint(metadata),
                source_identity_override=request.source_ref,
            )
        digest = _docker_metadata_digest(metadata)
        if result.registration is None:
            return result
        runtime = result.registration.runtime_profile.model_copy(update={
            "runtime_kind": "docker",
            "source_kind": "docker_image",
            "image_digest": digest,
            "entrypoint": request.entrypoint or result.registration.runtime_profile.entrypoint,
        })
        return result.model_copy(update={"registration": result.registration.model_copy(update={"runtime_profile": runtime})})

    def _load_declaration(self, source: Path, requested: str | None) -> tuple[str | None, dict[str, object] | None]:
        candidates = [requested] if requested else list(self.declaration_candidates)
        for relative in candidates:
            if not relative:
                continue
            path = (source / relative).resolve()
            try:
                path.relative_to(source.resolve())
            except ValueError as error:
                raise ProjectScanError("declaration_file must stay inside the source root") from error
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ProjectScanError("project declaration must be a JSON object")
            return relative.replace("\\", "/"), payload
        return None, None

    def _registration_from_payload(
        self,
        request: ProjectScanRequest,
        payload: dict[str, object],
        source: Path,
        source_ref: str,
        source_fingerprint: str,
        findings: list[ScanFinding],
    ) -> ProjectIntelligenceRegistration:
        registration_payload = payload.get("registration", payload)
        if not isinstance(registration_payload, dict):
            raise ProjectScanError("registration declaration must be a JSON object")
        if "agent_manifest" not in registration_payload or "capabilities" not in registration_payload or "runtime_profile" not in registration_payload:
            raise ProjectScanError("project declaration must include agent_manifest, capabilities, and runtime_profile")
        data = dict(registration_payload)
        data["project_id"] = request.project_id
        data["baseline_version"] = request.version
        data.pop("snapshot_version", None)
        manifest = AgentManifest.model_validate({
            **dict(data["agent_manifest"]),
            "project_id": request.project_id,
            "source_kind": request.source_kind,
            "source_ref": source_ref,
        })
        capabilities = []
        for item in data["capabilities"]:
            capabilities.append(CapabilityRecord.model_validate({
                **dict(item),
                "project_id": request.project_id,
                "source_refs": list(dict.fromkeys([*dict(item).get("source_refs", []), "scan:" + source_fingerprint])),
            }))
        runtime_data = dict(data["runtime_profile"])
        runtime_kind = request.runtime_kind or runtime_data.get("runtime_kind") or _infer_runtime_kind(source)
        entrypoint = request.entrypoint or runtime_data.get("entrypoint") or _infer_entrypoint(source, runtime_kind)
        if not entrypoint:
            raise ProjectScanError("runtime entrypoint could not be discovered; provide --entrypoint")
        dependencies = list(runtime_data.get("dependencies") or _dependency_files(source))
        if not dependencies:
            dependencies = ["source metadata only"]
        execution_requirements = list(runtime_data.get("execution_requirements") or [
            "source fingerprint is pinned by the scanner",
            "runtime entrypoint is declared before evaluation",
        ])
        runtime = runtime_data | {
            "project_id": request.project_id,
            "runtime_kind": runtime_kind,
            "entrypoint": entrypoint,
            "dependencies": dependencies,
            "execution_requirements": execution_requirements,
            "source_ref": source_ref,
            "source_kind": request.source_kind,
            "source_fingerprint": source_fingerprint,
            "dependency_lock_fingerprint": _dependency_fingerprint(source),
            "scanner_ref": self.scanner_ref,
            "preflight_contract_ref": f"preflight:{source_fingerprint}",
        }
        findings.append(ScanFinding(path="runtime metadata", kind="runtime", detail=f"runtime kind={runtime_kind}, entrypoint={entrypoint}"))
        return ProjectIntelligenceRegistration(
            project_id=request.project_id,
            agent_manifest=manifest,
            capabilities=capabilities,
            runtime_profile=runtime,
            baseline_version=request.version,
            initial_evaluation_history=list(data.get("initial_evaluation_history") or []),
        )

    def _discover_registration(
        self,
        request: ProjectScanRequest,
        source: Path,
        source_ref: str,
        source_fingerprint: str,
        findings: list[ScanFinding],
    ) -> ProjectIntelligenceRegistration | None:
        component_roots = [("skill", "skills"), ("skill", "capabilities"), ("tool", "tools")]
        capabilities: list[CapabilityRecord] = []
        for component_type, directory_name in component_roots:
            directory = source / directory_name
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".py", ".js", ".ts", ".mjs", ".cjs"}:
                    continue
                if path.stem == "__init__":
                    continue
                name = path.stem
                capabilities.append(CapabilityRecord(
                    project_id=request.project_id,
                    component_type=component_type,
                    name=name,
                    responsibility=f"Static {component_type} implementation discovered at {path.relative_to(source).as_posix()}.",
                    status="declared",
                    source_refs=[f"scan:{source_fingerprint}", path.relative_to(source).as_posix()],
                ))
        if not capabilities:
            return None
        names = [item.name for item in capabilities]
        purpose = _read_purpose(source) or "Complete user tasks through declared Agent capabilities."
        manifest = AgentManifest(
            project_id=request.project_id,
            agent_name=source.name,
            purpose=purpose,
            source_kind=request.source_kind,
            source_ref=source_ref,
            available_components=names,
            capability_descriptions={item.name: item.responsibility for item in capabilities},
        )
        runtime_kind = request.runtime_kind or _infer_runtime_kind(source)
        entrypoint = request.entrypoint or _infer_entrypoint(source, runtime_kind)
        if not entrypoint:
            findings.append(ScanFinding(path="runtime", kind="warning", detail="components found but runtime entrypoint is unresolved"))
            return None
        runtime = {
            "project_id": request.project_id,
            "entrypoint": entrypoint,
            "runtime_kind": runtime_kind,
            "dependencies": _dependency_files(source) or ["source metadata only"],
            "execution_requirements": ["source fingerprint is pinned by the scanner", "runtime entrypoint is declared before evaluation"],
            "source_ref": source_ref,
            "source_kind": request.source_kind,
            "source_fingerprint": source_fingerprint,
            "dependency_lock_fingerprint": _dependency_fingerprint(source),
            "scanner_ref": self.scanner_ref,
            "preflight_contract_ref": f"preflight:{source_fingerprint}",
        }
        return ProjectIntelligenceRegistration(
            project_id=request.project_id,
            agent_manifest=manifest,
            capabilities=capabilities,
            runtime_profile=runtime,
            baseline_version=request.version,
        )

    def _result(
        self,
        request: ProjectScanRequest,
        *,
        source_fingerprint: str,
        source_ref: str,
        status: ScanStatus,
        findings: list[ScanFinding],
        unresolved_reasons: list[str] | None = None,
        declaration_ref: str | None = None,
        registration: ProjectIntelligenceRegistration | None = None,
    ) -> ProjectScanResult:
        scan_id = "scan_" + _fingerprint({
            "project_id": request.project_id,
            "version": request.version,
            "source_kind": request.source_kind,
            "source_fingerprint": source_fingerprint,
        })[:16]
        record = ProjectScanRecord(
            scan_id=scan_id,
            project_id=request.project_id,
            version=request.version,
            source_kind=request.source_kind,
            source_ref=source_ref,
            source_fingerprint=source_fingerprint,
            scanner_ref=self.scanner_ref,
            status=status,
            findings=findings,
            unresolved_reasons=unresolved_reasons or [],
            declaration_ref=declaration_ref,
            registration_fingerprint=_fingerprint(registration.model_dump(mode="json")) if registration else None,
        )
        return ProjectScanResult(scan=record, registration=registration)

    def _failed(self, request: ProjectScanRequest, detail: str) -> ProjectScanResult:
        return self._result(
            request,
            source_fingerprint=_fingerprint(request.source_ref),
            source_ref=f"{request.source_kind}:{request.source_ref}",
            status="failed",
            findings=[ScanFinding(path=request.source_ref, kind="error", detail=detail)],
            unresolved_reasons=[detail],
        )


def _tree_fingerprint(root: Path, ignored: set[str]) -> str:
    entries: list[dict[str, str | int]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(".env") or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pyc"}:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        entries.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return _fingerprint(entries)


def _repository_identity(root: Path, source_fingerprint: str) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    except OSError:
        result = None
    revision = result.stdout.strip() if result and result.returncode == 0 else ""
    return f"git:{revision}" if revision else f"tree:{source_fingerprint}"


def _dependency_files(root: Path) -> list[str]:
    known = {
        "requirements.txt", "requirements.lock", "pyproject.toml", "poetry.lock", "uv.lock",
        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    }
    return [path.relative_to(root).as_posix() for path in sorted(root.rglob("*")) if path.is_file() and path.name in known]


def _dependency_fingerprint(root: Path) -> str | None:
    files = _dependency_files(root)
    if not files:
        return None
    return _fingerprint({path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in files})


def _infer_runtime_kind(root: Path) -> RuntimeKind:
    if (root / "Dockerfile").is_file():
        return "docker"
    return "package" if (root / "pyproject.toml").is_file() or (root / "package.json").is_file() else "native_command"


def _infer_entrypoint(root: Path, runtime_kind: RuntimeKind) -> str | None:
    if runtime_kind == "docker" and (root / "Dockerfile").is_file():
        return _dockerfile_entrypoint((root / "Dockerfile").read_text(encoding="utf-8", errors="replace"))
    package_json = root / "package.json"
    if package_json.is_file():
        payload = json.loads(package_json.read_text(encoding="utf-8"))
        start = (payload.get("scripts") or {}).get("start") if isinstance(payload, dict) else None
        return str(start) if start else None
    for candidate in ("main.py", "app.py", "backend/main.py", "src/main.py", "index.js", "src/index.js"):
        if (root / candidate).is_file():
            return f"python {candidate}" if candidate.endswith(".py") else f"node {candidate}"
    return None


def _read_purpose(root: Path) -> str | None:
    readme = next((root / name for name in ("README.md", "README.rst", "README.txt") if (root / name).is_file()), None)
    if not readme:
        return None
    for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:300]
    return None


def _safe_extract_archive(archive: Path, root: Path) -> None:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                target = (root / member.filename).resolve()
                target.relative_to(root.resolve())
            handle.extractall(root)
        return
    import tarfile

    if not tarfile.is_tarfile(archive):
        raise ValueError("unsupported package archive; expected zip or tar")
    with tarfile.open(archive) as handle:
        for member in handle.getmembers():
            target = (root / member.name).resolve()
            target.relative_to(root.resolve())
        handle.extractall(root)


def _dockerfile_entrypoint(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.upper().startswith("ENTRYPOINT ") or stripped.upper().startswith("CMD "):
            return stripped.split(None, 1)[1].strip()
    return "docker image entrypoint"


def _dockerfile_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _docker_image_metadata(image: str) -> dict[str, object] | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    result = subprocess.run([docker, "image", "inspect", image], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None


def _docker_metadata_digest(metadata: dict[str, object]) -> str | None:
    repo_digests = metadata.get("RepoDigests")
    if isinstance(repo_digests, list):
        for item in repo_digests:
            text = str(item)
            if "@sha256:" in text:
                return text.split("@", 1)[1]
    return None


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ProjectScanError",
    "ProjectScanRecord",
    "ProjectScanRequest",
    "ProjectScanResult",
    "ProjectScanner",
    "ProjectScannerRepository",
    "ScanFinding",
    "ScanStatus",
]
