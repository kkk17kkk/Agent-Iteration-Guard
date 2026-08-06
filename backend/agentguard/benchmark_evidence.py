"""Optional import of externally-produced benchmark result summaries.

v1 imports evidence only.  It never connects to, schedules, or executes an
external benchmark.  Imported values stay explicitly external so the local
execution/oracle evidence remains the release gate's primary source.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .store import Store


BenchmarkUnit = Literal[
    "ratio", "count", "seconds", "milliseconds", "tokens", "usd", "custom"
]


class BenchmarkMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str = Field(min_length=1, max_length=120)
    unit: BenchmarkUnit = "custom"
    baseline_value: float
    candidate_value: float
    sample_count: int | None = Field(default=None, ge=1)


class BenchmarkEvidence(BaseModel):
    """Sanitized, immutable external benchmark summary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.benchmark-evidence.v1"] = "aig.benchmark-evidence.v1"
    evidence_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    benchmark_name: str = Field(min_length=1, max_length=160)
    source_ref: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(min_length=64, max_length=64)
    metrics: list[BenchmarkMetric] = Field(min_length=1, max_length=32)
    evidence_level: Literal["external"] = "external"
    imported_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    integrity_hash: str = Field(min_length=64, max_length=64)


class BenchmarkEvidenceRepository:
    _KIND = "benchmark_evidence"

    def __init__(self, store: Store) -> None:
        self.store = store

    def import_result(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        source_ref: str,
        source_bytes: bytes | None = None,
    ) -> BenchmarkEvidence:
        benchmark_name = _text(payload, "benchmark_name", "benchmark", "name")
        before = _mapping_or_number(payload, "before", "baseline")
        after = _mapping_or_number(payload, "after", "candidate")
        metrics = _parse_metrics(payload, before, after)
        raw = source_bytes if source_bytes is not None else json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        source_sha256 = hashlib.sha256(raw).hexdigest()
        evidence_id = "benchmark_" + hashlib.sha256(
            f"{project_id}:{benchmark_name}:{source_sha256}".encode("utf-8")
        ).hexdigest()[:16]
        semantic = {
            "project_id": project_id,
            "benchmark_name": benchmark_name,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "metrics": [item.model_dump(mode="json") for item in metrics],
            "evidence_level": "external",
        }
        record = BenchmarkEvidence(
            evidence_id=evidence_id,
            project_id=project_id,
            benchmark_name=benchmark_name,
            source_ref=source_ref,
            source_sha256=source_sha256,
            metrics=metrics,
            evidence_refs=[f"benchmark:{evidence_id}"],
            integrity_hash=_hash(semantic),
        )
        existing = self.store.get(self._KIND, evidence_id, BenchmarkEvidence)
        if existing is not None:
            if existing.integrity_hash != record.integrity_hash:
                raise ValueError(
                    f"Benchmark evidence {evidence_id} already exists with different contents."
                )
            return existing
        self.store.save(self._KIND, evidence_id, project_id, record)
        return record

    def list(self, project_id: str) -> list[BenchmarkEvidence]:
        return sorted(
            self.store.list(self._KIND, BenchmarkEvidence, project_id),
            key=lambda item: (item.benchmark_name, item.evidence_id),
        )

    def get(self, project_id: str, evidence_id: str) -> BenchmarkEvidence | None:
        evidence = self.store.get(self._KIND, evidence_id, BenchmarkEvidence)
        if evidence is None or evidence.project_id != project_id:
            return None
        return evidence


def recompute_integrity_hash(evidence: BenchmarkEvidence) -> str:
    """Recompute an imported evidence record's internal integrity binding."""

    return _hash({
        "project_id": evidence.project_id,
        "benchmark_name": evidence.benchmark_name,
        "source_ref": evidence.source_ref,
        "source_sha256": evidence.source_sha256,
        "metrics": [item.model_dump(mode="json") for item in evidence.metrics],
        "evidence_level": evidence.evidence_level,
    })


def _parse_metrics(
    payload: Mapping[str, object],
    before: float | Mapping[str, object],
    after: float | Mapping[str, object],
) -> list[BenchmarkMetric]:
    explicit = payload.get("metrics")
    if explicit is not None:
        parsed = _parse_explicit_metrics(explicit)
        if parsed:
            return parsed

    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        before_value, after_value = _normalize_metric_pair("success", float(before), float(after))
        return [_metric("success", before_value, after_value, unit="ratio")]
    assert isinstance(before, Mapping) and isinstance(after, Mapping)
    result: list[BenchmarkMetric] = []
    for name in sorted(set(before) & set(after)):
        try:
            before_value = _number(before[name])
            after_value = _number(after[name])
        except ValueError:
            continue
        before_value, after_value = _normalize_metric_pair(str(name), before_value, after_value)
        unit = "ratio" if _looks_like_ratio(name, before_value, after_value) else "custom"
        result.append(_metric(str(name), before_value, after_value, unit=unit))
    if not result:
        raise ValueError("Benchmark result must contain at least one shared numeric metric before and after.")
    return result


def _parse_explicit_metrics(value: object) -> list[BenchmarkMetric]:
    if isinstance(value, Mapping):
        items = []
        for name, raw in value.items():
            if not isinstance(raw, Mapping):
                raise ValueError("Benchmark metrics mapping values must be objects.")
            items.append({"metric_name": name, **raw})
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("Benchmark metrics must be an object or array.")

    result: list[BenchmarkMetric] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            raise ValueError("Each benchmark metric must be an object.")
        name = _text(raw, "metric_name", "name", "metric")
        before = raw.get("baseline_value", raw.get("before"))
        after = raw.get("candidate_value", raw.get("after"))
        if before is None or after is None:
            raise ValueError(f"Benchmark metric {name!r} must contain before and after values.")
        before_value, after_value = _normalize_metric_pair(name, _number(before), _number(after))
        unit = str(raw.get("unit", "ratio" if _looks_like_ratio(name, before_value, after_value) else "custom"))
        result.append(_metric(name, before_value, after_value, unit=unit))
    return result


def _metric(name: str, before: object, after: object, *, unit: str) -> BenchmarkMetric:
    if unit == "ratio" and (not 0 <= float(before) <= 1 or not 0 <= float(after) <= 1):
        raise ValueError(f"Ratio metric {name!r} must be between 0 and 1 after percent normalization.")
    return BenchmarkMetric(
        metric_name=name,
        unit=unit,  # type: ignore[arg-type]
        baseline_value=float(before),
        candidate_value=float(after),
    )


def _mapping_or_number(payload: Mapping[str, object], *keys: str) -> float | Mapping[str, object]:
    for key in keys:
        if key in payload:
            value = payload[key]
            if isinstance(value, Mapping):
                return value
            return _number(value)
    raise ValueError(f"Benchmark result must contain one of {keys}.")


def _text(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Benchmark result must contain a non-empty {keys[0]}.")


def _number(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a benchmark metric.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            if text.endswith("%"):
                return float(text[:-1]) / 100
            return float(text)
        except ValueError as error:
            raise ValueError(f"Not a numeric benchmark metric: {value!r}.") from error
    raise ValueError(f"Not a numeric benchmark metric: {value!r}.")


def _looks_like_ratio(name: str, before: float, after: float) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("success", "pass", "rate", "accuracy", "score")) and 0 <= before <= 1 and 0 <= after <= 1


def _normalize_metric_pair(name: str, before: float, after: float) -> tuple[float, float]:
    lowered = name.lower()
    if any(token in lowered for token in ("success", "pass", "rate", "accuracy", "score")):
        if 1 < before <= 100 and 1 < after <= 100:
            return before / 100, after / 100
    return before, after


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "BenchmarkEvidence",
    "BenchmarkEvidenceRepository",
    "BenchmarkMetric",
    "BenchmarkUnit",
    "recompute_integrity_hash",
]
