"""
Integration tests — Reasoning router.

Requires: Neo4j running locally, seed_data fixture loaded.
"""

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.db

HEADERS = {"X-API-Key": "dev-api-key"}


# ── Compatibility check ───────────────────────────────────────────────────────

class TestCompatibilityCheck:
    def test_compatible_parts_pass(self, test_client, seed_data):
        """TEST-P-001 and TEST-P-002 are VERIFIED compatible."""
        r = test_client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id":   "TEST-P-001",
            "substitute_part_id": "TEST-P-002",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["original_part_id"] == "TEST-P-001"
        assert body["substitute_part_id"] == "TEST-P-002"

    def test_response_structure(self, test_client, seed_data):
        r = test_client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id":   "TEST-P-001",
            "substitute_part_id": "TEST-P-002",
        })
        body = r.json()
        result = body["result"]
        assert "passed" in result
        assert "rule_name" in result
        assert "reason" in result
        assert "confidence" in result
        assert "failure_severity" in result

    def test_provenance_included(self, test_client, seed_data):
        r = test_client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id":   "TEST-P-001",
            "substitute_part_id": "TEST-P-002",
        })
        provenance = r.json()["provenance"]
        assert provenance is not None
        assert provenance["total_entries"] >= 1

    def test_incompatible_categories_fail(self, test_client, seed_data):
        """TEST-P-001 (electronic) vs TEST-P-003 (mechanical) — category mismatch."""
        r = test_client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id":   "TEST-P-001",
            "substitute_part_id": "TEST-P-003",
        })
        assert r.status_code == 200
        assert r.json()["result"]["passed"] is False

    def test_missing_original_part_returns_404(self, test_client, seed_data):
        r = test_client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id":   "TEST-P-NONEXISTENT",
            "substitute_part_id": "TEST-P-002",
        })
        assert r.status_code == 404

    def test_missing_substitute_part_returns_404(self, test_client, seed_data):
        r = test_client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id":   "TEST-P-001",
            "substitute_part_id": "TEST-P-NONEXISTENT",
        })
        assert r.status_code == 404

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.post("/reasoning/compatibility", json={
            "original_part_id": "TEST-P-001",
            "substitute_part_id": "TEST-P-002",
        })
        assert r.status_code == 401


# ── Lead time check ───────────────────────────────────────────────────────────

class TestLeadTimeCheck:
    def test_feasible_delivery(self, test_client, seed_data):
        required = str(date.today() + timedelta(days=30))
        r = test_client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 21,
            "required_date":           required,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["feasible"] is True
        assert body["result"]["passed"] is True

    def test_infeasible_delivery(self, test_client, seed_data):
        required = str(date.today() + timedelta(days=10))
        r = test_client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 21,
            "required_date":           required,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["feasible"] is False
        assert body["result"]["passed"] is False

    def test_result_contains_reason(self, test_client, seed_data):
        required = str(date.today() + timedelta(days=30))
        r = test_client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 21,
            "required_date":           required,
        })
        assert r.json()["result"]["reason"]

    def test_custom_order_date(self, test_client, seed_data):
        """Supplying order_date in the past makes even generous lead times tight."""
        past_order = str(date.today() - timedelta(days=5))
        required   = str(date.today() + timedelta(days=10))
        r = test_client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 21,
            "required_date":           required,
            "order_date":              past_order,
        })
        assert r.status_code == 200

    def test_invalid_lead_time_returns_422(self, test_client, seed_data):
        r = test_client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 0,   # ge=1 constraint
            "required_date": str(date.today() + timedelta(days=30)),
        })
        assert r.status_code == 422

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.post("/reasoning/lead-time", json={
            "supplier_lead_time_days": 21,
            "required_date": str(date.today() + timedelta(days=30)),
        })
        assert r.status_code == 401


# ── Supplier qualification ────────────────────────────────────────────────────

class TestSupplierQualification:
    def test_qualified_supplier_passes(self, test_client, seed_data):
        """TEST-SUP-001 has ISO9001, IATF16949, rating 4.5 — should qualify easily."""
        r = test_client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id":             "TEST-SUP-001",
            "required_certifications": ["ISO9001"],
            "min_rating":              4.0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["qualified"] is True
        assert body["supplier_id"] == "TEST-SUP-001"

    def test_missing_certification_fails(self, test_client, seed_data):
        """TEST-SUP-002 doesn't have IATF16949."""
        r = test_client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id":             "TEST-SUP-002",
            "required_certifications": ["IATF16949"],
            "min_rating":              3.0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["qualified"] is False
        assert "IATF16949" in body["result"]["reason"]

    def test_low_rating_fails(self, test_client, seed_data):
        """TEST-SUP-002 has rating 4.2 — should fail if min_rating is 4.5."""
        r = test_client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "TEST-SUP-002",
            "min_rating":  4.5,
        })
        assert r.status_code == 200
        assert r.json()["qualified"] is False

    def test_result_structure(self, test_client, seed_data):
        r = test_client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "TEST-SUP-001",
        })
        result = r.json()["result"]
        assert "passed" in result
        assert "confidence" in result
        assert "facts_used" in result

    def test_missing_supplier_returns_404(self, test_client, seed_data):
        r = test_client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "TEST-SUP-NONEXISTENT",
        })
        assert r.status_code == 404

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.post("/reasoning/qualify-supplier", json={
            "supplier_id": "TEST-SUP-001",
        })
        assert r.status_code == 401