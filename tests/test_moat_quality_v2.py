from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.moat_quality_v2 import DIMENSIONS, MoatQualityContractError, evaluate_packet


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def packet() -> dict:
    dimensions = {}
    for index, name in enumerate(DIMENSIONS):
        dimensions[name] = {
            "assessment": 0.5 + index * 0.05,
            "confidence": 0.6 + index * 0.04,
            "counter_argument": f"Counter case for {name}",
            "invalidation_conditions": [f"Invalidate {name} if evidence reverses"],
            "evidence": [
                {
                    "evidence_id": f"E{index}",
                    "source_id": f"SRC{index}",
                    "independence_group": f"GROUP{index}",
                    "source_type": "REGULATORY_FILING" if index % 2 == 0 else "CUSTOMER_DISCLOSURE",
                    "direction": "CHALLENGE" if index == 5 else "SUPPORT",
                    "published_at": "2026-09-18T08:00:00Z",
                    "available_at": "2026-09-18T09:00:00Z",
                    "raw_sha256": sha(f"raw-{index}"),
                    "claim": f"Primary evidence for {name}",
                }
            ],
        }
    return {
        "schema": "moat-quality-v2",
        "asset_id": "KR:005930",
        "issuer_id": "DART:00126380",
        "as_of": "2026-09-18T20:00:00Z",
        "reviewed_at": "2026-09-19T01:00:00Z",
        "review_status": "RESEARCH_REVIEWED",
        "dimensions": dimensions,
    }


class MoatQualityV2Smoke(unittest.TestCase):
    def test_complete_packet_has_unweighted_score_and_zero_investment_authority(self):
        result = evaluate_packet(packet(), "2026-09-19T02:00:00Z")
        expected = sum(0.5 + i * 0.05 for i in range(6)) / 6
        self.assertAlmostEqual(result["moat_quality_score"], expected)
        self.assertEqual(result["score_method"], "UNWEIGHTED_MEAN_SIX_REQUIRED_DIMENSIONS")
        self.assertFalse(result["selector_eligible"])
        self.assertEqual(result["portfolio_weight_effect"], 0.0)
        self.assertFalse(result["orders_allowed"])
        self.assertFalse(result["production_authority"])
        self.assertFalse(result["oos_validated"])
        self.assertFalse(result["historical_pit_certified"])

    def test_missing_dimension_fails_closed_instead_of_averaging_remaining_scores(self):
        value = packet()
        value["dimensions"].pop(DIMENSIONS[-1])
        with self.assertRaisesRegex(MoatQualityContractError, "dimension_set"):
            evaluate_packet(value)

    def test_genuine_zero_is_preserved(self):
        value = packet()
        value["dimensions"][DIMENSIONS[0]]["assessment"] = 0.0
        result = evaluate_packet(value)
        self.assertEqual(result["dimension_scores"][DIMENSIONS[0]], 0.0)

    def test_boolean_or_nonfinite_score_is_rejected(self):
        for bad in [True, float("nan"), float("inf"), -0.1, 1.1, "0.5"]:
            value = packet()
            value["dimensions"][DIMENSIONS[0]]["assessment"] = bad
            with self.subTest(bad=bad), self.assertRaises(MoatQualityContractError):
                evaluate_packet(value)

    def test_evidence_after_review_or_cutoff_is_rejected(self):
        value = packet()
        value["dimensions"][DIMENSIONS[0]]["evidence"][0]["available_at"] = "2026-09-19T02:00:01Z"
        with self.assertRaisesRegex(MoatQualityContractError, "future_evidence"):
            evaluate_packet(value, "2026-09-19T02:00:00Z")

    def test_evidence_after_as_of_but_before_review_is_rejected(self):
        value = packet()
        value["dimensions"][DIMENSIONS[0]]["evidence"][0]["available_at"] = "2026-09-18T21:00:00Z"
        with self.assertRaisesRegex(MoatQualityContractError, "future_evidence"):
            evaluate_packet(value, "2026-09-19T02:00:00Z")

    def test_publication_must_precede_availability(self):
        value = packet()
        evidence = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
        evidence["published_at"] = "2026-09-18T10:00:00Z"
        evidence["available_at"] = "2026-09-18T09:00:00Z"
        with self.assertRaisesRegex(MoatQualityContractError, "evidence_publication_after_availability"):
            evaluate_packet(value)

    def test_duplicate_evidence_identity_is_rejected_across_dimensions(self):
        value = packet()
        value["dimensions"][DIMENSIONS[1]]["evidence"][0]["evidence_id"] = "E0"
        with self.assertRaisesRegex(MoatQualityContractError, "duplicate_evidence_id"):
            evaluate_packet(value)

    def test_bad_hash_source_type_or_direction_is_rejected(self):
        mutations = [
            ("raw_sha256", "bad"),
            ("source_type", "BLOG_OPINION"),
            ("direction", "BULLISH"),
        ]
        for field, bad in mutations:
            value = packet()
            value["dimensions"][DIMENSIONS[2]]["evidence"][0][field] = bad
            with self.subTest(field=field), self.assertRaises(MoatQualityContractError):
                evaluate_packet(value)

    def test_counter_argument_and_invalidation_condition_are_mandatory(self):
        for mutation in ["counter", "invalidations"]:
            value = packet()
            row = value["dimensions"][DIMENSIONS[3]]
            if mutation == "counter":
                row["counter_argument"] = ""
            else:
                row["invalidation_conditions"] = []
            with self.subTest(mutation=mutation), self.assertRaises(MoatQualityContractError):
                evaluate_packet(value)

    def test_confidence_is_reported_separately_not_used_as_hidden_weight(self):
        value = packet()
        first_score = evaluate_packet(value)["moat_quality_score"]
        for name in DIMENSIONS:
            value["dimensions"][name]["confidence"] = 0.01
        result = evaluate_packet(value)
        self.assertAlmostEqual(result["moat_quality_score"], first_score)
        self.assertAlmostEqual(result["evidence_confidence"], 0.01)

    def test_packet_digest_is_deterministic_and_changes_with_evidence(self):
        value = packet()
        first = evaluate_packet(value)["packet_sha256"]
        second = evaluate_packet(copy.deepcopy(value))["packet_sha256"]
        self.assertEqual(first, second)
        value["dimensions"][DIMENSIONS[0]]["evidence"][0]["claim"] += " changed"
        self.assertNotEqual(first, evaluate_packet(value)["packet_sha256"])

    def test_contract_document_matches_code_dimensions_and_safety(self):
        contract = json.loads((ROOT / "docs/moat_quality_v2_contract.json").read_text())
        self.assertEqual(contract["dimensions"], list(DIMENSIONS))
        self.assertEqual(contract["score_method"], "UNWEIGHTED_MEAN_SIX_REQUIRED_DIMENSIONS")
        self.assertTrue(contract["safety"]["research_only"])
        self.assertFalse(contract["safety"]["selector_weight_change_allowed"])
        self.assertFalse(contract["safety"]["target_book_write_allowed"])
        self.assertFalse(contract["safety"]["orders_allowed"])
        self.assertTrue(contract["evidence_requirements"]["available_at_must_not_exceed_as_of"])


if __name__ == "__main__":
    unittest.main()
