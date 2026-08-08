"""Server-owned upload references for browser-to-scan project intake."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .store import Store


UploadStatus = Literal["stored"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectUpload(BaseModel):
    """Immutable non-secret identity of one server-owned uploaded source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.project-upload.v1"] = "aig.project-upload.v1"
    upload_id: str = Field(default_factory=lambda: f"upload_{uuid4().hex}", min_length=1)
    project_id: str = Field(min_length=1)
    original_filename: str = Field(min_length=1)
    source_kind: Literal["repository", "package"]
    source_path: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    media_type: str | None = None
    status: UploadStatus = "stored"
    created_at: str = Field(default_factory=_now)


class ProjectUploadRepository:
    _KIND = "project_upload"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, upload: ProjectUpload) -> ProjectUpload:
        existing = self.store.get(self._KIND, upload.upload_id, ProjectUpload)
        if existing and existing.model_dump(mode="json") != upload.model_dump(mode="json"):
            raise ValueError(f"Project Upload {upload.upload_id} already exists with different contents.")
        self.store.save(self._KIND, upload.upload_id, upload.project_id, upload)
        return upload

    def get(self, project_id: str, upload_id: str) -> ProjectUpload | None:
        upload = self.store.get(self._KIND, upload_id, ProjectUpload)
        if upload is None or upload.project_id != project_id:
            return None
        return upload

    def list(self, project_id: str) -> list[ProjectUpload]:
        return self.store.list(self._KIND, ProjectUpload, project_id)


def fingerprint_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


__all__ = ["ProjectUpload", "ProjectUploadRepository", "UploadStatus", "fingerprint_file"]
