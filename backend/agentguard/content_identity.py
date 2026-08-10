"""Canonical content identities shared by persisted v1 domain records."""

from __future__ import annotations

import hashlib
import json


def canonical_fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["canonical_fingerprint"]
