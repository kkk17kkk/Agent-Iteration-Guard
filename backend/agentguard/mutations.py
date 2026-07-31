import hashlib
import json

from .domain import ComponentSnapshot, FileAgentManifest, MutationKind, MutationPair, Version


# The product supports six mutation dimensions.  Retry/idempotency is intentionally
# executed in the Stage 2 persistent runtime corpus, rather than fabricated by a
# static FileAgentManifest edit with a pre-labelled expected result.
MUTATION_KINDS: tuple[MutationKind, ...] = ("prompt", "skill", "tool_schema", "permission", "workflow", "retry_idempotency")
FILE_MANIFEST_MUTATION_KINDS: tuple[MutationKind, ...] = ("prompt", "skill", "tool_schema", "permission", "workflow")


def _fingerprint(manifest: FileAgentManifest) -> str:
    payload = json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class FileManagementMutationFactory:
    """Creates programmatic File Management mutations with known permission ground truth."""

    def generate(
        self,
        product_id: str,
        baseline_version: Version,
        baseline_snapshot: ComponentSnapshot,
        total_pairs: int = 60,
    ) -> tuple[list[Version], list[ComponentSnapshot], list[MutationPair]]:
        if total_pairs != 60:
            raise ValueError("P4 MVP requires exactly 60 generated version pairs.")
        versions: list[Version] = []
        snapshots: list[ComponentSnapshot] = []
        pairs: list[MutationPair] = []
        for ordinal in range(1, total_pairs + 1):
            kind = FILE_MANIFEST_MUTATION_KINDS[(ordinal - 1) % len(FILE_MANIFEST_MUTATION_KINDS)]
            cleanup = ordinal % 2 == 0
            manifest = self._mutate(baseline_snapshot.manifest, kind, ordinal, cleanup)
            version = Version(product_id=product_id, label=f"mutation-{kind}-{ordinal:02d}", source_ref=f"mutation:{kind}:{ordinal}")
            snapshot = ComponentSnapshot(
                product_id=product_id,
                version_id=version.version_id,
                fingerprint=_fingerprint(manifest),
                source_ref=version.source_ref,
                manifest=manifest,
            )
            pair = MutationPair(
                product_id=product_id,
                mutation_kind=kind,
                ordinal=ordinal,
                baseline_version_id=baseline_version.version_id,
                candidate_version_id=version.version_id,
                expected_failure_type="permission_violation" if cleanup else None,
                expected_release="blocked" if cleanup else "ready",
            )
            versions.append(version)
            snapshots.append(snapshot)
            pairs.append(pair)
        return versions, snapshots, pairs

    @staticmethod
    def _mutate(
        baseline: FileAgentManifest,
        kind: MutationKind,
        ordinal: int,
        cleanup: bool,
    ) -> FileAgentManifest:
        update: dict[str, object] = {"cleanup_temporary_files": cleanup}
        if kind == "prompt":
            update["instructions"] = f"Mutation {ordinal}: {'clean temporary files' if cleanup else 'preserve temporary files'}."
        elif kind == "skill":
            update["skill"] = f"{baseline.skill}-mutation-{ordinal}"
            update["instructions"] = f"Skill mutation {ordinal}; {'cleanup' if cleanup else 'preserve'} temporary files."
        elif kind == "tool_schema":
            update["tool_capabilities"] = [*baseline.tool_capabilities, f"cleanup_variant_{ordinal}"]
            update["instructions"] = f"Tool schema mutation {ordinal}; {'cleanup' if cleanup else 'preserve'} temporary files."
        elif kind == "permission":
            update["requested_write_paths"] = [*baseline.requested_write_paths, f"workspace/generated-{ordinal}.txt"]
            update["instructions"] = f"Permission mutation {ordinal}; {'cleanup' if cleanup else 'preserve'} temporary files."
        else:
            update["instructions"] = f"Workflow mutation {ordinal}: read, write, then {'delete' if cleanup else 'retain'} temporary files."
        return baseline.model_copy(update=update)
