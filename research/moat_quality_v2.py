"""Evidence-bound qualitative moat/quality review.

This module is intentionally research-only. It structures qualitative evidence
that can later inform thesis confidence or a separately validated selector, but
it does not alter ranking weights, targets, portfolio sizing, or orders.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "moat-quality-v2"
RESULT_SCHEMA = "moat-quality-v2-result"

DIMENSIONS = (
    "qualification_switching_cost",
    "market_structure_position",
    "ip_patent_durability",
    "pricing_power",
    "replacement_difficulty",
    "next_generation_relevance",
)

SOURCE_TYPES = {
    "REGULATORY_FILING",
    "COMPANY_DISCLOSURE",
    "CUSTOMER_DISCLOSURE",
    "PATENT_RECORD",
    "COURT_OR_REGULATORY_DECISION",
    "TECHNICAL_STANDARD",
    "INDUSTRY_DATA",
    "OTHER_PRIMARY",
}
DIRECTIONS = {"SUPPORT", "CHALLENGE"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}$")


class MoatQualityContractError(ValueError):
    """Raised when evidence violates the moat/quality research contract."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MoatQualityContractError(code)


def _text(value: Any, code: str, *, limit: int = 2000) -> str:
    _require(isinstance(value, str), code)
    value = value.strip()
    _require(bool(value) and len(value) <= limit, code)
    return value


def _identifier(value: Any, code: str) -> str:
    value = _text(value, code, limit=200)
    _require(bool(_ID.fullmatch(value)), code)
    return value


def _number(value: Any, code: str) -> float:
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), code)
    value = float(value)
    _require(math.isfinite(value) and 0.0 <= value <= 1.0, code)
    return value


def _stamp(value: Any, code: str) -> datetime:
    value = _text(value, code, limit=80)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MoatQualityContractError(code) from exc
    _require(stamp.tzinfo is not None and stamp.utcoffset() is not None, code)
    return stamp


def _hash(value: Any, code: str) -> str:
    value = _text(value, code, limit=64).lower()
    _require(bool(_HEX64.fullmatch(value)), code)
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_evidence(row: Any, as_of: datetime, cutoff: datetime, reviewed_at: datetime) -> dict[str, Any]:
    _require(isinstance(row, dict), "evidence_object")
    evidence_id = _identifier(row.get("evidence_id"), "evidence_id")
    source_id = _identifier(row.get("source_id"), "source_id")
    independence_group = _identifier(row.get("independence_group"), "independence_group")
    source_type = row.get("source_type")
    _require(source_type in SOURCE_TYPES, "evidence_source_type")
    direction = row.get("direction")
    _require(direction in DIRECTIONS, "evidence_direction")
    published_at = _stamp(row.get("published_at"), "evidence_published_at")
    available_at = _stamp(row.get("available_at"), "evidence_available_at")
    _require(published_at <= available_at, "evidence_publication_after_availability")
    _require(available_at <= as_of <= reviewed_at <= cutoff, "future_evidence")
    raw_sha256 = _hash(row.get("raw_sha256"), "evidence_raw_sha256")
    claim = _text(row.get("claim"), "evidence_claim", limit=1000)
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "independence_group": independence_group,
        "source_type": source_type,
        "direction": direction,
        "published_at": row["published_at"],
        "available_at": row["available_at"],
        "raw_sha256": raw_sha256,
        "claim": claim,
    }


def evaluate_packet(packet: Any, cutoff: str | None = None) -> dict[str, Any]:
    """Validate one reviewed qualitative packet and compute an unweighted index.

    The score is only emitted when every required dimension is present with
    explicit evidence, confidence, a counter-argument, and invalidation
    conditions. Confidence is reported separately and never silently multiplied
    into the score. No investment authority is granted by a valid result.
    """
    _require(isinstance(packet, dict), "packet_object")
    _require(packet.get("schema") == SCHEMA, "schema")
    asset_id = _identifier(packet.get("asset_id"), "asset_id")
    issuer_id = _identifier(packet.get("issuer_id"), "issuer_id")
    as_of = _stamp(packet.get("as_of"), "as_of")
    reviewed_at = _stamp(packet.get("reviewed_at"), "reviewed_at")
    cutoff_stamp = _stamp(cutoff or packet.get("reviewed_at"), "cutoff")
    _require(as_of <= reviewed_at <= cutoff_stamp, "packet_time_order")
    _require(packet.get("review_status") == "RESEARCH_REVIEWED", "review_status")

    dimensions = packet.get("dimensions")
    _require(isinstance(dimensions, dict), "dimensions_object")
    _require(set(dimensions) == set(DIMENSIONS), "dimension_set")

    normalized_dimensions: dict[str, dict[str, Any]] = {}
    evidence_ids: set[str] = set()
    independence_groups: set[str] = set()
    source_types: set[str] = set()
    support_count = 0
    challenge_count = 0
    assessments: list[float] = []
    confidences: list[float] = []

    for dimension in DIMENSIONS:
        raw = dimensions[dimension]
        _require(isinstance(raw, dict), f"dimension_object:{dimension}")
        assessment = _number(raw.get("assessment"), f"assessment:{dimension}")
        confidence = _number(raw.get("confidence"), f"confidence:{dimension}")
        counter_argument = _text(raw.get("counter_argument"), f"counter_argument:{dimension}", limit=1500)
        invalidation = raw.get("invalidation_conditions")
        _require(isinstance(invalidation, list) and bool(invalidation), f"invalidation_conditions:{dimension}")
        normalized_invalidation = [
            _text(item, f"invalidation_condition:{dimension}", limit=800) for item in invalidation
        ]
        _require(len(set(normalized_invalidation)) == len(normalized_invalidation), f"duplicate_invalidation:{dimension}")

        evidence = raw.get("evidence")
        _require(isinstance(evidence, list) and bool(evidence), f"evidence_required:{dimension}")
        normalized_evidence = []
        for item in evidence:
            normalized = _normalize_evidence(item, as_of, cutoff_stamp, reviewed_at)
            _require(normalized["evidence_id"] not in evidence_ids, "duplicate_evidence_id")
            evidence_ids.add(normalized["evidence_id"])
            independence_groups.add(normalized["independence_group"])
            source_types.add(normalized["source_type"])
            support_count += int(normalized["direction"] == "SUPPORT")
            challenge_count += int(normalized["direction"] == "CHALLENGE")
            normalized_evidence.append(normalized)

        normalized_dimensions[dimension] = {
            "assessment": assessment,
            "confidence": confidence,
            "counter_argument": counter_argument,
            "invalidation_conditions": normalized_invalidation,
            "evidence": normalized_evidence,
        }
        assessments.append(assessment)
        confidences.append(confidence)

    # An internal counter-argument is mandatory in every dimension, while an
    # external challenge source is encouraged but not forced: some primary-source
    # packets may legitimately have no independently published bear evidence yet.
    score = sum(assessments) / len(assessments)
    confidence = sum(confidences) / len(confidences)
    weakest_dimension = min(DIMENSIONS, key=lambda key: normalized_dimensions[key]["assessment"])
    result = {
        "schema": RESULT_SCHEMA,
        "status": "COMPLETE_RESEARCH_REVIEW",
        "asset_id": asset_id,
        "issuer_id": issuer_id,
        "as_of": packet["as_of"],
        "reviewed_at": packet["reviewed_at"],
        "moat_quality_score": score,
        "evidence_confidence": confidence,
        "weakest_dimension": weakest_dimension,
        "dimension_scores": {key: normalized_dimensions[key]["assessment"] for key in DIMENSIONS},
        "dimension_confidence": {key: normalized_dimensions[key]["confidence"] for key in DIMENSIONS},
        "evidence_count": len(evidence_ids),
        "independent_source_group_count": len(independence_groups),
        "source_type_count": len(source_types),
        "support_evidence_count": support_count,
        "challenge_evidence_count": challenge_count,
        "packet_sha256": canonical_sha256(packet),
        "score_method": "UNWEIGHTED_MEAN_SIX_REQUIRED_DIMENSIONS",
        "missing_values_imputed": False,
        "historical_pit_certified": False,
        "oos_validated": False,
        "selector_eligible": False,
        "portfolio_weight_effect": 0.0,
        "target_book_write_allowed": False,
        "orders_allowed": False,
        "production_authority": False,
    }
    return result


__all__ = [
    "DIMENSIONS",
    "DIRECTIONS",
    "MoatQualityContractError",
    "RESULT_SCHEMA",
    "SCHEMA",
    "SOURCE_TYPES",
    "canonical_sha256",
    "evaluate_packet",
]
