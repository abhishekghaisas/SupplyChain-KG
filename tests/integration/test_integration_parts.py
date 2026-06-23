"""
Integration tests — Parts router.

Requires: Neo4j running locally, seed_data fixture loaded.

Run with:
    pytest tests/integration/test_integration_parts.py -v -m db
"""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db

HEADERS = {"X-API-Key": "dev-api-key"}


# ── List parts ────────────────────────────────────────────────────────────────

class TestListParts:
    def test_returns_200(self, test_client, seed_data):
        r = test_client.get("/parts", headers=HEADERS)
        assert r.status_code == 200

    def test_returns_list(self, test_client, seed_data):
        r = test_client.get("/parts", headers=HEADERS)
        assert isinstance(r.json(), list)

    def test_seed_parts_present(self, test_client, seed_data):
        r = test_client.get("/parts", headers=HEADERS)
        ids = [p["id"] for p in r.json()]
        assert "TEST-P-001" in ids
        assert "TEST-P-002" in ids

    def test_filter_by_category(self, test_client, seed_data):
        r = test_client.get("/parts?category=electronic", headers=HEADERS)
        parts = r.json()
        assert all(p["category"] == "electronic" for p in parts
                   if p["id"].startswith("TEST-"))

    def test_filter_by_criticality(self, test_client, seed_data):
        r = test_client.get("/parts?criticality=HIGH", headers=HEADERS)
        parts = r.json()
        assert all(p["criticality"] == "HIGH" for p in parts
                   if p["id"].startswith("TEST-"))

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.get("/parts")
        assert r.status_code == 401


# ── Get part ──────────────────────────────────────────────────────────────────

class TestGetPart:
    def test_returns_correct_part(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "TEST-P-001"
        assert body["name"] == "Servo Motor SM-400"
        assert body["category"] == "electronic"
        assert body["criticality"] == "HIGH"

    def test_specifications_decoded(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001", headers=HEADERS)
        specs = r.json()["specifications"]
        assert specs["power_rating"] == "400W"
        assert specs["voltage"] == "24V DC"

    def test_404_for_missing_part(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-NONEXISTENT", headers=HEADERS)
        assert r.status_code == 404

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001")
        assert r.status_code == 401


# ── Create part ───────────────────────────────────────────────────────────────

class TestCreatePart:
    PART_ID = "TEST-P-CREATE-001"

    def setup_method(self, _):
        """Ensure clean state before each test."""
        self._cleanup_id = self.PART_ID

    def teardown_method(self, _, db_client=None):
        pass  # session teardown handles TEST- nodes

    def test_create_returns_201(self, test_client, seed_data):
        r = test_client.post("/parts", headers=HEADERS, json={
            "id":          self.PART_ID,
            "name":        "Test Created Part",
            "description": "Integration test part",
            "category":    "mechanical",
            "criticality": "LOW",
            "specifications": {"material": "Aluminium"},
            "unit_of_measure": "EA",
        })
        assert r.status_code == 201

    def test_create_returns_part(self, test_client, seed_data):
        r = test_client.post("/parts", headers=HEADERS, json={
            "id":          "TEST-P-CREATE-002",
            "name":        "Test Part 2",
            "description": "desc",
            "category":    "electrical",
            "criticality": "MEDIUM",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == "TEST-P-CREATE-002"
        assert body["name"] == "Test Part 2"

    def test_create_missing_required_field_returns_422(self, test_client, seed_data):
        r = test_client.post("/parts", headers=HEADERS, json={
            "name": "No ID Part",
            "description": "missing id",
            "category": "electronic",
            "criticality": "LOW",
        })
        assert r.status_code == 422

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.post("/parts", json={
            "id": "TEST-P-NOAUTH", "name": "x", "description": "",
            "category": "electronic", "criticality": "LOW",
        })
        assert r.status_code == 401


# ── Part suppliers ────────────────────────────────────────────────────────────

class TestPartSuppliers:
    def test_returns_suppliers_for_part(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001/suppliers", headers=HEADERS)
        assert r.status_code == 200
        suppliers = r.json()
        assert len(suppliers) >= 2   # TEST-SUP-001 and TEST-SUP-002 both supply TEST-P-001
        supplier_ids = [s["supplier_id"] for s in suppliers]
        assert "TEST-SUP-001" in supplier_ids
        assert "TEST-SUP-002" in supplier_ids

    def test_lead_time_and_price_present(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001/suppliers", headers=HEADERS)
        for s in r.json():
            assert "lead_time_days" in s
            assert "price" in s

    def test_historical_query(self, test_client, seed_data):
        # Before TEST-SUP-002 contract started (2023-06-01), only TEST-SUP-001 supplied TEST-P-001
        r = test_client.get(
            "/parts/TEST-P-001/suppliers?as_of=2023-03-01",
            headers=HEADERS,
        )
        assert r.status_code == 200
        supplier_ids = [s["supplier_id"] for s in r.json()]
        assert "TEST-SUP-001" in supplier_ids
        assert "TEST-SUP-002" not in supplier_ids

    def test_404_for_missing_part(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-NONEXISTENT/suppliers", headers=HEADERS)
        assert r.status_code == 404


# ── Part compatibility ────────────────────────────────────────────────────────

class TestPartCompatibility:
    def test_returns_substitutes(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001/compatibility", headers=HEADERS)
        assert r.status_code == 200
        subs = r.json()
        assert len(subs) >= 1
        assert subs[0]["substitute_part_id"] == "TEST-P-002"
        assert subs[0]["compatibility_type"] == "FORM_FIT_FUNCTION"
        assert subs[0]["validation_status"] == "VERIFIED"

    def test_no_substitutes_returns_empty(self, test_client, seed_data):
        # TEST-P-003 has no COMPATIBLE_WITH relationships
        r = test_client.get("/parts/TEST-P-003/compatibility", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_404_for_missing_part(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-NONEXISTENT/compatibility", headers=HEADERS)
        assert r.status_code == 404


# ── Part BOM usage ────────────────────────────────────────────────────────────

class TestPartBOMUsage:
    BOM_ID = "TEST-BOM-PART-USAGE"

    @pytest.fixture(autouse=True)
    def setup_bom(self, db_client, seed_data):
        """Create a BOM containing TEST-P-001 for usage tests."""
        db_client.create_bom(
            bom_id=self.BOM_ID, name="Usage Test BOM",
            description="", version="1.0", status="RELEASED",
        )
        db_client.add_bom_component(
            bom_id=self.BOM_ID, part_id="TEST-P-001", quantity=2.0,
        )
        yield
        db_client.delete_bom(self.BOM_ID)

    def test_returns_boms_for_part(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001/boms", headers=HEADERS)
        assert r.status_code == 200
        bom_ids = [b["bom_id"] for b in r.json()]
        assert self.BOM_ID in bom_ids

    def test_quantity_in_response(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-001/boms", headers=HEADERS)
        entry = next(b for b in r.json() if b["bom_id"] == self.BOM_ID)
        assert entry["quantity"] == 2.0

    def test_part_with_no_boms(self, test_client, seed_data):
        r = test_client.get("/parts/TEST-P-004/boms", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []