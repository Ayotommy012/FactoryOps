"""Deterministic canonical IDs and string normalization matching Meterless specification."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Optional


def sha256_hex(data: str) -> str:
    """Return hex-encoded SHA-256 of UTF-8 string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """Deterministic canonical JSON serialization.

    - Object keys sorted alphabetically.
    - None/undefined values omitted at root/child dict levels.
    - No whitespace between delimiters (: and ,).
    """
    if value is None:
        return "null"
    if isinstance(value, (int, float, bool, str)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        filtered_keys = sorted(k for k, v in value.items() if v is not None)
        items = [
            f"{json.dumps(k, ensure_ascii=False)}:{canonical_json(value[k])}"
            for k in filtered_keys
        ]
        return "{" + ",".join(items) + "}"
    # Fallback to string representation for other types
    return json.dumps(str(value), ensure_ascii=False, separators=(",", ":"))


_COMBINING_MARKS_RE = re.compile(r"[\u0300-\u036f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Normalize names per Meterless stable-ids specification:

    1. NFKD normalization (decomposes accents, converts NBSP U+00A0 to space).
    2. Strip combining marks [U+0300 - U+036F].
    3. Strip zero-width characters [U+200B - U+200D, U+FEFF].
    4. Lowercase.
    5. Collapse whitespace runs to a single space.
    6. Trim.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    without_marks = _COMBINING_MARKS_RE.sub("", decomposed)
    without_zero_width = _ZERO_WIDTH_RE.sub("", without_marks)
    lowercased = without_zero_width.lower()
    collapsed = _WHITESPACE_RE.sub(" ", lowercased)
    return collapsed.strip()


def external_id(type_: str, system: str, id_: str) -> str:
    """Externally keyed entity or context: literal grammar type:system:externalId."""
    return f"{type_}:{system}:{id_}"


def entity_id_from_name(type_: str, name: str) -> str:
    """Name-keyed entity: ent_<first 16 hex of sha256(type:normalize_name(name))>."""
    norm = normalize_name(name)
    digest = sha256_hex(f"{type_}:{norm}")
    return f"ent_{digest[:16]}"


def context_id_from_attrs(
    type_: str, attrs: dict[str, Any], parent: Optional[str] = None
) -> str:
    """Context ID derived from type, canonical JSON attributes, and optional parent."""
    payload = f"{type_}:{canonical_json(attrs)}:{parent or ''}"
    digest = sha256_hex(payload)
    return f"ctx_{digest[:16]}"


def relationship_id(
    from_: str,
    type_: str,
    to_: str,
    context: Optional[str] = None,
    valid_from: Optional[str] = None,
) -> str:
    """Relationship ID: hash(from|type|to|context|valid_from).

    Re-adding the identical edge is an idempotent no-op.
    """
    payload = f"{from_}|{type_}|{to_}|{context or ''}|{valid_from or ''}"
    digest = sha256_hex(payload)
    return f"rel_{digest[:16]}"


def fact_id(
    about: str,
    predicate: str,
    value: Any,
    asserted_at: str,
    source_kind: str,
    source_url: Optional[str] = None,
) -> str:
    """Fact ID: content-addressed deduplication key."""
    val_json = canonical_json(value)
    payload = f"{about}|{predicate}|{val_json}|{asserted_at}|{source_kind}|{source_url or ''}"
    digest = sha256_hex(payload)
    return f"fact_{digest[:16]}"
