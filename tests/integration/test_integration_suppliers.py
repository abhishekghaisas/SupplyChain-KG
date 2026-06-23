"""
Integration tests — Suppliers router.

Requires: Neo4j running locally, seed_data fixture loaded.
"""

import pytest

pytestmark = pytest.mark.db

HEADERS = {"X-API-Key": "dev-api-key"}


# ── List suppliers ────────────────────────────────────────────────────────────

class TestListSuppliers:
    def test_returns_200(self, test_client, seed_data):
        r = test_client.get("/suppliers", headers=HEADERS)
        assert r.status_code == 200

    def test_seed_suppliers_present(self, test_client, seed_data):
        r = test_client.get("/suppliers", headers=HEADERS)
        ids = [s["id"] for s in r.json()]
        assert "TEST-SUP-001" in ids
        assert "TEST-SUP-002" in ids

    def test_response_fields(self, test_client, seed_data):
        r = test_client.get("/suppliers", headers=HEADERS)
        sup = next(s for s in r.json() if s["id"] == "TEST-SUP-001")
        assert sup["name"] == "Precision Motors Inc"
        assert sup["location"] == "Germany"
        assert sup["status"] == "ACTIVE"
        assert "ISO9001" in sup["certifications"]

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.get("/suppliers")
        assert r.status_code == 401


# ── Get supplier ──────────────────────────────────────────────────────────────

class TestGetSupplier:
    def test_returns_correct_supplier(self, test_client, seed_data):
        r = test_client.get("/suppliers/TEST-SUP-001", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "TEST-SUP-001"
        assert body["name"] == "Precision Motors Inc"

    def test_404_for_missing_supplier(self, test_client, seed_data):
        r = test_client.get("/suppliers/TEST-SUP-NONEXISTENT", headers=HEADERS)
        assert r.status_code == 404

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.get("/suppliers/TEST-SUP-001")
        assert r.status_code == 401


# ── Create supplier ───────────────────────────────────────────────────────────

class TestCreateSupplier:
    def test_create_returns_201(self, test_client, seed_data):
        r = test_client.post("/suppliers", headers=HEADERS, json={
            "id":             "TEST-SUP-CREATE-001",
            "name":           "New Test Supplier",
            "location":       "USA",
            "certifications": ["ISO9001"],
            "status":         "ACTIVE",
            "tier":           2,
            "rating":         4.0,
        })
        assert r.status_code == 201

    def test_create_returns_supplier(self, test_client, seed_data):
        r = test_client.post("/suppliers", headers=HEADERS, json={
            "id":             "TEST-SUP-CREATE-002",
            "name":           "Test Supplier 2",
            "location":       "Japan",
            "certifications": ["ISO9001", "ISO14001"],
            "status":         "ACTIVE",
            "tier":           1,
            "rating":         4.8,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == "TEST-SUP-CREATE-002"
        assert body["name"] == "Test Supplier 2"
        assert body["location"] == "Japan"

    def test_missing_required_field_returns_422(self, test_client, seed_data):
        r = test_client.post("/suppliers", headers=HEADERS, json={
            "name": "No ID Supplier",
            "location": "USA",
        })
        assert r.status_code == 422

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.post("/suppliers", json={
            "id": "TEST-SUP-NOAUTH", "name": "x", "location": "x",
        })
        assert r.status_code == 401


# ── Disruption assessment ─────────────────────────────────────────────────────

class TestDisruptionAssessment:
    def test_returns_affected_parts(self, test_client, seed_data):
        r = test_client.get("/suppliers/TEST-SUP-001/disruption", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["supplier_id"] == "TEST-SUP-001"
        assert body["affected_parts_count"] >= 2   # supplies TEST-P-001 and TEST-P-002
        part_ids = [p["part_id"] for p in body["affected_parts"]]
        assert "TEST-P-001" in part_ids
        assert "TEST-P-002" in part_ids

    def test_critical_parts_separated(self, test_client, seed_data):
        r = test_client.get("/suppliers/TEST-SUP-001/disruption", headers=HEADERS)
        body = r.json()
        # TEST-P-001 is HIGH criticality — should appear in critical_parts
        critical_ids = [p["part_id"] for p in body["critical_parts"]]
        assert "TEST-P-001" in critical_ids

    def test_supplier_with_no_parts(self, test_client, db_client, seed_data):
        # Create a supplier with no SUPPLIES relationships
        db_client.create_supplier(
            supplier_id="TEST-SUP-NOPARTS",
            name="Inactive Test Supplier",
            location="USA",
            certifications=[],
            status="ACTIVE",
        )
        r = test_client.get("/suppliers/TEST-SUP-NOPARTS/disruption", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["affected_parts_count"] == 0

    def test_404_for_missing_supplier(self, test_client, seed_data):
        r = test_client.get("/suppliers/TEST-SUP-NONEXISTENT/disruption", headers=HEADERS)
        assert r.status_code == 404