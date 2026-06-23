"""
Integration tests — BOMs router.

Requires: Neo4j running locally, seed_data fixture loaded.
"""

import pytest

pytestmark = pytest.mark.db

HEADERS = {"X-API-Key": "dev-api-key"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_bom(test_client, bom_id: str, components: list = None) -> dict:
    """Helper to create a BOM via the API."""
    payload = {
        "id":          bom_id,
        "name":        f"Test BOM {bom_id}",
        "description": "Integration test BOM",
        "version":     "1.0",
        "status":      "DRAFT",
    }
    if components:
        payload["components"] = components
    r = test_client.post("/boms", headers=HEADERS, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── List BOMs ─────────────────────────────────────────────────────────────────

class TestListBOMs:
    BOM_ID = "TEST-BOM-LIST-001"

    @pytest.fixture(autouse=True)
    def setup(self, db_client, seed_data):
        db_client.create_bom(
            bom_id=self.BOM_ID, name="List Test BOM",
            description="", version="1.0", status="DRAFT",
        )
        yield
        db_client.delete_bom(self.BOM_ID)

    def test_returns_200(self, test_client, seed_data):
        r = test_client.get("/boms", headers=HEADERS)
        assert r.status_code == 200

    def test_created_bom_present(self, test_client, seed_data):
        r = test_client.get("/boms", headers=HEADERS)
        ids = [b["id"] for b in r.json()]
        assert self.BOM_ID in ids

    def test_filter_by_status(self, test_client, seed_data):
        r = test_client.get("/boms?status=DRAFT", headers=HEADERS)
        boms = r.json()
        assert all(b["status"] == "DRAFT" for b in boms
                   if b["id"].startswith("TEST-"))

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.get("/boms")
        assert r.status_code == 401


# ── Create BOM ────────────────────────────────────────────────────────────────

class TestCreateBOM:
    def test_create_empty_bom(self, test_client, seed_data):
        r = test_client.post("/boms", headers=HEADERS, json={
            "id":          "TEST-BOM-CREATE-001",
            "name":        "Empty BOM",
            "description": "",
            "version":     "1.0",
            "status":      "DRAFT",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == "TEST-BOM-CREATE-001"
        assert body["version"] == "1.0"
        assert body["status"] == "DRAFT"

    def test_create_bom_with_inline_components(self, test_client, seed_data):
        r = test_client.post("/boms", headers=HEADERS, json={
            "id":      "TEST-BOM-CREATE-002",
            "name":    "BOM With Parts",
            "version": "1.0",
            "status":  "DRAFT",
            "components": [
                {"part_id": "TEST-P-001", "quantity": 2.0,
                 "reference_designator": "U1"},
                {"part_id": "TEST-P-003", "quantity": 4.0,
                 "reference_designator": "M1"},
            ],
        })
        assert r.status_code == 201
        body = r.json()
        assert len(body["components"]) == 2
        part_ids = [c["part_id"] for c in body["components"]]
        assert "TEST-P-001" in part_ids
        assert "TEST-P-003" in part_ids

    def test_create_with_nonexistent_part_returns_422(self, test_client, seed_data):
        r = test_client.post("/boms", headers=HEADERS, json={
            "id":      "TEST-BOM-CREATE-BADPART",
            "name":    "Bad BOM",
            "version": "1.0",
            "status":  "DRAFT",
            "components": [{"part_id": "TEST-P-NONEXISTENT", "quantity": 1.0}],
        })
        assert r.status_code == 422

    def test_missing_id_returns_422(self, test_client, seed_data):
        r = test_client.post("/boms", headers=HEADERS, json={
            "name": "No ID BOM", "version": "1.0",
        })
        assert r.status_code == 422

    def test_requires_auth(self, test_client, seed_data):
        r = test_client.post("/boms", json={
            "id": "TEST-BOM-NOAUTH", "name": "x", "version": "1.0",
        })
        assert r.status_code == 401


# ── Get BOM ───────────────────────────────────────────────────────────────────

class TestGetBOM:
    BOM_ID = "TEST-BOM-GET-001"

    @pytest.fixture(autouse=True)
    def setup(self, db_client, seed_data):
        db_client.create_bom(
            bom_id=self.BOM_ID, name="Get Test BOM",
            description="desc", version="2.0", status="RELEASED",
        )
        db_client.add_bom_component(
            bom_id=self.BOM_ID, part_id="TEST-P-001",
            quantity=3.0, reference_designator="U1",
        )
        yield
        db_client.delete_bom(self.BOM_ID)

    def test_returns_bom(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == self.BOM_ID
        assert body["version"] == "2.0"
        assert body["status"] == "RELEASED"

    def test_components_included(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}", headers=HEADERS)
        components = r.json()["components"]
        assert len(components) == 1
        assert components[0]["part_id"] == "TEST-P-001"
        assert components[0]["quantity"] == 3.0

    def test_404_for_missing_bom(self, test_client, seed_data):
        r = test_client.get("/boms/TEST-BOM-NONEXISTENT", headers=HEADERS)
        assert r.status_code == 404


# ── Add component ─────────────────────────────────────────────────────────────

class TestAddComponent:
    BOM_ID = "TEST-BOM-ADDCOMP-001"

    @pytest.fixture(autouse=True)
    def setup(self, db_client, seed_data):
        db_client.create_bom(
            bom_id=self.BOM_ID, name="Add Component BOM",
            description="", version="1.0", status="DRAFT",
        )
        yield
        db_client.delete_bom(self.BOM_ID)

    def test_add_component_returns_201(self, test_client, seed_data):
        r = test_client.post(
            f"/boms/{self.BOM_ID}/components", headers=HEADERS,
            json={"part_id": "TEST-P-001", "quantity": 5.0,
                  "reference_designator": "U1"},
        )
        assert r.status_code == 201

    def test_component_appears_in_bom(self, test_client, seed_data):
        test_client.post(
            f"/boms/{self.BOM_ID}/components", headers=HEADERS,
            json={"part_id": "TEST-P-003", "quantity": 2.0},
        )
        r = test_client.get(f"/boms/{self.BOM_ID}", headers=HEADERS)
        part_ids = [c["part_id"] for c in r.json()["components"]]
        assert "TEST-P-003" in part_ids

    def test_nonexistent_part_returns_422(self, test_client, seed_data):
        r = test_client.post(
            f"/boms/{self.BOM_ID}/components", headers=HEADERS,
            json={"part_id": "TEST-P-NONEXISTENT", "quantity": 1.0},
        )
        assert r.status_code == 422

    def test_nonexistent_bom_returns_404(self, test_client, seed_data):
        r = test_client.post(
            "/boms/TEST-BOM-NONEXISTENT/components", headers=HEADERS,
            json={"part_id": "TEST-P-001", "quantity": 1.0},
        )
        assert r.status_code == 404


# ── Delete BOM ────────────────────────────────────────────────────────────────

class TestDeleteBOM:
    def test_delete_returns_204(self, test_client, db_client, seed_data):
        db_client.create_bom(
            bom_id="TEST-BOM-DEL-001", name="Delete Me",
            description="", version="1.0", status="DRAFT",
        )
        r = test_client.delete("/boms/TEST-BOM-DEL-001", headers=HEADERS)
        assert r.status_code == 204

    def test_deleted_bom_not_found(self, test_client, db_client, seed_data):
        db_client.create_bom(
            bom_id="TEST-BOM-DEL-002", name="Delete Me Too",
            description="", version="1.0", status="DRAFT",
        )
        test_client.delete("/boms/TEST-BOM-DEL-002", headers=HEADERS)
        r = test_client.get("/boms/TEST-BOM-DEL-002", headers=HEADERS)
        assert r.status_code == 404

    def test_delete_nonexistent_returns_404(self, test_client, seed_data):
        r = test_client.delete("/boms/TEST-BOM-NONEXISTENT", headers=HEADERS)
        assert r.status_code == 404


# ── BOM risk assessment ───────────────────────────────────────────────────────

class TestBOMRisk:
    BOM_ID = "TEST-BOM-RISK-001"

    @pytest.fixture(autouse=True)
    def setup(self, db_client, seed_data):
        db_client.create_bom(
            bom_id=self.BOM_ID, name="Risk Test BOM",
            description="", version="1.0", status="RELEASED",
        )
        # TEST-P-001: MULTI_SOURCE (2 suppliers)
        db_client.add_bom_component(self.BOM_ID, "TEST-P-001", 2.0)
        # TEST-P-003: SINGLE_SOURCE (1 supplier)
        db_client.add_bom_component(self.BOM_ID, "TEST-P-003", 1.0)
        # TEST-P-004: NO_SUPPLIER (0 suppliers), CRITICAL
        db_client.add_bom_component(self.BOM_ID, "TEST-P-004", 1.0)
        yield
        db_client.delete_bom(self.BOM_ID)

    def test_returns_risk_assessment(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}/risk", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["bom_id"] == self.BOM_ID
        assert body["total_components"] == 3

    def test_no_supplier_flagged(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}/risk", headers=HEADERS)
        components = {c["part_id"]: c for c in r.json()["components"]}
        assert components["TEST-P-004"]["risk_level"] == "NO_SUPPLIER"

    def test_single_source_flagged(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}/risk", headers=HEADERS)
        components = {c["part_id"]: c for c in r.json()["components"]}
        assert components["TEST-P-003"]["risk_level"] == "SINGLE_SOURCE"

    def test_multi_source_ok(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}/risk", headers=HEADERS)
        components = {c["part_id"]: c for c in r.json()["components"]}
        assert components["TEST-P-001"]["risk_level"] == "MULTI_SOURCE"

    def test_at_risk_count_correct(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}/risk", headers=HEADERS)
        body = r.json()
        # TEST-P-003 (SINGLE_SOURCE) + TEST-P-004 (NO_SUPPLIER) = 2 at risk
        assert body["at_risk_count"] == 2

    def test_at_risk_components_listed(self, test_client, seed_data):
        r = test_client.get(f"/boms/{self.BOM_ID}/risk", headers=HEADERS)
        at_risk_ids = [c["part_id"] for c in r.json()["at_risk_components"]]
        assert "TEST-P-003" in at_risk_ids
        assert "TEST-P-004" in at_risk_ids
        assert "TEST-P-001" not in at_risk_ids

    def test_404_for_missing_bom(self, test_client, seed_data):
        r = test_client.get("/boms/TEST-BOM-NONEXISTENT/risk", headers=HEADERS)
        assert r.status_code == 404