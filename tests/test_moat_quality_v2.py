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


RAW = {}


def add_raw(source_id: str, payload: str) -> str:
    data = payload.encode()
    digest = hashlib.sha256(data).hexdigest()
    RAW[(source_id, digest)] = data
    return digest


def resolver(source_id: str, expected_sha256: str) -> bytes:
    return RAW[(source_id, expected_sha256)]


def evidence(index: int, direction: str = "SUPPORT", affiliation: str = "CUSTOMER") -> dict:
    source_id = f"SRC{index}-{direction}-{affiliation}"
    return {
        "evidence_id": f"E{index}-{direction}-{affiliation}",
        "source_id": source_id,
        "independence_group": f"GROUP{index}-{affiliation}",
        "subject_asset_id": "KR:005930",
        "subject_issuer_id": "DART:00126380",
        "source_type": "CUSTOMER_DISCLOSURE" if affiliation == "CUSTOMER" else "COMPANY_DISCLOSURE",
        "source_affiliation": affiliation,
        "direction": direction,
        "published_at": "2026-09-10T08:00:00Z",
        "available_at": "2026-09-10T09:00:00Z",
        "verified_at": "2026-09-18T09:00:00Z",
        "valid_through": "2027-03-18T00:00:00Z",
        "raw_sha256": add_raw(source_id, f"raw-{index}-{direction}-{affiliation}"),
        "claim": f"Evidence {index} {direction}",
    }


def packet() -> dict:
    dimensions = {}
    for index, name in enumerate(DIMENSIONS):
        dimensions[name] = {
            "assessment": 0.6 + index * 0.05,
            "confidence": 0.6 + index * 0.04,
            "counter_argument": f"Counter case for {name}",
            "reconciliation": f"Reviewed reconciliation for {name}",
            "invalidation_conditions": [f"Invalidate {name} if evidence reverses"],
            "evidence": [evidence(index)],
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
    def setUp(self):
        RAW.clear()

    def test_complete_packet_has_unweighted_score_and_zero_investment_authority(self):
        value = packet()
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        expected = sum(0.6 + i * 0.05 for i in range(6)) / 6
        self.assertAlmostEqual(result["moat_quality_score"], expected)
        self.assertFalse(result["selector_eligible"])
        self.assertEqual(result["portfolio_weight_effect"], 0.0)
        self.assertFalse(result["orders_allowed"])
        self.assertFalse(result["production_authority"])
        self.assertFalse(result["oos_validated"])
        self.assertFalse(result["historical_pit_certified"])

    def test_cutoff_is_mandatory_and_packet_cannot_self_authorize_future_clock(self):
        value = packet()
        with self.assertRaises(TypeError):
            evaluate_packet(value, raw_resolver=resolver)  # type: ignore[call-arg]
        value["as_of"] = "2099-01-01T00:00:00Z"
        value["reviewed_at"] = "2099-01-02T00:00:00Z"
        with self.assertRaisesRegex(MoatQualityContractError, "packet_time_order"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_evidence_must_bind_to_reviewed_asset_and_issuer(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
        row["subject_issuer_id"] = "DART:OTHER"
        with self.assertRaisesRegex(MoatQualityContractError, "evidence_subject_identity"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_raw_hash_is_verified_against_resolved_bytes(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
        claimed = row["raw_sha256"]
        RAW[(row["source_id"], claimed)] = b"tampered"
        with self.assertRaisesRegex(MoatQualityContractError, "raw_evidence_hash_mismatch"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_stale_or_expired_evidence_is_rejected(self):
        for mutation in ("stale", "expired"):
            value = packet()
            row = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
            if mutation == "stale":
                row["verified_at"] = "2020-01-01T00:00:00Z"
            else:
                row["valid_through"] = "2026-09-17T00:00:00Z"
            with self.subTest(mutation=mutation), self.assertRaises(MoatQualityContractError):
                evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_positive_assessment_requires_independent_support(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]
        old = row["evidence"][0]
        issuer = evidence(90, "SUPPORT", "ISSUER")
        issuer["evidence_id"] = old["evidence_id"]
        issuer["subject_asset_id"] = old["subject_asset_id"]
        issuer["subject_issuer_id"] = old["subject_issuer_id"]
        row["evidence"] = [issuer]
        with self.assertRaisesRegex(MoatQualityContractError, "independent_support_required"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_positive_assessment_cannot_be_backed_only_by_challenge_evidence(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]
        row["evidence"] = [evidence(91, "CHALLENGE", "CUSTOMER")]
        with self.assertRaisesRegex(MoatQualityContractError, "support_required"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_negative_assessment_requires_challenge_evidence(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]
        row["assessment"] = 0.2
        with self.assertRaisesRegex(MoatQualityContractError, "challenge_required"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_neutral_assessment_requires_both_sides(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]
        row["assessment"] = 0.5
        with self.assertRaisesRegex(MoatQualityContractError, "balanced_evidence_required"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        row["evidence"].append(evidence(92, "CHALLENGE", "CUSTOMER"))
        evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_missing_dimension_fails_closed_instead_of_partial_average(self):
        value = packet()
        value["dimensions"].pop(DIMENSIONS[-1])
        with self.assertRaisesRegex(MoatQualityContractError, "dimension_set"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_genuine_zero_is_preserved_with_challenge_evidence(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]
        row["assessment"] = 0.0
        row["evidence"] = [evidence(93, "CHALLENGE", "CUSTOMER")]
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertEqual(result["dimension_scores"][DIMENSIONS[0]], 0.0)

    def test_boolean_nonfinite_or_out_of_range_score_is_rejected(self):
        for bad in [True, float("nan"), float("inf"), -0.1, 1.1, "0.5"]:
            value = packet()
            value["dimensions"][DIMENSIONS[0]]["assessment"] = bad
            with self.subTest(bad=bad), self.assertRaises(MoatQualityContractError):
                evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_future_evidence_is_rejected_at_as_of_boundary(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
        row["verified_at"] = "2026-09-18T21:00:00Z"
        with self.assertRaisesRegex(MoatQualityContractError, "future_evidence"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_publication_must_precede_availability(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
        row["published_at"] = "2026-09-10T10:00:00Z"
        row["available_at"] = "2026-09-10T09:00:00Z"
        with self.assertRaisesRegex(MoatQualityContractError, "publication_after_availability"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_duplicate_evidence_id_is_rejected_across_dimensions(self):
        value = packet()
        value["dimensions"][DIMENSIONS[1]]["evidence"][0]["evidence_id"] = \
            value["dimensions"][DIMENSIONS[0]]["evidence_id"]
        with self.assertRaisesRegex(MoatQualityContractError, "duplicate_evidence_id"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_source_type_cannot_spoof_independent_affiliation(self):
        value = packet()
        row = value["dimensions"][DIMENSIONS[0]]["evidence"][0]
        row["source_type"] = "COMPANY_DISCLOSURE"
        row["source_affiliation"] = "CUSTOMER"
        with self.assertRaisesRegex(MoatQualityContractError, "evidence_source_affiliation_mismatch"):
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_counter_reconciliation_and_invalidation_are_mandatory(self):
        for field, bad in [
            ("counter_argument", ""),
            ("reconciliation", ""),
            ("invalidation_conditions", []),
        ]:
            value = packet()
            value["dimensions"][DIMENSIONS[0]][field] = bad
            with self.subTest(field=field), self.assertRaises(MoatQualityContractError):
                evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)

    def test_confidence_is_reported_separately_not_hidden_weight(self):
        value = packet()
        first = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)["moat_quality_score"]
        for name in DIMENSIONS:
            value["dimensions"][name]["confidence"] = 0.01
        result = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)
        self.assertAlmostEqual(result["moat_quality_score"], first)
        self.assertAlmostEqual(result["evidence_confidence"], 0.01)

    def test_packet_digest_is_deterministic_and_changes_with_evidence(self):
        value = packet()
        first = evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)["packet_sha256"]
        second = evaluate_packet(copy.deepcopy(value), "2026-09-19T02:00:00Z", resolver)["packet_sha256"]
        self.assertEqual(first, second)
        value["dimensions"][DIMENSIONS[0]]["evidence"][0]["claim"] += " changed"
        self.assertNotEqual(
            first,
            evaluate_packet(value, "2026-09-19T02:00:00Z", resolver)["packet_sha256"],
        )

    def test_contract_document_matches_code_and_safety(self):
        contract = json.loads((ROOT / "docs" / "moat_quality_v2_contract.json").read_text())
        self.assertEqual(contract["dimensions"], list(DIMENSIONS))
        self.assertEqual(contract["revision"], 3)
        self.assertTrue(contract["evidence_requirements"]["trusted_cutoff_required_from_caller"])
        self.assertTrue(contract["evidence_requirements"]["raw_bytes_must_resolve_and_match_sha256"])
        self.assertFalse(contract["safety"]["selector_weight_change_allowed"])
        self.assertEqual(contract["safety"]["portfolio_weight_effect"], 0.0)
        self.assertFalse(contract["safety"]["orders_allowed"])



if __name__ == "__main__":
    unittest.main()
