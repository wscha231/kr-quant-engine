"""Cross-method investment research methodology contract.

Research-only, style-neutral evaluation over canonical pillars. Named investment
methods are explanatory lenses over the same pillars, never additive alpha
factors. Sector profiles change interpretation/evidence, not weights.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "investment-methodology-v1"
RESULT_SCHEMA = "investment-methodology-v1-result"
ASSESSMENT_ANCHOR_VERSION = "canonical-pillar-anchor-v1"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
ArtifactResolver = Callable[[str, str], bytes]

PILLARS = (
    "industry_structure_bottleneck",
    "moat_durability",
    "growth_runway_customer_product",
    "profitability_reinvestment_capital_efficiency",
    "valuation_margin_of_safety",
    "earnings_revision_operating_acceleration",
    "market_leadership_price_volume_rs",
    "management_governance_capital_allocation",
    "catalyst_ownership_information_edge",
    "downside_balance_sheet_cycle_regime",
)

SECTOR_PROFILES = {
    "GENERAL",
    "SEMICONDUCTOR_HARDWARE",
    "POWER_UTILITIES_INFRA",
    "PROJECT_INDUSTRIAL_SHIPBUILDING_NUCLEAR",
    "CONSUMER_BRAND_ODM",
    "BIOTECH_PRECOMMERCIAL",
    "SOFTWARE_PLATFORM",
    "FINANCIALS",
    "COMMODITY_CYCLICAL",
}

PEER_SCOPES = {
    "GLOBAL_INDUSTRY",
    "LOCAL_INDUSTRY",
    "LIFECYCLE_STAGE",
    "ASSET_CLASS",
}
ASSESSMENT_MODES = {"STANDARD", "STAGE_ADJUSTED"}
ARTIFACT_TYPES = {
    "MOAT_V2",
    "QUANT_FEATURE_PACKET",
    "ER_PACKET",
    "REGIME_PACKET",
    "GOVERNANCE_PACKET",
    "OWNERSHIP_EVENT_PACKET",
    "SECTOR_THESIS_PACKET",
    "PRIMARY_RESEARCH_PACKET",
}

CLASSIC_LENSES = {
    "GRAHAM_MARGIN_OF_SAFETY": (
        "valuation_margin_of_safety",
        "downside_balance_sheet_cycle_regime",
        "profitability_reinvestment_capital_efficiency",
    ),
    "BUFFETT_MUNGER_COMPOUNDER": (
        "moat_durability",
        "profitability_reinvestment_capital_efficiency",
        "management_governance_capital_allocation",
        "valuation_margin_of_safety",
        "growth_runway_customer_product",
    ),
    "FISHER_QUALITY_GROWTH": (
        "growth_runway_customer_product",
        "moat_durability",
        "management_governance_capital_allocation",
        "industry_structure_bottleneck",
        "earnings_revision_operating_acceleration",
    ),
    "LYNCH_GARP": (
        "growth_runway_customer_product",
        "valuation_margin_of_safety",
        "earnings_revision_operating_acceleration",
        "industry_structure_bottleneck",
    ),
    "GREENBLATT_QUALITY_VALUE": (
        "profitability_reinvestment_capital_efficiency",
        "valuation_margin_of_safety",
    ),
    "ONEIL_CANSLIM": (
        "earnings_revision_operating_acceleration",
        "growth_runway_customer_product",
        "market_leadership_price_volume_rs",
        "catalyst_ownership_information_edge",
        "downside_balance_sheet_cycle_regime",
    ),
    "MINERVINI_SUPERPERFORMANCE": (
        "earnings_revision_operating_acceleration",
        "growth_runway_customer_product",
        "market_leadership_price_volume_rs",
        "downside_balance_sheet_cycle_regime",
    ),
    "PORTER_INDUSTRY_STRUCTURE": (
        "industry_structure_bottleneck",
        "moat_durability",
    ),
    "DAMODARAN_SCENARIO_VALUATION": (
        "valuation_margin_of_safety",
        "growth_runway_customer_product",
        "profitability_reinvestment_capital_efficiency",
        "downside_balance_sheet_cycle_regime",
    ),
    "DRUCKENMILLER_LEADERSHIP_LIQUIDITY": (
        "industry_structure_bottleneck",
        "earnings_revision_operating_acceleration",
        "market_leadership_price_volume_rs",
        "catalyst_ownership_information_edge",
        "downside_balance_sheet_cycle_regime",
    ),
    "HOWARD_MARKS_CYCLE_RISK": (
        "valuation_margin_of_safety",
        "downside_balance_sheet_cycle_regime",
        "industry_structure_bottleneck",
        "market_leadership_price_volume_rs",
    ),
    "PIOTROSKI_FINANCIAL_STRENGTH": (
        "profitability_reinvestment_capital_efficiency",
        "downside_balance_sheet_cycle_regime",
        "management_governance_capital_allocation",
        "earnings_revision_operating_acceleration",
    ),
    "SOROS_REFLEXIVITY": (
        "industry_structure_bottleneck",
        "market_leadership_price_volume_rs",
        "catalyst_ownership_information_edge",
        "downside_balance_sheet_cycle_regime",
    ),
    "MAUBOUSSIN_COMPETITIVE_ADVANTAGE_PERIOD": (
        "industry_structure_bottleneck",
        "moat_durability",
        "profitability_reinvestment_capital_efficiency",
        "growth_runway_customer_product",
        "valuation_margin_of_safety",
    ),
}

ACADEMIC_LENSES = {
    "FAMA_FRENCH_VALUE_PROFITABILITY_INVESTMENT": (
        "valuation_margin_of_safety",
        "profitability_reinvestment_capital_efficiency",
        "management_governance_capital_allocation",
        "downside_balance_sheet_cycle_regime",
    ),
    "JEGADEESH_TITMAN_MOMENTUM": (
        "market_leadership_price_volume_rs",
        "earnings_revision_operating_acceleration",
        "catalyst_ownership_information_edge",
    ),
    "NOVY_MARX_GROSS_PROFITABILITY": (
        "profitability_reinvestment_capital_efficiency",
        "growth_runway_customer_product",
        "valuation_margin_of_safety",
    ),
    "ASNESS_QUALITY_VALUE_MOMENTUM": (
        "profitability_reinvestment_capital_efficiency",
        "management_governance_capital_allocation",
        "valuation_margin_of_safety",
        "market_leadership_price_volume_rs",
        "downside_balance_sheet_cycle_regime",
    ),
    "POST_EARNINGS_ANNOUNCEMENT_DRIFT": (
        "earnings_revision_operating_acceleration",
        "market_leadership_price_volume_rs",
        "catalyst_ownership_information_edge",
    ),
}

PORTFOLIO_THEORIES_A5_ONLY = (
    "MARKOWITZ_MEAN_VARIANCE",
    "FRACTIONAL_KELLY",
    "BLACK_LITTERMAN",
    "RISK_BUDGETING",
    "FACTOR_RESIDUAL_ALPHA",
)

PROJECT_LENSES = {
    "PROJECT_QUALITY_COMPOUNDER": (
        "moat_durability",
        "growth_runway_customer_product",
        "profitability_reinvestment_capital_efficiency",
        "management_governance_capital_allocation",
        "valuation_margin_of_safety",
    ),
    "PROJECT_MARKET_LEADER": (
        "industry_structure_bottleneck",
        "growth_runway_customer_product",
        "earnings_revision_operating_acceleration",
        "market_leadership_price_volume_rs",
        "catalyst_ownership_information_edge",
    ),
    "PROJECT_EARLY_GROWTH_INFLECTION": (
        "industry_structure_bottleneck",
        "growth_runway_customer_product",
        "earnings_revision_operating_acceleration",
        "market_leadership_price_volume_rs",
        "catalyst_ownership_information_edge",
    ),
    "PROJECT_TURNAROUND_VALUE": (
        "valuation_margin_of_safety",
        "earnings_revision_operating_acceleration",
        "profitability_reinvestment_capital_efficiency",
        "market_leadership_price_volume_rs",
        "downside_balance_sheet_cycle_regime",
    ),
    "PROJECT_SMART_MONEY_EVENT": (
        "catalyst_ownership_information_edge",
        "earnings_revision_operating_acceleration",
        "moat_durability",
    ),
    "PROJECT_REGIME_RESILIENCE": (
        "downside_balance_sheet_cycle_regime",
        "industry_structure_bottleneck",
        "profitability_reinvestment_capital_efficiency",
        "valuation_margin_of_safety",
    ),
}
METHOD_LENSES = {**CLASSIC_LENSES, **ACADEMIC_LENSES, **PROJECT_LENSES}

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class MethodologyContractError(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MethodologyContractError(code)


def _text(value: Any, code: str, limit: int = 2000) -> str:
    _require(isinstance(value, str), code)
    value = value.strip()
    _require(bool(value) and len(value) <= limit, code)
    return value


def _identifier(value: Any, code: str) -> str:
    value = _text(value, code, 200)
    _require(bool(_ID.fullmatch(value)), code)
    return value


def _number(value: Any, code: str) -> float:
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), code)
    value = float(value)
    _require(math.isfinite(value) and 0.0 <= value <= 1.0, code)
    return value


def _integer(value: Any, code: str, minimum: int) -> int:
    _require(not isinstance(value, bool) and isinstance(value, int), code)
    _require(value >= minimum, code)
    return value


def _stamp(value: Any, code: str) -> datetime:
    value = _text(value, code, 80)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MethodologyContractError(code) from exc
    _require(stamp.tzinfo is not None and stamp.utcoffset() is not None, code)
    return stamp


def _hash(value: Any, code: str) -> str:
    value = _text(value, code, 64).lower()
    _require(bool(_HEX64.fullmatch(value)), code)
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verify_artifact(
    artifact_id: str,
    expected_sha256: str,
    resolver: ArtifactResolver,
) -> None:
    _require(callable(resolver), "artifact_resolver_required")
    try:
        raw = resolver(artifact_id, expected_sha256)
    except Exception as exc:
        raise MethodologyContractError("artifact_unavailable") from exc
    _require(
        isinstance(raw, bytes) and 0 < len(raw) <= MAX_ARTIFACT_BYTES,
        "artifact_bytes_invalid",
    )
    _require(
        hashlib.sha256(raw).hexdigest() == expected_sha256,
        "artifact_hash_mismatch",
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _lens_scores(pillar_scores: dict[str, float]) -> dict[str, float]:
    return {
        lens: _mean([pillar_scores[pillar] for pillar in pillars])
        for lens, pillars in METHOD_LENSES.items()
    }


def evaluate_packet(
    packet: Any,
    cutoff: str,
    artifact_resolver: ArtifactResolver,
) -> dict[str, Any]:
    _require(isinstance(packet, dict), "packet_object")
    _require(packet.get("schema") == SCHEMA, "schema")
    _require(
        packet.get("assessment_anchor_version") == ASSESSMENT_ANCHOR_VERSION,
        "assessment_anchor_version",
    )
    asset_id = _identifier(packet.get("asset_id"), "asset_id")
    issuer_id = _identifier(packet.get("issuer_id"), "issuer_id")
    country = _identifier(packet.get("country"), "country")
    asset_class = _identifier(packet.get("asset_class"), "asset_class")
    sector_profile = packet.get("sector_profile")
    _require(sector_profile in SECTOR_PROFILES, "sector_profile")
    as_of = _stamp(packet.get("as_of"), "as_of")
    reviewed_at = _stamp(packet.get("reviewed_at"), "reviewed_at")
    cutoff_stamp = _stamp(cutoff, "cutoff")
    _require(as_of <= reviewed_at <= cutoff_stamp, "packet_time_order")
    _require(packet.get("review_status") == "RESEARCH_REVIEWED", "review_status")

    pillars = packet.get("pillars")
    _require(isinstance(pillars, dict), "pillars_object")
    _require(set(pillars) == set(PILLARS), "pillar_set")

    absolute: dict[str, float] = {}
    peer: dict[str, float] = {}
    confidence: dict[str, float] = {}
    artifact_ids: set[str] = set()
    artifact_hashes: set[str] = set()
    stage_adjusted_count = 0
    thin_peer_group_count = 0

    for pillar in PILLARS:
        row = pillars[pillar]
        _require(isinstance(row, dict), f"pillar_object:{pillar}")
        absolute[pillar] = _number(row.get("absolute_assessment"), f"absolute:{pillar}")
        confidence[pillar] = _number(row.get("confidence"), f"confidence:{pillar}")

        mode = row.get("assessment_mode")
        _require(mode in ASSESSMENT_MODES, f"assessment_mode:{pillar}")
        if mode == "STAGE_ADJUSTED":
            stage_adjusted_count += 1
            _text(row.get("stage_adjustment_reason"), f"stage_adjustment_reason:{pillar}", 1200)

        _text(row.get("absolute_rationale"), f"absolute_rationale:{pillar}", 1600)
        _text(row.get("peer_rationale"), f"peer_rationale:{pillar}", 1600)
        _text(row.get("counter_argument"), f"counter_argument:{pillar}", 1600)
        invalidation = row.get("invalidation_conditions")
        _require(isinstance(invalidation, list) and bool(invalidation), f"invalidation:{pillar}")
        normalized_invalidations = [
            _text(item, f"invalidation_item:{pillar}", 800) for item in invalidation
        ]
        _require(
            len(set(normalized_invalidations)) == len(normalized_invalidations),
            f"duplicate_invalidation:{pillar}",
        )

        peer_group_id = _identifier(row.get("peer_group_id"), f"peer_group_id:{pillar}")
        peer_scope = row.get("peer_scope")
        _require(peer_scope in PEER_SCOPES, f"peer_scope:{pillar}")
        if peer_scope != "GLOBAL_INDUSTRY":
            _text(
                row.get("peer_scope_exception_reason"),
                f"peer_scope_exception_reason:{pillar}",
                1200,
            )
        peer_snapshot_artifact_id = _identifier(
            row.get("peer_snapshot_artifact_id"),
            f"peer_snapshot_artifact_id:{pillar}",
        )
        peer_snapshot_sha256 = _hash(
            row.get("peer_snapshot_sha256"),
            f"peer_snapshot_sha256:{pillar}",
        )
        peer_as_of = _stamp(row.get("peer_as_of"), f"peer_as_of:{pillar}")
        _require(peer_as_of <= as_of, f"future_peer_snapshot:{pillar}")
        member_count = _integer(row.get("peer_member_count"), f"peer_member_count:{pillar}", 3)
        peer_rank = _integer(row.get("peer_rank"), f"peer_rank:{pillar}", 1)
        _require(peer_rank <= member_count, f"peer_rank:{pillar}")
        peer[pillar] = (member_count - peer_rank) / (member_count - 1)
        _verify_artifact(
            peer_snapshot_artifact_id,
            peer_snapshot_sha256,
            artifact_resolver,
        )
        thin_peer_group_count += int(member_count < 5)

        refs = row.get("evidence_refs")
        _require(isinstance(refs, list) and bool(refs), f"evidence_refs:{pillar}")
        local_ref_ids: set[str] = set()
        for ref in refs:
            _require(isinstance(ref, dict), f"evidence_ref_object:{pillar}")
            artifact_id = _identifier(ref.get("artifact_id"), f"artifact_id:{pillar}")
            _require(artifact_id not in local_ref_ids, f"duplicate_artifact_ref:{pillar}")
            local_ref_ids.add(artifact_id)
            artifact_type = ref.get("artifact_type")
            _require(artifact_type in ARTIFACT_TYPES, f"artifact_type:{pillar}")
            sha = _hash(ref.get("sha256"), f"artifact_sha256:{pillar}")
            available_at = _stamp(ref.get("available_at"), f"artifact_available_at:{pillar}")
            _require(available_at <= as_of, f"future_artifact:{pillar}")
            _require(ref.get("review_status") == "REVIEWED", f"artifact_review_status:{pillar}")
            _verify_artifact(artifact_id, sha, artifact_resolver)
            artifact_ids.add(artifact_id)
            artifact_hashes.add(sha)

    equal_absolute = _mean([absolute[pillar] for pillar in PILLARS])
    equal_peer = _mean([peer[pillar] for pillar in PILLARS])
    mean_confidence = _mean([confidence[pillar] for pillar in PILLARS])
    lens_abs = _lens_scores(absolute)
    lens_peer = _lens_scores(peer)
    abs_values = list(lens_abs.values())
    peer_values = list(lens_peer.values())

    return {
        "schema": RESULT_SCHEMA,
        "status": "COMPLETE_RESEARCH_REVIEW",
        "asset_id": asset_id,
        "issuer_id": issuer_id,
        "country": country,
        "asset_class": asset_class,
        "sector_profile": sector_profile,
        "assessment_anchor_version": ASSESSMENT_ANCHOR_VERSION,
        "as_of": packet["as_of"],
        "reviewed_at": packet["reviewed_at"],
        "equal_absolute_score": equal_absolute,
        "equal_peer_score": equal_peer,
        "evidence_confidence": mean_confidence,
        "pillar_absolute_scores": absolute,
        "pillar_peer_scores": peer,
        "pillar_confidence": confidence,
        "method_lens_absolute_scores": lens_abs,
        "method_lens_peer_scores": lens_peer,
        "method_lens_dispersion_absolute": max(abs_values) - min(abs_values),
        "method_lens_dispersion_peer": max(peer_values) - min(peer_values),
        "method_consensus_above_neutral_absolute": sum(v > 0.5 for v in abs_values) / len(abs_values),
        "method_consensus_above_neutral_peer": sum(v > 0.5 for v in peer_values) / len(peer_values),
        "stage_adjusted_pillar_count": stage_adjusted_count,
        "thin_peer_group_count": thin_peer_group_count,
        "unique_artifact_id_count": len(artifact_ids),
        "unique_artifact_hash_count": len(artifact_hashes),
        "packet_sha256": canonical_sha256(packet),
        "score_method": "UNWEIGHTED_MEAN_TEN_CANONICAL_PILLARS_DIAGNOSTIC_ONLY",
        "method_lenses_are_explanatory_only": True,
        "portfolio_theories_deferred_to_a5": list(PORTFOLIO_THEORIES_A5_ONLY),
        "sector_profiles_change_interpretation_not_weight": True,
        "absolute_and_peer_scores_not_blended": True,
        "peer_relative_method": "RANK_PERCENTILE_FROM_VERIFIED_PEER_SNAPSHOT",
        "historical_pit_certified": False,
        "oos_validated": False,
        "selector_eligible": False,
        "portfolio_weight_effect": 0.0,
        "target_book_write_allowed": False,
        "orders_allowed": False,
        "production_authority": False,
    }


for _lens, _pillars in METHOD_LENSES.items():
    if not _pillars or any(pillar not in PILLARS for pillar in _pillars):
        raise RuntimeError(f"invalid_method_lens:{_lens}")
    if len(set(_pillars)) != len(_pillars):
        raise RuntimeError(f"duplicate_pillar_in_lens:{_lens}")


__all__ = [
    "ACADEMIC_LENSES",
    "ASSESSMENT_ANCHOR_VERSION",
    "ARTIFACT_TYPES",
    "ASSESSMENT_MODES",
    "CLASSIC_LENSES",
    "METHOD_LENSES",
    "MethodologyContractError",
    "PEER_SCOPES",
    "PILLARS",
    "PORTFOLIO_THEORIES_A5_ONLY",
    "PROJECT_LENSES",
    "RESULT_SCHEMA",
    "SCHEMA",
    "SECTOR_PROFILES",
    "canonical_sha256",
    "evaluate_packet",
]
