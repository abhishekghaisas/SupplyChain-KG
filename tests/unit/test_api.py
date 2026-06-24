"""
API test suite for the Supply Chain Knowledge Graph.

Tests are split into two groups:
  - No-DB tests  : run anywhere, no Neo4j required (health, auth, lead-time)
  - DB tests      : require a running Neo4j with sample data loaded
                    (pytest -m db  or  pytest -m "not db" to skip)

Run all:        pytest tests/test_api.py -v
Skip DB tests:  pytest tests/test_api.py -v -m "not db"
Only DB tests:  pytest tests/test_api.py -v -m db
"""

import pytest
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)
from jose import jwt as _jwt
import os
_JWT_SECRET = os.environ.get("JWT_SECRET_KEY", "ci-test-jwt-secret-not-for-production")
_TEST_TOKEN = _jwt.encode(
    {"sub": "ci-client", "type": "access"},
    _JWT_SECRET,
    algorithm="HS256",
)
HEADERS = {"Authorization": f"Bearer {_TEST_TOKEN}"}


# ─── Health ───────────────────────────────────────────────────────────────────

def test_health_returns_200():
    r = client.get("/health")
    assert r.status_code == 200

def test_health_schema():
    r = client.get("/health")
    body = r.json()
    assert "status" in body
    assert "version" in body
    assert "database" in body
    assert body["status"] == "ok"


# ─── Auth ─────────────────────────────────────────────────────────────────────

def test_missing_api_key_returns_401():
    r = client.get("/parts")
    assert r.status_code == 401

def test_wrong_api_key_returns_401():
    r = client.get("/parts", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401

def test_valid_api_key_accepted():
    # /reasoning/lead-time needs no DB, so safe to call here
    r = client.post("/reasoning/lead-time", headers=HEADERS, json={
        "supplier_lead_time_days": 10,
        "required_date": "2026-08-01",
    })
    assert r.status_code == 200


# ─── Lead-time (no DB) ────────────────────────────────────────────────────────

class TestLeadTime:
    def test_feasible(self):
        r = client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 21,
            "required_date": "2026-07-25",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["feasible"] is True
        assert body["result"]["passed"] is True
        assert body["result"]["details"]["buffer_days"] > 0

    def test_infeasible(self):
        r = client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 60,
            "required_date": "2026-06-25",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["feasible"] is False
        assert body["result"]["passed"] is False
        assert "days_late" in body["result"]["details"]

    def test_exact_boundary(self):
        """Ordering today for delivery exactly on the required date — should pass."""
        from datetime import date, timedelta
        exact = (date.today() + timedelta(days=14)).isoformat()
        r = client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 14,
            "required_date": exact,
        })
        assert r.status_code == 200
        assert r.json()["feasible"] is True

    def test_explicit_order_date(self):
        r = client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 10,
            "required_date": "2026-07-10",
            "order_date": "2026-06-25",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["result"]["facts_used"] == [
            "lead_time:10",
            "required_date:2026-07-10",
            "order_date:2026-06-25",
        ]

    def test_invalid_payload_returns_422(self):
        r = client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": -5,   # violates ge=1
            "required_date": "2026-07-01",
        })
        assert r.status_code == 422

    def test_missing_required_date_returns_422(self):
        r = client.post("/reasoning/lead-time", headers=HEADERS, json={
            "supplier_lead_time_days": 10,
        })
        assert r.status_code == 422


# ─── Parts (DB) ───────────────────────────────────────────────────────────────

@pytest.mark.db
class TestParts:
    def test_list_parts(self, db_client):
        r = client.get("/parts", headers=HEADERS)
        assert r.status_code == 200
        parts = r.json()
        assert len(parts) >= 5
        ids = {p["id"] for p in parts}
        assert {"P-12345", "P-67890", "P-11111", "P-22222", "P-33333"}.issubset(ids)

    def test_list_parts_filter_category(self, db_client):
        r = client.get("/parts?category=electronic", headers=HEADERS)
        assert r.status_code == 200
        parts = r.json()
        assert all(p["category"] == "electronic" for p in parts)
        assert len(parts) >= 3

    def test_list_parts_filter_criticality(self, db_client):
        r = client.get("/parts?criticality=HIGH", headers=HEADERS)
        assert r.status_code == 200
        parts = r.json()
        assert all(p["criticality"] == "HIGH" for p in parts)

    def test_get_part_by_id(self, db_client):
        r = client.get("/parts/P-12345", headers=HEADERS)
        assert r.status_code == 200
        part = r.json()
        assert part["id"] == "P-12345"
        assert part["name"] == "Servo Motor SM-400"
        assert part["unit_of_measure"] == "EA"
        assert isinstance(part["specifications"], dict)
        assert part["specifications"]["power_rating"] == "400W"

    def test_get_part_not_found(self, db_client):
        r = client.get("/parts/P-DOESNOTEXIST", headers=HEADERS)
        assert r.status_code == 404

    def test_get_part_suppliers(self, db_client):
        r = client.get("/parts/P-12345/suppliers", headers=HEADERS)
        assert r.status_code == 200
        suppliers = r.json()
        assert len(suppliers) == 2
        names = {s["supplier_name"] for s in suppliers}
        assert "Precision Motors Inc" in names
        assert "Asia Electronics Co" in names
        # Should be ordered by lead_time_days ascending
        lead_times = [s["lead_time_days"] for s in suppliers]
        assert lead_times == sorted(lead_times)

    def test_get_part_suppliers_historical(self, db_client):
        # Both suppliers were active from 2023 — should still appear
        r = client.get("/parts/P-12345/suppliers?as_of=2024-01-01", headers=HEADERS)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_part_compatibility(self, db_client):
        r = client.get("/parts/P-12345/compatibility", headers=HEADERS)
        assert r.status_code == 200
        compat = r.json()
        assert len(compat) == 1
        c = compat[0]
        assert c["substitute_part_id"] == "P-67890"
        assert c["compatibility_type"] == "FORM_FIT_FUNCTION"
        assert c["validation_status"] == "VERIFIED"

    def test_create_and_fetch_part(self, db_client):
        new_part = {
            "id": "P-TEST-001",
            "name": "Test Sensor TS-100",
            "description": "Temp sensor for testing",
            "category": "electronic",
            "criticality": "LOW",
            "specifications": {"range": "-40C to 125C"},
            "unit_of_measure": "EA",
        }
        # Create
        r = client.post("/parts", headers=HEADERS, json=new_part)
        assert r.status_code == 201
        created = r.json()
        assert created["id"] == "P-TEST-001"
        assert created["unit_of_measure"] == "EA"

        # Fetch back
        r2 = client.get("/parts/P-TEST-001", headers=HEADERS)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Test Sensor TS-100"

        # Cleanup
        db_client.execute_write(
            "MATCH (p:Part {id: 'P-TEST-001'}) DETACH DELETE p"
        )


# ─── Suppliers (DB) ───────────────────────────────────────────────────────────

@pytest.mark.db
class TestSuppliers:
    def test_list_suppliers(self, db_client):
        r = client.get("/suppliers", headers=HEADERS)
        assert r.status_code == 200
        suppliers = r.json()
        assert len(suppliers) >= 4
        ids = {s["id"] for s in suppliers}
        assert {"SUP-001", "SUP-002", "SUP-003", "SUP-004"}.issubset(ids)

    def test_get_supplier_by_id(self, db_client):
        r = client.get("/suppliers/SUP-001", headers=HEADERS)
        assert r.status_code == 200
        s = r.json()
        assert s["name"] == "Precision Motors Inc"
        assert s["location"] == "Germany"
        assert "ISO9001" in s["certifications"]
        assert s["status"] == "ACTIVE"

    def test_get_supplier_not_found(self, db_client):
        r = client.get("/suppliers/SUP-NOPE", headers=HEADERS)
        assert r.status_code == 404

    def test_disruption_assessment(self, db_client):
        r = client.get("/suppliers/SUP-001/disruption", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["supplier_id"] == "SUP-001"
        assert body["affected_parts_count"] >= 2
        part_ids = {p["part_id"] for p in body["affected_parts"]}
        assert "P-12345" in part_ids
        assert "P-67890" in part_ids

    def test_create_and_fetch_supplier(self, db_client):
        new_supplier = {
            "id": "SUP-TEST-001",
            "name": "Test Supplier Co",
            "location": "Canada",
            "certifications": ["ISO9001"],
            "status": "ACTIVE",
            "tier": 2,
            "rating": 4.1,
            "contact_info": {"email": "test@testsupplier.ca"},
        }
        r = client.post("/suppliers", headers=HEADERS, json=new_supplier)
        assert r.status_code == 201
        assert r.json()["id"] == "SUP-TEST-001"

        r2 = client.get("/suppliers/SUP-TEST-001", headers=HEADERS)
        assert r2.status_code == 200
        assert r2.json()["location"] == "Canada"

        # Cleanup
        db_client.execute_write(
            "MATCH (s:Supplier {id: 'SUP-TEST-001'}) DETACH DELETE s"
        )


# ─── Reasoning (DB) ───────────────────────────────────────────────────────────

@pytest.mark.db
class TestReasoning:
    def test_compatibility_verified(self, db_client):
        """P-12345 → P-67890 has a VERIFIED relationship — should pass."""
        r = client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id": "P-12345",
            "substitute_part_id": "P-67890",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["result"]["passed"] is True
        assert body["result"]["confidence"] == 1.0
        assert "graph_verified_relationship" in body["result"]["details"]["source"]
        # Provenance chain should have 3 entries: source, validation, decision
        assert body["provenance"]["total_entries"] == 3
        assert body["provenance"]["entry_types"]["decision"] == 1

    def test_compatibility_unverified_pair(self, db_client):
        """P-12345 → P-11111 have no relationship — should fall through to spec checks and fail."""
        r = client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id": "P-12345",
            "substitute_part_id": "P-11111",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["result"]["passed"] is False
        # Category mismatch is the first check — should be the reason
        assert "category" in body["result"]["reason"].lower()

    def test_compatibility_part_not_found(self, db_client):
        r = client.post("/reasoning/compatibility", headers=HEADERS, json={
            "original_part_id": "P-NOPE",
            "substitute_part_id": "P-67890",
        })
        assert r.status_code == 404

    def test_qualify_supplier_passes(self, db_client):
        """SUP-001 has ISO9001 and rating 4.5 — should qualify."""
        r = client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "SUP-001",
            "required_certifications": ["ISO9001"],
            "min_rating": 4.0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["qualified"] is True
        assert body["result"]["passed"] is True

    def test_qualify_supplier_fails_missing_cert(self, db_client):
        """SUP-004 only has ISO9001 — asking for ITAR should fail."""
        r = client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "SUP-004",
            "required_certifications": ["ITAR"],
            "min_rating": 3.5,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["qualified"] is False
        assert "ITAR" in body["result"]["reason"]

    def test_qualify_supplier_fails_low_rating(self, db_client):
        """SUP-004 has rating 4.0 — requiring 4.5 should fail."""
        r = client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "SUP-004",
            "required_certifications": [],
            "min_rating": 4.5,
        })
        assert r.status_code == 200
        assert r.json()["qualified"] is False

    def test_qualify_supplier_not_found(self, db_client):
        r = client.post("/reasoning/qualify-supplier", headers=HEADERS, json={
            "supplier_id": "SUP-NOPE",
            "required_certifications": [],
        })
        assert r.status_code == 404