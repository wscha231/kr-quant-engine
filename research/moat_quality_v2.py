"""Evidence-bound qualitative moat/quality review.

Research-only contract for current issuer thesis evidence. The output has no
selector, portfolio, target-book, ledger, order, or production authority.
"""
from __future__ import annotations

from collections.abc import Callable
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
SOURCE_AFFILIATIONS = {
    "ISSUER",
    "CUSTOMER",
    "REGULATOR",
    "PATENT_OFFICE",
    "COURT",
    "STANDARD_BODY",
    "INDUSTRY_THIRD_PARTY",
    "OTHER_INDEPENDENT",
}
INDEPENDENT_AFFILIATIONS = SOURCE_AFFILIATIONS - {"ISSUER"}
SOURCE_TYPE_AFFILIATIONS = {
    "REGULATORY_FILING": {"ISSUER", "REGULATOR"},
    "COMPANY_DISCLOSURE": {"ISSUER"},
    "CUSTOMER_DISCLOSURE": {"CUSTOMER"},
    "PATENT_RECORD": {"PATENT_OFFICE"},
    "COURT_OR_REGULATORY_DECISION": {"COURT", "REGULATOR"},
    "TECHNICAL_STANDARD": {"STANDARD_BODY"},
    "INDUSTRY_DATA": {"INDUSTRY_THIRD_PARTY"},
    "OTHER_PRIMARY": SOURCE_AFFILIATIONS,
}
DIRECTIONS = {"SUPPORT", "CHALLENGE"}

# Freshness means "last independently verified", not original publication age.
# Long-lived facts such as patents can be old, but their current status must be
# re-verified from a current authoritative record before they support a packet.
MAX_VERIFICATION_AGE_DAYS = {
    "REGULATORY_FILING": 548,
    "COMPANY_DISCLOSURE": 365,
    "CUSTOMER_DISCLOSURE": 365,
    "PATENT_RECORD": 365,
    "COURT_OR_REGULATORY_DECISION": 548,
    "TECHNICAL_STANDARD": 548,
    "INDUSTRY_DATA": 180,
    "OTHER_PRIMARY": 365,
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}$")
MAX_RAW_BYTES = 16 * 1024 * 1024

RawResolver = Callable[[str, str], bytes]


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
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verify_raw(
    source_id: str,
    expected_sha256: str,
    raw_resolver: RawResolver,
) -> None:
    _require(callable(raw_resolver), "raw_resolver_required")
    try:
        raw = raw_resolver(source_id, expected_sha256)
    except Exception as exc:
        raise MoatQualityContractError("raw_evidence_unavailable") from exc
    _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_RAW_BYTES, "raw_evidence_bytes")
    _require(hashlib.sha256(raw).hexdigest() == expected_sha256, "raw_evidence_hash_mismatch")


def _normalize_evidence(
    row: Any,
    *,
    asset_id: str,
    issuer_id: str,
    as_of: datetime,
    cutoff: datetime,
    reviewed_at: datetime,
    raw_resolver: RawResolver,
) -> dict[str, Any]:
    _require(isinstance(row, dict), "evidence_object")
    evidence_id = _identifier(row.get("evidence_id"), "evidence_id")
    source_id = _identifier(row.get("source_id"), "source_id")
    independence_group = _identifier(row.get("independence_group"), "independence_group")
    subject_asset_id = _identifier(row.get("subject_asset_id"), "subject_asset_id")
    subject_issuer_id = _identifier(row.get("subject_issuer_id"), "subject_issuer_id")
    _require(
        subject_asset_id == asset_id and subject_issuer_id == issuer_id,
        "evidence_subject_identity",
    )

    source_type = row.get("source_type")
    _require(source_type in SOURCE_TYPES, "evidence_source_type")
    source_affiliation = row.get("source_affiliation")
    _require(source_affiliation in SOURCE_AFFILIATIONS, "evidence_source_affiliation")
    _require(
        source_affiliation in SOURCE_TYPE_AFFILIATIONS[source_type],
        "evidence_source_affiliation_mismatch",
    )
    direction = row.get("direction")
    _require(direction in DIRECTIONS, "evidence_direction")

    published_at = _stamp(row.get("published_at"), "evidence_published_at")
    available_at = _stamp(row.get("available_at"), "evidence_available_at")
    verified_at = _stamp(row.get("verified_at"), "evidence_verified_at")
    valid_through = _stamp(row.get("valid_through"), "evidence_valid_through")
    _require(published_at <= available_at, "publication_after_availability")
    _require(available_at <= verified_at, "availability_after_verification")
    _require(verified_at <= as_of, "future_evidence")
    _require(as_of <= reviewed_at <= cutoff, "review_after_cutoff")
    _require(as_of <= valid_through, "expired_evidence")
    verification_age = (as_of - verified_at).total_seconds() / 86400.0
    _require(
        verification_age <= MAX_VERIFICATION_AGE_DAYS[source_type],
        "stale_evidence",
    )

    raw_sha256 = _hash(row.get("raw_sha256"), "evidence_raw_sha256")
    _verify_raw(source_id, raw_sha256, raw_resolver)
    claim = _text(row.get("claim"), "evidence_claim", limit=1000)
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "independence_group": independence_group,
        "subject_asset_id": subject_asset_id,
        "subject_issuer_id": subject_issuer_id,
        "source_type": source_type,
        "source_affiliation": source_affiliation,
        "direction": direction,
        "published_at": row["published_at"],
        "available_at": row["available_at"],
        "verified_at": row["verified_at"],
        "valid_through": row["valid_through"],
        "raw_sha256": raw_sha256,
        "claim": claim,
    }


def evaluate_packet(
    packet: Any,
    cutoff: str,
    raw_resolver: RawResolver,
) -> dict[str, Any]:
    """Validate one qualitative packet and compute an unweighted research index.

    ``cutoff`` is mandatory and must come from the trusted caller clock, not the
    packet. ``raw_resolver`` must resolve immutable source bytes so claimed raw
    hashes are actually verified.
    """
    _require(isinstance(packet, dict), "packet_object")
    _require(packet.get("schema") == SCHEMA, "schema")
    asset_id = _identifier(packet.get("asset_id"), "asset_id")
    issuer_id = _identifier(packet.get("issuer_id"), "issuer_id")
    as_of = _stamp(packet.get("as_of"), "as_of")
    reviewed_at = _stamp(packet.get("reviewed_at"), "reviewed_at")
    cutoff_stamp = _stamp(cutoff, "cutoff")
    _require(as_of <= reviewed_at <= cutoff_stamp, "packet_time_order")
    _require(packet.get("review_status") == "RESEARCH_REVIEWED", "review_status")

    dimensions = packet.get("dimensions")
    _require(isinstance(dimensions, dict), "dimensions_object")
    _require(set(dimensions) == set(DIMENSIONS), "dimension_set")

    normalized_dimensions: dict[str, dict[str, Any]] = {}
    evidence_ids: set[str] = set()
    independence_groups: set[str] = set()
    source_types: set[str] = set()
    source_affiliations: set[str] = set()
    support_count = 0
    challenge_count = 0
    assessments: list[float] = []
    confidences: list[float] = []

    for dimension in DIMENSIONS:
        raw = dimensions[dimension]
        _require(isinstance(raw, dict), f"dimension_object:{dimension}")
        assessment = _number(raw.get("assessment"), f"assessment:{dimension}")
        confidence = _number(raw.get("confidence"), f"confidence:{dimension}")
        counter_argument = _text(
            raw.get("counter_argument"),
            f"counter_argument:{dimension}",
            limit=1500,
        )
        reconciliation = _text(
            raw.get("reconciliation"),
            f"reconciliation:{dimension}",
            limit=2000,
        )
        invalidation = raw.get("invalidation_conditions")
        _require(
            isinstance(invalidation, list) and bool(invalidation),
            f"invalidation_conditions:{dimension}",
        )
        normalized_invalidation = [
            _text(item, f"invalidation_condition:{dimension}", limit=800)
            for item in invalidation
        ]
        _require(
            len(set(normalized_invalidation)) == len(normalized_invalidation),
            f"duplicate_invalidation:{dimension}",
        )

        evidence = raw.get("evidence")
        _require(
            isinstance(evidence, list) and bool(evidence),
            f"evidence_required:{dimension}",
        )
        normalized_evidence = []
        dimension_groups: set[str] = set()
        dimension_support = []
        dimension_challenge = []
        for item in evidence:
            normalized = _normalize_evidence(
                item,
                asset_id=asset_id,
                issuer_id=issuer_id,
                as_of=as_of,
                cutoff=cutoff_stamp,
                reviewed_at=reviewed_at,
                raw_resolver=raw_resolver,
            )
            _require(
                normalized["evidence_id"] not in evidence_ids,
                "duplicate_evidence_id",
            )
            evidence_ids.add(normalized["evidence_id"])
            independence_groups.add(normalized["independence_group"])
            dimension_groups.add(normalized["independence_group"])
            source_types.add(normalized["source_type"])
            source_affiliations.add(normalized["source_affiliation"])
            if normalized["direction"] == "SUPPORT":
                support_count += 1
                dimension_support.append(normalized)
            else:
                challenge_count += 1
                dimension_challenge.append(normalized)
            normalized_evidence.append(normalized)

        # Positive moat claims cannot be management-only; neutral assessments
        # require evidence on both sides, and weak assessments require challenge.
        if assessment > 0.5:
            _require(bool(dimension_support), f"support_required:{dimension}")
            _require(
                any(
                    item["source_affiliation"] in INDEPENDENT_AFFILIATIONS
                    for item in dimension_support
                ),
                f"independent_support_required:{dimension}",
            )
        elif assessment < 0.5:
            _require(bool(dimension_challenge), f"challenge_required:{dimension}")
        else:
            _require(
                bool(dimension_support) and bool(dimension_challenge),
                f"balanced_evidence_required:{dimension}",
            )

        normalized_dimensions[dimension] = {
            "assessment": assessment,
            "confidence": confidence,
            "counter_argument": counter_argument,
            "reconciliation": reconciliation,
            "invalidation_conditions": normalized_invalidation,
            "evidence": normalized_evidence,
            "independent_group_count": len(dimension_groups),
        }
        assessments.append(assessment)
        confidences.append(confidence)

    # Packet-level independence is also required, so six dimensions cannot all
    # trace back to one economic source even if labels differ.
    _require(
        len(independence_groups) >= 2,
        "insufficient_independent_source_groups",
    )
    _require(
        bool(source_affiliations & INDEPENDENT_AFFILIATIONS),
        "non_management_corroboration_required",
    )

    score = sum(assessments) / len(assessments)
    confidence = sum(confidences) / len(confidences)
    weakest_dimension = min(
        DIMENSIONS,
        key=lambda key: normalized_dimensions[key]["assessment"],
    )
    return {
        "schema": RESULT_SCHMA,
        "status": "COMPLETE_RESEARCH_REVIEW",
        "asset_id": asset_id,
        "issuer_id": issuer_id,
        "as_of": packet["as_of"],
        "reviewed_at": packet["reviewed_at"],
        "moat_quality_score": score,
        "evidence_confidence": confidence,
        "weakest_dimension": weakest_dimension,
        "dimension_scores": {
            key: normalized_dimensions[key]["assessment"] for key in DIMENSIONS
        },
        "dimension_confidence": {
            key: normalized_dimensions[key]["confidence"] for key in DIMENSIONS
        },
        "evidence_count": len(evidence_ids),
        "independent_source_group_count": len(independence_groups),
        "source_type_count": len(source_types),
        "source_affiliation_count": len(source_affiliations),
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


__all__ = [
    "DIMENSIONS",
    "DIRECTIONS",
    "INDEPENDENT_AFFILIATIONS",
    "MAX_VERIFICATION_AGE_DAYS",
    "MoatQualityContractError",
    "RESULT_SCHMA",
    "SCHEMA",
    "SOURCE_AFFILIATIONS",
    "SOURCE_TYPE_AFFILIATIONS",
    "SOURCE_TYPES",
    "canonical_sha256",
    "evaluate_packet",
]
