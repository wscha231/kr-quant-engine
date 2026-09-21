from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.investment_methodology_v1 import (
    ACADEMIC_LENSES,
    ASSESSMENT_ANCHOR_VERSION,
    CLASSIC_LENSES,
    METHOD_LENSES,
    PILLARS,
    PORTFOLIO_THEORIES_A5_ONLY,
    PROJECT_LENSES,
    MethodologyContractError,
    evaluate_packet,
)


RAW: dict[tuple[str, str], bytes] = {}


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def add_raw(artifact_id: str, label: str) -> str:
    raw = label.encode()
    digest = hashlib.sha256(raw).hexdigest()
    RAW[(artifact_id, digest)] = raw
    return digest


def resolver(artifact_id: str, expected_sha256: str) -> bytes:
    return RAW[(artifact_id, expected_sha256)]


def packet() -> dict:
    pillars = {}
    for i, name in enumerate(PILLARS):
        peer_artifact_id = f"PEER:{i}"
        evidence_artifact_id = f"ART:{i}"
        pillars[name] = {
            "absolute_assessment": 0.40 + 0.05 * i,
            "confidence": 0.60 + 0.03 * i,
            "assessment_mode": "STANDARD",
            "absolute_rationale": f"Absolute rationale for {name}",
            "peer_rationale": f"Peer rationale for {name}",
            "counter_argument": f"Counter argument for {name}",
            "invalidation_conditions": [f"Invalidate {name} if core evidence reverses"],
            "peer_group_id": f"GLOBAL:{i}",
            "peer_scope": "GLOBAL_INDUSTRY",
            "peer_snapshot_artifact_id": peer_artifact_id,
            "peer_snapshot_sha256": add_raw(peer_artifact_id, f"peer-{i}"),
            "peer_as_of": "2026-09-18T20:00:00Z",
            "peer_member_count": 12,
            "peer_rank": i + 1,
            "evidence_refs": [
                {
                    "artifact_id": evidence_artifact_id,
                    "artifact_type": "QUANT_FEATURE_PACKET" if i % 2 == 0 else "PRIMARY_RESEARCH_PACKET",
                    "sha256": add_raw(evidence_artifact_id, f"artifact-{i}"),
                    "available_at": "2026-09-18T19:00:00Z",
                    "review_status": "REVIEWED",
                }
            ],
        }
    return {
        "schema": "investment-methodology-v1",
        "assessment_anchor_version": ASSESSMENT_ANCHOR_VERSION,
        "asset_id": "KR:005930",
        "issuer_id": "DART:00126380",
        "country": "KR",
        "asset_class": "EQUITY",
        "sector_profile": "SEMICONDUCTOR_HARDWARE",
        "as_of": "2026-09-18T21:00:00Z",
        "reviewed_at": "2026-09-19T01:00:00Z",
        "review_status": "RESEARCH_REVIEWED",
        "pillars": pillars,
    }


class InvestmentMethodologyV1Smoke(unittest.TestCase):
    def setUp(self):
        RAW.clear()

    def test_complete_packet_emits_equal_pillar_scores_and_zero_authority(self):
        value = packet()
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertAlmostEqual(
            result["equal_absolute_score"],
            sum(0.40 + 0.05 * i for i in range(10)) / 10,
        )
        self.assertAlmostEqual(
            result["equal_peer_score"],
            sum((12 - (i + 1)) / 11 for i in range(10)) / 10,
        )
        self.assertEqual(
            result["peer_relative_method"],
            "RANK_PERCENTILE_FROM_VERIFIED_PEER_SNAPSHOT",
        )
        self.assertFalse(result["selector_eligible"])
        self.assertEqual(result["portfolio_weight_effect"], 0.0)
        self.assertFalse(result["orders_allowed"])
        self.assertTrue(result["method_lenses_are_explanatory_only"])
        self.assertIn("FRACTIONAL_KELLY", result["portfolio_theories_deferred_to_a5"])
        self.assertTrue(result["absolute_and_peer_scores_not_blended"])

    def test_missing_pillar_fails_closed(self):
        value = packet()
        value["pillars"].pop(PILLARS[-1])
        with self.assertRaisesRegex(MethodologyContractError, "pillar_set"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_future_artifact_or_peer_snapshot_fails_closed(self):
        for field in ("artifact", "peer"):
            value = packet()
            row = value["pillars"][PILLARS[0]]
            if field == "artifact":
                row["evidence_refs"][0]["available_at"] = "2026-09-18T22:00:00Z"
                pattern = "future_artifact"
            else:
                row["peer_as_of"] = "2026-09-18T22:00:00Z"
                pattern = "future_peer_snapshot"
            with self.subTest(field=field), self.assertRaisesRegex(MethodologyContractError, pattern):
                evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_peer_group_requires_minimum_three_members_and_flags_thin_groups(self):
        value = packet()
        value["pillars"][PILLARS[0]]["peer_member_count"] = 2
        with self.assertRaisesRegex(MethodologyContractError, "peer_member_count"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        value["pillars"][PILLARS[0]]["peer_member_count"] = 3
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertEqual(result["thin_peer_group_count"], 1)

    def test_stage_adjustment_requires_explicit_reason(self):
        value = packet()
        row = value["pillars"][PILLARS[3]]
        row["assessment_mode"] = "STAGE_ADJUSTED"
        with self.assertRaisesRegex(MethodologyContractError, "stage_adjustment_reason"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        row["stage_adjustment_reason"] = "Pre-commercial biotech uses cash runway and probability-adjusted economics."
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertEqual(result["stage_adjusted_pillar_count"], 1)

    def test_sector_profile_changes_interpretation_not_equal_weights(self):
        first = packet()
        first_result = evaluate_packet(first, "2026-09-19T02:00:00Z", resolver)
        second = copy.deepcopy(first)
        second["sector_profile"] = "BIOTECH_PRECOMMERCIAL"
        second_result = evaluate_packet(second, "2026-09-19T02:00:00Z", resolver)
        self.assertEqual(first_result["equal_absolute_score"], second_result["equal_absolute_score"])
        self.assertEqual(first_result["equal_peer_score"], second_result["equal_peer_score"])
        self.assertTrue(second_result["sector_profiles_change_interpretation_not_weight"])

    def test_confidence_is_reported_separately_not_hidden_weight(self):
        value = packet()
        baseline = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)["equal_absolute_score"]
        for name in PILLARS:
            value["pillars"][name]["confidence"] = 0.01
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertEqual(result["equal_absolute_score"], baseline)
        self.assertAlmostEqual(result["evidence_confidence"], 0.01)

    def test_absolute_and_peer_assessment_are_not_blended(self):
        value = packet()
        for name in PILLARS:
            value["pillars"][name]["absolute_assessment"] = 0.9
            value["pillars"][name]["peer_member_count"] = 11
            value["pillars"][name]["peer_rank"] = 9
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertAlmostEqual(result["equal_absolute_score"], 0.9)
        self.assertAlmostEqual(result["equal_peer_score"], 0.2)

    def test_method_lenses_reuse_canonical_pillars_without_double_counting(self):
        value = packet()
        before = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        changed_pillar = "valuation_margin_of_safety"
        value["pillars"][changed_pillar]["absolute_assessment"] = 0.0
        after = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        touched = {
            lens for lens, pillars in METHOD_LENSES.items() if changed_pillar in pillars
        }
        untouched = set(METHOD_LENSES) - touched
        for lens in touched:
            self.assertNotEqual(
                before["method_lens_absolute_scores"][lens],
                after["method_lens_absolute_scores"][lens],
            )
        for lens in untouched:
            self.assertEqual(
                before["method_lens_absolute_scores"][lens],
                after["method_lens_absolute_scores"][lens],
            )

    def test_named_method_registry_has_classic_and_project_lenses(self):
        self.assertGreaterEqual(len(CLASSIC_LENSES), 12)
        self.assertGreaterEqual(len(PROJECT_LENSES), 6)
        self.assertGreaterEqual(len(ACADEMIC_LENSES), 5)
        self.assertIn("BUFFETT_MUNGER_COMPOUNDER", METHOD_LENSES)
        self.assertIn("ONEIL_CANSLIM", METHOD_LENSES)
        self.assertIn("MINERVINI_SUPERPERFORMANCE", METHOD_LENSES)
        self.assertIn("PROJECT_MARKET_LEADER", METHOD_LENSES)
        self.assertIn("FAMA_FRENCH_VALUE_PROFITABILITY_INVESTMENT", METHOD_LENSES)
        self.assertIn("JEGADEESH_TITMAN_MOMENTUM", METHOD_LENSES)
        self.assertIn("MARKOWITZ_MEAN_VARIANCE", PORTFOLIO_THEORIES_A5_ONLY)
        for lens, pillars in METHOD_LENSES.items():
            self.assertEqual(len(pillars), len(set(pillars)), lens)
            self.assertTrue(set(pillars).issubset(PILLARS), lens)

    def test_duplicate_artifact_ref_within_pillar_is_rejected(self):
        value = packet()
        row = value["pillars"][PILLARS[0]]
        row["evidence_refs"].append(copy.deepcopy(row["evidence_refs"][0]))
        with self.assertRaisesRegex(MethodologyContractError, "duplicate_artifact_ref"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_packet_time_order_is_fail_closed(self):
        value = packet()
        value["reviewed_at"] = "2026-09-20T00:00:00Z"
        with self.assertRaisesRegex(MethodologyContractError, "packet_time_order"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_peer_rank_is_computed_not_manually_supplied(self):
        value = packet()
        row = value["pillars"][PILLARS[0]]
        row["peer_member_count"] = 5
        row["peer_rank"] = 2
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertAlmostEqual(
            result["pillar_peer_scores"][PILLARS[0]],
            0.75,
        )
        row["peer_rank"] = 6
        with self.assertRaisesRegex(MethodologyContractError, "peer_rank"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_non_global_peer_scope_requires_exception_reason(self):
        value = packet()
        row = value["pillars"][PILLARS[0]]
        row["peer_scope"] = "LIFECYCLE_STAGE"
        with self.assertRaisesRegex(MethodologyContractError, "peer_scope_exception_reason"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        row["peer_scope_exception_reason"] = "Pre-commercial lifecycle economics are not comparable with profitable peers."
        evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_peer_snapshot_and_evidence_bytes_must_match_declared_hash(self):
        for target in ("peer", "evidence"):
            value = packet()
            row = value["pillars"][PILLARS[0]]
            if target == "peer":
                artifact_id = row["peer_snapshot_artifact_id"]
                digest = row["peer_snapshot_sha256"]
            else:
                artifact_id = row["evidence_refs"][0]["artifact_id"]
                digest = row["evidence_refs"][0]["sha256"]
            RAW[(artifact_id, digest)] = b"tampered"
            with self.subTest(target=target), self.assertRaisesRegex(
                MethodologyContractError,
                "artifact_hash_mismatch",
            ):
                evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_assessment_anchor_version_is_mandatory(self):
        value = packet()
        value["assessment_anchor_version"] = "other"
        with self.assertRaisesRegex(MethodologyContractError, "assessment_anchor_version"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_packet_digest_is_deterministic(self):
        value = packet()
        first = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)["packet_sha256"]
        second = evaluate_packet(copy.deepcopy(value), "2026-09-19T02:00:00Z", resolver)["packet_sha256"]
        self.assertEqual(first, second)
        value["pillars"][PILLARS[0]]["absolute_rationale"] += " changed"
        self.assertNotEqual(
            first,
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)["packet_sha256"],
        )

    def test_contract_document_preserves_equal_weight_research_only_policy(self):
        contract = json.loads((ROOT / "docs" / "investment_methodology_v1_contract.json").read_text())
        self.assertEqual(contract["pillars"], list(PILLARS))
        self.assertEqual(contract["assessment_anchor_version"], ASSESSMENT_ANCHOR_VERSION)
        self.assertTrue(contract["fairness"]["universal_pillars_required_for_every_asset"])
        self.assertTrue(contract["fairness"]["sector_profiles_change_interpretation_not_weight"])
        self.assertFalse(contract["fairness"]["missing_pillars_imputation_allowed"])
        self.assertTrue(contract["fairness"]["peer_relative_score_derived_from_rank"])
        self.assertTrue(contract["evidence"]["artifact_bytes_must_resolve_and_match_sha256"])
        self.assertTrue(contract["safety"]["research_only"])
        self.assertFalse(contract["safety"]["selector_eligible"])
        self.assertEqual(contract["safety"]["portfolio_weight_effect"], 0.0)


if __name__ == "__main__":
    unittest.main()
