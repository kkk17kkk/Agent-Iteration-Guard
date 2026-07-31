"""Read-only intake and version-memory primitives for real Agent revisions.

This module intentionally does not clone, install, start, or credential-inject a
target repository.  It turns a user-provided local Git checkout into auditable
intake records and leaves execution behind explicit contracts and approval.
"""

import hashlib
import subprocess
from pathlib import Path

from .domain import (
    AgentEvolutionCase,
    AgentRevision,
    AgentSource,
    EvolutionChangeSet,
    EvolutionComparison,
    EvolutionFileChange,
    EvolutionPairPlan,
    EvolutionReviewWorkItem,
    EvaluationAdmission,
    EvaluationPipeline,
    EnvironmentCheck,
    HistoricalReplayEvidence,
    IntakeReviewClaim,
    IntakeReviewReport,
    MemoryDependency,
    MemoryEntry,
    ProductContractRevision,
    ReproductionContract,
    NativeHarnessContract,
    RuntimeEnvironmentContract,
    RuntimeEnvironmentPreflight,
    RuntimeParityAssessment,
    StalePropagation,
    TaskVerifierContract,
)
from .store import Store


class EvolutionIntakeError(ValueError):
    pass


class EvolutionIntakeResult:
    def __init__(
        self,
        source: AgentSource,
        baseline: AgentRevision,
        candidate: AgentRevision,
        changeset: EvolutionChangeSet,
        contracts: list[ReproductionContract],
        parity: list[RuntimeParityAssessment],
        case: AgentEvolutionCase,
        plan: EvolutionPairPlan,
        comparison: EvolutionComparison,
        report: IntakeReviewReport,
    ) -> None:
        self.source = source
        self.baseline = baseline
        self.candidate = candidate
        self.changeset = changeset
        self.contracts = contracts
        self.parity = parity
        self.case = case
        self.plan = plan
        self.comparison = comparison
        self.report = report

    def as_dict(self) -> dict[str, object]:
        return {
            "agent_source": self.source.model_dump(),
            "baseline_revision": self.baseline.model_dump(),
            "candidate_revision": self.candidate.model_dump(),
            "evolution_changeset": self.changeset.model_dump(),
            "reproduction_contracts": [item.model_dump() for item in self.contracts],
            "runtime_parity": [item.model_dump() for item in self.parity],
            "evolution_case": self.case.model_dump(),
            "evolution_pair_plan": self.plan.model_dump(),
            "evolution_comparison": self.comparison.model_dump(),
            "intake_review_report": self.report.model_dump(),
        }


class EvaluationAdmissionResult:
    def __init__(self, admission: EvaluationAdmission, pipeline: EvaluationPipeline | None) -> None:
        self.admission = admission
        self.pipeline = pipeline

    def as_dict(self) -> dict[str, object]:
        return {
            "evaluation_admission": self.admission.model_dump(),
            "evaluation_pipeline": self.pipeline.model_dump() if self.pipeline else None,
        }


class LocalGitSourceInspector:
    """Only invokes local Git read operations against an existing checkout."""

    LOCK_NAMES = {
        "uv.lock", "poetry.lock", "pdm.lock", "pdm.lock", "pdm.lock",
        "pipfile.lock", "requirements.lock", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
        "pnpm-lock.yaml", "cargo.lock", "go.sum", "composer.lock",
    }
    LICENSE_NAMES = {"license", "license.md", "license.txt", "copying"}

    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path.resolve()

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.source_path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise EvolutionIntakeError(f"Git read-only intake failed: {detail}")
        return completed.stdout.strip()

    def assert_repository(self) -> None:
        if not self.source_path.is_dir():
            raise EvolutionIntakeError(f"Source checkout does not exist: {self.source_path}")
        if self._git("rev-parse", "--is-inside-work-tree") != "true":
            raise EvolutionIntakeError(f"Source path is not a Git worktree: {self.source_path}")

    def resolve_commit(self, revision: str) -> str:
        return self._git("rev-parse", f"{revision}^{{commit}}")

    def tree_sha(self, commit: str) -> str:
        return self._git("rev-parse", f"{commit}^{{tree}}")

    def origin_url(self) -> str | None:
        completed = subprocess.run(
            ["git", "-C", str(self.source_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 and completed.stdout.strip() else None

    def tree_files(self, commit: str) -> list[str]:
        return [item for item in self._git("ls-tree", "-r", "--name-only", commit).splitlines() if item]

    def manifest_sha(self, commit: str) -> str:
        listing = self._git("ls-tree", "-r", commit)
        return hashlib.sha256(listing.encode("utf-8")).hexdigest()

    def lock_files(self, commit: str) -> list[str]:
        return [
            path for path in self.tree_files(commit)
            if Path(path).name.lower() in self.LOCK_NAMES
        ]

    def license_ref(self, commit: str) -> str | None:
        for path in self.tree_files(commit):
            if Path(path).name.lower() in self.LICENSE_NAMES:
                return f"git:{commit}:{path}"
        return None

    def changes(self, baseline: str, candidate: str) -> tuple[list[EvolutionFileChange], str]:
        raw_diff = self._git("diff", "--no-ext-diff", "--name-status", "-M", baseline, candidate)
        changes: list[EvolutionFileChange] = []
        status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
        for line in raw_diff.splitlines():
            parts = line.split("\t")
            if not parts:
                continue
            marker = parts[0][:1]
            status = status_map.get(marker, "unknown")
            path = parts[-1] if len(parts) > 1 else line
            changes.append(EvolutionFileChange(
                path=path,
                status=status,
                evidence_ref=f"git:{baseline}..{candidate}:{line}",
            ))
        return changes, hashlib.sha256(raw_diff.encode("utf-8")).hexdigest()


class EvolutionService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def intake(
        self,
        *,
        project_id: str,
        source_path: Path,
        baseline_ref: str,
        candidate_ref: str,
        repository_url: str | None = None,
        declared_entrypoint: str | None = None,
    ) -> EvolutionIntakeResult:
        inspector = LocalGitSourceInspector(source_path)
        inspector.assert_repository()
        baseline_commit = inspector.resolve_commit(baseline_ref)
        candidate_commit = inspector.resolve_commit(candidate_ref)
        source_url = repository_url or inspector.origin_url() or f"local-git:{inspector.source_path}"
        source = self._source(project_id, source_url, inspector.license_ref(candidate_commit))
        baseline = self._revision(project_id, source, inspector, baseline_commit, declared_entrypoint)
        candidate = self._revision(project_id, source, inspector, candidate_commit, declared_entrypoint)
        changes, diff_sha = inspector.changes(baseline_commit, candidate_commit)
        changeset = EvolutionChangeSet(
            project_id=project_id,
            baseline_revision_id=baseline.revision_id,
            candidate_revision_id=candidate.revision_id,
            diff_sha256=diff_sha,
            changes=changes,
        )
        contracts = [self._contract(project_id, revision) for revision in (baseline, candidate)]
        parity = [self._parity(project_id, revision, contract) for revision, contract in zip((baseline, candidate), contracts)]
        case = AgentEvolutionCase(
            project_id=project_id,
            source_id=source.source_id,
            baseline_revision_id=baseline.revision_id,
            candidate_revision_id=candidate.revision_id,
            evolution_changeset_id=changeset.evolution_changeset_id,
            status="awaiting_approval",
        )
        blocking_reasons = [gap for contract in contracts for gap in contract.gaps]
        blocking_reasons.extend(item.reason for item in parity if item.status == "environment_parity_blocked")
        plan = EvolutionPairPlan(
            project_id=project_id,
            evolution_case_id=case.evolution_case_id,
            status="awaiting_approval",
            required_contract_ids=[item.reproduction_contract_id for item in contracts],
            blocking_reasons=list(dict.fromkeys(blocking_reasons)),
        )
        comparison = EvolutionComparison(
            project_id=project_id,
            evolution_case_id=case.evolution_case_id,
            status="awaiting_evidence",
            conclusion="Static intake is complete; no target Agent process or Provider has been invoked.",
        )
        report = self._report(project_id, case, baseline, candidate, changeset, contracts, parity)
        records = [
            ("agent_source", source.source_id, project_id, source),
            ("agent_revision", baseline.revision_id, project_id, baseline),
            ("agent_revision", candidate.revision_id, project_id, candidate),
            ("evolution_changeset", changeset.evolution_changeset_id, project_id, changeset),
            *[("reproduction_contract", item.reproduction_contract_id, project_id, item) for item in contracts],
            *[("runtime_parity", item.parity_assessment_id, project_id, item) for item in parity],
            ("agent_evolution_case", case.evolution_case_id, project_id, case),
            ("evolution_pair_plan", plan.evolution_pair_plan_id, project_id, plan),
            ("evolution_comparison", comparison.evolution_comparison_id, project_id, comparison),
            ("intake_review_report", report.intake_review_report_id, project_id, report),
        ]
        self.store.save_many(records)
        return EvolutionIntakeResult(source, baseline, candidate, changeset, contracts, parity, case, plan, comparison, report)

    def _source(self, project_id: str, repository_url: str, license_ref: str | None) -> AgentSource:
        existing = next(
            (item for item in self.store.list("agent_source", AgentSource, project_id) if item.repository_url == repository_url),
            None,
        )
        if existing:
            return existing
        return AgentSource(project_id=project_id, repository_url=repository_url, license_ref=license_ref)

    def _revision(self, project_id: str, source: AgentSource, inspector: LocalGitSourceInspector, commit: str, entrypoint: str | None) -> AgentRevision:
        existing = next(
            (
                item for item in self.store.list("agent_revision", AgentRevision, project_id)
                if item.source_id == source.source_id and item.commit_sha == commit
            ),
            None,
        )
        if existing:
            return existing
        return AgentRevision(
            project_id=project_id,
            source_id=source.source_id,
            commit_sha=commit,
            tree_sha=inspector.tree_sha(commit),
            manifest_sha256=inspector.manifest_sha(commit),
            declared_entrypoint=entrypoint,
            lock_files=inspector.lock_files(commit),
        )

    @staticmethod
    def _contract(project_id: str, revision: AgentRevision) -> ReproductionContract:
        gaps = [
            gap for gap, present in (
                ("declared Agent entrypoint is required", revision.declared_entrypoint),
                ("reproduction command is required", False),
                ("environment reset strategy is required", False),
                ("independent task/verifier contract is required", False),
            ) if not present
        ]
        if not revision.lock_files:
            gaps.append("no dependency lock file observed in this revision")
        return ReproductionContract(
            project_id=project_id,
            revision_id=revision.revision_id,
            entrypoint=revision.declared_entrypoint,
            status="incomplete",
            gaps=gaps,
        )

    @staticmethod
    def _parity(project_id: str, revision: AgentRevision, contract: ReproductionContract) -> RuntimeParityAssessment:
        if not revision.lock_files:
            return RuntimeParityAssessment(
                project_id=project_id,
                revision_id=revision.revision_id,
                status="environment_parity_blocked",
                reason="No dependency lock file was observed; environment parity cannot be established from static intake.",
                evidence_refs=[f"git:{revision.commit_sha}:tree"],
            )
        if not revision.declared_entrypoint:
            return RuntimeParityAssessment(
                project_id=project_id,
                revision_id=revision.revision_id,
                status="unassessed",
                reason="A dependency lock file is present, but the real Agent entrypoint has not been declared.",
                evidence_refs=[f"git:{revision.commit_sha}:tree"],
            )
        return RuntimeParityAssessment(
            project_id=project_id,
            revision_id=revision.revision_id,
            status="preflight_ready",
            reason="Static dependencies and a declared entrypoint are available; runtime, reset, and verifier parity still require approval.",
            evidence_refs=[f"git:{revision.commit_sha}:tree"],
        )

    def _report(
        self,
        project_id: str,
        case: AgentEvolutionCase,
        baseline: AgentRevision,
        candidate: AgentRevision,
        changeset: EvolutionChangeSet,
        contracts: list[ReproductionContract],
        parity: list[RuntimeParityAssessment],
    ) -> IntakeReviewReport:
        commits = f"git:{baseline.commit_sha};git:{candidate.commit_sha}"
        parity_blocked = any(item.status == "environment_parity_blocked" for item in parity)
        claims = [
            IntakeReviewClaim(
                topic="source_revision",
                status="eligible_with_gaps",
                statement="The supplied local checkout resolves both requested Git commits.",
                evidence_level="verified",
                evidence_refs=[commits],
                scope="local Git object identity only; upstream ownership and repository history were not independently authenticated",
                unknowns=["upstream provenance remains a user-review responsibility"],
                next_evidence_action="Review repository URL, license, and intended revision pair before approving execution.",
            ),
            IntakeReviewClaim(
                topic="change_classification",
                status="not_observed",
                statement="Changed files were extracted deterministically, but semantic capability and risk classification remains review_required.",
                evidence_level="supported",
                evidence_refs=[f"artifact:{changeset.diff_sha256}", commits],
                scope="file-level Git diff only",
                unknowns=["no manual ChangeSet review has been recorded"],
                next_evidence_action="Review every changed file against product contract, tools, prompts, skills, and recovery behavior.",
            ),
            IntakeReviewClaim(
                topic="runtime_parity",
                status="deferred" if parity_blocked else "eligible_with_gaps",
                statement="No target Agent process, dependency installation, or Provider call was performed during static intake.",
                evidence_level="unresolved",
                evidence_refs=[commits],
                scope="static source preflight",
                unknowns=[item.reason for item in parity],
                next_evidence_action="Approve complete reproduction, environment, task/verifier, and ProviderBinding contracts before a controlled trial.",
            ),
        ]
        issues = audit_intake_report_claims(claims, {baseline.commit_sha, candidate.commit_sha})
        return IntakeReviewReport(
            project_id=project_id,
            evolution_case_id=case.evolution_case_id,
            claims=claims,
            quality_status="PASS" if not issues else "BLOCKED",
            quality_issues=issues,
        )

    def record_product_contract(self, contract: ProductContractRevision) -> ProductContractRevision:
        self.store.save("product_contract_revision", contract.product_contract_revision_id, contract.project_id, contract)
        return contract

    def record_native_harness_contract(self, contract: NativeHarnessContract) -> NativeHarnessContract:
        self._case(contract.project_id, contract.evolution_case_id)
        if contract.status == "approved" and contract.behavior_mode != "production_parity":
            raise EvolutionIntakeError("A reconstruction harness cannot satisfy Level 2 full-runtime admission.")
        self.store.save("native_harness_contract", contract.native_harness_contract_id, contract.project_id, contract)
        return contract

    def record_runtime_environment_contract(self, contract: RuntimeEnvironmentContract) -> RuntimeEnvironmentContract:
        self._case(contract.project_id, contract.evolution_case_id)
        required = {
            "docker_ref": contract.docker_ref,
            "dependency_lock_ref": contract.dependency_lock_ref,
            "model_config_ref": contract.model_config_ref,
            "tools_manifest_ref": contract.tools_manifest_ref,
            "reset_command_ref": contract.reset_command_ref,
            "initial_state_ref": contract.initial_state_ref,
        }
        missing = [name for name, value in required.items() if not value]
        if contract.status == "approved" and missing:
            raise EvolutionIntakeError(f"An approved Level 2 environment contract is missing: {', '.join(missing)}")
        self.store.save("runtime_environment_contract", contract.runtime_environment_contract_id, contract.project_id, contract)
        return contract

    def record_task_verifier_contract(self, contract: TaskVerifierContract) -> TaskVerifierContract:
        self._case(contract.project_id, contract.evolution_case_id)
        self.store.save("task_verifier_contract", contract.task_verifier_contract_id, contract.project_id, contract)
        return contract

    def record_runtime_preflight(self, preflight: RuntimeEnvironmentPreflight) -> RuntimeEnvironmentPreflight:
        contract = self.store.get("runtime_environment_contract", preflight.environment_contract_id, RuntimeEnvironmentContract)
        if not contract or contract.project_id != preflight.project_id or contract.evolution_case_id != preflight.evolution_case_id:
            raise EvolutionIntakeError("Runtime preflight must reference the same project's environment contract.")
        required = {"docker", "dependency", "model_config", "tools", "reset", "initial_state", "verifier"}
        observed = {item.name for item in preflight.checks}
        duplicate = len(observed) != len(preflight.checks)
        if observed != required or duplicate:
            raise EvolutionIntakeError("Runtime preflight must contain one result for docker, dependency, model_config, tools, reset, initial_state, and verifier.")
        failed = [item for item in preflight.checks if item.status in {"missing", "failed"}]
        unfinished = [item for item in preflight.checks if item.status == "not_run"]
        if failed:
            preflight = preflight.model_copy(update={"status": "environment_not_satisfied", "environment_fingerprint": None})
        elif unfinished:
            preflight = preflight.model_copy(update={"status": "not_run", "environment_fingerprint": None})
        else:
            fingerprint_source = "\n".join(
                [contract.runtime_environment_contract_id, *sorted(item.evidence_ref or "" for item in preflight.checks)]
            )
            preflight = preflight.model_copy(update={
                "status": "passed",
                "environment_fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
            })
        self.store.save("runtime_environment_preflight", preflight.runtime_environment_preflight_id, preflight.project_id, preflight)
        return preflight

    def record_historical_replay_evidence(self, evidence: HistoricalReplayEvidence) -> HistoricalReplayEvidence:
        case = self._case(evidence.project_id, evidence.evolution_case_id)
        if evidence.revision_id not in {case.baseline_revision_id, case.candidate_revision_id}:
            raise EvolutionIntakeError("Historical replay evidence must belong to the case baseline or candidate revision.")
        self.store.save("historical_replay_evidence", evidence.historical_replay_evidence_id, evidence.project_id, evidence)
        return evidence

    def assess_evaluation_admission(self, project_id: str, evolution_case_id: str) -> EvaluationAdmissionResult:
        case = self._case(project_id, evolution_case_id)
        replay = self._replay_complete(case)
        native = self._approved_native_contract(project_id, evolution_case_id)
        environment = self._approved_environment_contract(project_id, evolution_case_id)
        verifier = self._approved_verifier_contract(project_id, evolution_case_id)
        preflight = self._latest_preflight(project_id, evolution_case_id, environment.runtime_environment_contract_id if environment else None)
        blocking: list[str] = []
        if not native:
            blocking.append("approved production-parity native harness contract is missing")
        if not environment:
            blocking.append("approved runtime environment contract is missing")
        if not verifier:
            blocking.append("approved independent task/verifier contract is missing")
        if not preflight:
            blocking.append("runtime environment preflight has not been recorded")
        elif preflight.status == "environment_not_satisfied":
            blocking.extend(f"environment {item.name}: {item.detail}" for item in preflight.checks if item.status in {"missing", "failed"})
        elif preflight.status != "passed":
            blocking.append("runtime environment preflight is not passed")

        runtime_ready = native and environment and verifier and preflight and preflight.status == "passed"
        if runtime_ready:
            admission = EvaluationAdmission(
                project_id=project_id,
                evolution_case_id=evolution_case_id,
                level="L2_full_runtime",
                status="runtime_ready",
                allowed_operations=["baseline_execute", "candidate_execute", "skill_ablation", "compare", "report"],
                evidence_ids=[
                    native.native_harness_contract_id,
                    environment.runtime_environment_contract_id,
                    verifier.task_verifier_contract_id,
                    preflight.runtime_environment_preflight_id,
                ],
            )
        elif preflight and preflight.status == "environment_not_satisfied":
            admission = EvaluationAdmission(
                project_id=project_id,
                evolution_case_id=evolution_case_id,
                level="L1_replay" if replay else "L0_artifact_only",
                status="environment_not_satisfied",
                allowed_operations=["changeset_analysis", *( ["replay", "trace_regression_analysis"] if replay else [])],
                blocking_reasons=blocking,
                evidence_ids=[item.historical_replay_evidence_id for item in replay] + [preflight.runtime_environment_preflight_id],
            )
        elif replay:
            admission = EvaluationAdmission(
                project_id=project_id,
                evolution_case_id=evolution_case_id,
                level="L1_replay",
                status="replay_ready",
                allowed_operations=["changeset_analysis", "replay", "trace_regression_analysis"],
                blocking_reasons=blocking,
                evidence_ids=[item.historical_replay_evidence_id for item in replay],
            )
        else:
            admission = EvaluationAdmission(
                project_id=project_id,
                evolution_case_id=evolution_case_id,
                level="L0_artifact_only",
                status="contract_incomplete" if blocking else "analysis_only",
                allowed_operations=["changeset_analysis"],
                blocking_reasons=blocking,
            )
        self.store.save("evaluation_admission", admission.evaluation_admission_id, project_id, admission)
        pipeline = self._queue_pipeline(case, admission, preflight) if admission.status == "runtime_ready" else None
        return EvaluationAdmissionResult(admission, pipeline)

    def _queue_pipeline(
        self,
        case: AgentEvolutionCase,
        admission: EvaluationAdmission,
        preflight: RuntimeEnvironmentPreflight | None,
    ) -> EvaluationPipeline:
        assert preflight and preflight.environment_fingerprint
        existing = [
            item for item in self.store.list("evaluation_pipeline", EvaluationPipeline, case.project_id)
            if item.evolution_case_id == case.evolution_case_id and item.status in {"queued", "running"}
        ]
        if existing:
            return existing[-1]
        pipeline = EvaluationPipeline(
            project_id=case.project_id,
            evolution_case_id=case.evolution_case_id,
            admission_id=admission.evaluation_admission_id,
            stages=[
                "reset_baseline_environment", "execute_baseline_agent", "capture_baseline_trace",
                "reset_candidate_environment", "execute_candidate_agent", "capture_candidate_trace",
                "independent_verifier", "skill_ablation", "compare", "report_contract",
            ],
            environment_fingerprint=preflight.environment_fingerprint,
        )
        self.store.save("evaluation_pipeline", pipeline.evaluation_pipeline_id, case.project_id, pipeline)
        return pipeline

    def _case(self, project_id: str, evolution_case_id: str) -> AgentEvolutionCase:
        case = self.store.get("agent_evolution_case", evolution_case_id, AgentEvolutionCase)
        if not case or case.project_id != project_id:
            raise EvolutionIntakeError("Evolution case not found in this project.")
        return case

    def _replay_complete(self, case: AgentEvolutionCase) -> list[HistoricalReplayEvidence]:
        records = [
            item for item in self.store.list("historical_replay_evidence", HistoricalReplayEvidence, case.project_id)
            if item.evolution_case_id == case.evolution_case_id
        ]
        by_revision = {item.revision_id: item for item in records}
        revision_ids = (case.baseline_revision_id, case.candidate_revision_id)
        if not all(revision_id in by_revision for revision_id in revision_ids):
            return []
        return [by_revision[revision_id] for revision_id in revision_ids]

    def _approved_native_contract(self, project_id: str, case_id: str) -> NativeHarnessContract | None:
        return next((item for item in self.store.list("native_harness_contract", NativeHarnessContract, project_id)
                     if item.evolution_case_id == case_id and item.status == "approved" and item.behavior_mode == "production_parity"), None)

    def _approved_environment_contract(self, project_id: str, case_id: str) -> RuntimeEnvironmentContract | None:
        return next((item for item in self.store.list("runtime_environment_contract", RuntimeEnvironmentContract, project_id)
                     if item.evolution_case_id == case_id and item.status == "approved"), None)

    def _approved_verifier_contract(self, project_id: str, case_id: str) -> TaskVerifierContract | None:
        return next((item for item in self.store.list("task_verifier_contract", TaskVerifierContract, project_id)
                     if item.evolution_case_id == case_id and item.status == "approved"), None)

    def _latest_preflight(
        self, project_id: str, case_id: str, environment_contract_id: str | None
    ) -> RuntimeEnvironmentPreflight | None:
        records = [item for item in self.store.list("runtime_environment_preflight", RuntimeEnvironmentPreflight, project_id)
                   if item.evolution_case_id == case_id and item.environment_contract_id == environment_contract_id]
        return max(records, key=lambda item: item.created_at, default=None)

    def record_memory(self, memory: MemoryEntry) -> MemoryEntry:
        if memory.status == "verified" and memory.recorded_by == "llm":
            raise EvolutionIntakeError("An LLM may propose a memory entry but cannot directly write verified memory.")
        self.store.save("memory_entry", memory.memory_id, memory.project_id, memory)
        return memory

    def record_dependency(self, dependency: MemoryDependency) -> MemoryDependency:
        memory = self.store.get("memory_entry", dependency.memory_id, MemoryEntry)
        if not memory or memory.project_id != dependency.project_id:
            raise EvolutionIntakeError("MemoryDependency must reference memory in the same project.")
        self.store.save("memory_dependency", dependency.memory_dependency_id, dependency.project_id, dependency)
        return dependency

    def propagate_stale(self, project_id: str, evolution_changeset_id: str) -> StalePropagation:
        changeset = self.store.get("evolution_changeset", evolution_changeset_id, EvolutionChangeSet)
        if not changeset or changeset.project_id != project_id:
            raise EvolutionIntakeError("Evolution ChangeSet not found in this project.")
        changed_paths = {item.path for item in changeset.changes}
        stale_ids: list[str] = []
        work_items: list[EvolutionReviewWorkItem] = []
        records: list[tuple[str, str, str, object]] = []
        for dependency in self.store.list("memory_dependency", MemoryDependency, project_id):
            if not changed_paths.intersection(dependency.component_paths):
                continue
            memory = self.store.get("memory_entry", dependency.memory_id, MemoryEntry)
            if not memory or memory.project_id != project_id:
                continue
            updated = memory if memory.status == "stale" else memory.model_copy(
                update={"status": "stale", "invalidated_by": [*memory.invalidated_by, changeset.evolution_changeset_id]}
            )
            stale_ids.append(memory.memory_id)
            work_item = EvolutionReviewWorkItem(
                project_id=project_id,
                evolution_changeset_id=changeset.evolution_changeset_id,
                memory_id=memory.memory_id,
                reason=f"Changed paths intersect memory dependency: {', '.join(sorted(changed_paths.intersection(dependency.component_paths)))}",
            )
            work_items.append(work_item)
            records.extend([
                ("memory_entry", updated.memory_id, project_id, updated),
                ("evolution_review_work_item", work_item.evolution_review_work_item_id, project_id, work_item),
            ])
        propagation = StalePropagation(
            project_id=project_id,
            evolution_changeset_id=changeset.evolution_changeset_id,
            stale_memory_ids=stale_ids,
            review_work_item_ids=[item.evolution_review_work_item_id for item in work_items],
        )
        records.append(("stale_propagation", propagation.stale_propagation_id, project_id, propagation))
        self.store.save_many(records)  # type: ignore[arg-type]
        return propagation


def audit_intake_report_claims(claims: list[IntakeReviewClaim], expected_commits: set[str]) -> list[str]:
    """Deterministic report-quality gate; semantic review stays with a human."""
    issues: list[str] = []
    for claim in claims:
        if not claim.evidence_refs:
            issues.append(f"{claim.claim_id}: missing evidence references")
        if not claim.unknowns:
            issues.append(f"{claim.claim_id}: missing disclosed unknowns")
        joined = " ".join(claim.evidence_refs)
        if claim.topic in {"source_revision", "change_classification", "runtime_parity"}:
            missing = [commit for commit in expected_commits if commit not in joined]
            if missing:
                issues.append(f"{claim.claim_id}: evidence does not cover declared revisions")
        if claim.status in {"eligible", "eligible_with_gaps", "deferred", "rejected"} and not claim.next_evidence_action:
            issues.append(f"{claim.claim_id}: missing next evidence action")
    return issues
