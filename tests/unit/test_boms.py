"""
Tests for BOM endpoints.

All tests are marked @pytest.mark.db — they require Neo4j running with
the sample parts data loaded (python scripts/load_sample_data.py).

Every test cleans up the BOMs it creates so the graph stays in a known state.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": "dev-api-key"}

# Reusable BOM payload using parts from the sample dataset
SAMPLE_BOM = {
    "id": "BOM-TEST-001",
    "name": "Robot Arm Assembly Rev 1",
    "description": "Test BOM for automated tests",
    "version": "1.0",
    "status": "DRAFT",
    "components": [
        {"part_id": "P-12345", "quantity": 2, "reference_designator": "M1"},
        {"part_id": "P-11111", "quantity": 4, "reference_designator": "BR1"},
        {"part_id": "P-22222", "quantity": 1, "reference_designator": "CBL1"},
    ],
}


@pytest.fixture(autouse=True)
def cleanup(db_client):
    """Delete test BOMs before and after each test."""
    def _delete():
        db_client.execute_write(
            "MATCH (b:BOM) WHERE b.id STARTS WITH 'BOM-TEST' "
            "OPTIONAL MATCH (b)-[:CONTAINS]->(c:Component) "
            "DETACH DELETE b, c"
        )
    _delete()
    yield
    _delete()


# ─── Auth ─────────────────────────────────────────────────────────────────────

@pytest.mark.db
def test_list_boms_requires_auth(db_client):
    r = client.get("/boms")
    assert r.status_code == 401


# ─── List ─────────────────────────────────────────────────────────────────────

@pytest.mark.db
class TestListBOMs:
    def test_list_empty(self, db_client):
        r = client.get("/boms", headers=HEADERS)
        assert r.status_code == 200
        # May have other BOMs from earlier tests but won't have our test ones
        ids = [b["id"] for b in r.json()]
        assert "BOM-TEST-001" not in ids

    def test_list_after_create(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms", headers=HEADERS)
        assert r.status_code == 200
        ids = [b["id"] for b in r.json()]
        assert "BOM-TEST-001" in ids

    def test_list_filter_by_status(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms?status=DRAFT", headers=HEADERS)
        assert r.status_code == 200
        assert all(b["status"] == "DRAFT" for b in r.json())

    def test_list_filter_excludes_other_status(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms?status=RELEASED", headers=HEADERS)
        assert r.status_code == 200
        ids = [b["id"] for b in r.json()]
        assert "BOM-TEST-001" not in ids

    def test_list_includes_component_count(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms", headers=HEADERS)
        bom = next(b for b in r.json() if b["id"] == "BOM-TEST-001")
        assert bom["component_count"] == 3


# ─── Create ───────────────────────────────────────────────────────────────────

@pytest.mark.db
class TestCreateBOM:
    def test_create_returns_201(self, db_client):
        r = client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        assert r.status_code == 201

    def test_create_response_schema(self, db_client):
        r = client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        body = r.json()
        assert body["id"] == "BOM-TEST-001"
        assert body["name"] == "Robot Arm Assembly Rev 1"
        assert body["version"] == "1.0"
        assert body["status"] == "DRAFT"
        assert len(body["components"]) == 3

    def test_create_components_have_correct_fields(self, db_client):
        r = client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        components = r.json()["components"]
        part_ids = {c["part_id"] for c in components}
        assert part_ids == {"P-12345", "P-11111", "P-22222"}

        m1 = next(c for c in components if c["part_id"] == "P-12345")
        assert m1["quantity"] == 2
        assert m1["reference_designator"] == "M1"
        assert m1["part_name"] == "Servo Motor SM-400"
        assert m1["criticality"] == "HIGH"

    def test_create_no_components(self, db_client):
        bom = {**SAMPLE_BOM, "components": []}
        r = client.post("/boms", headers=HEADERS, json=bom)
        assert r.status_code == 201
        assert r.json()["components"] == []

    def test_create_invalid_part_returns_422(self, db_client):
        bom = {**SAMPLE_BOM, "components": [
            {"part_id": "P-DOES-NOT-EXIST", "quantity": 1}
        ]}
        r = client.post("/boms", headers=HEADERS, json=bom)
        assert r.status_code == 422
        assert "P-DOES-NOT-EXIST" in r.json()["detail"]

    def test_create_missing_id_returns_422(self, db_client):
        bom = {k: v for k, v in SAMPLE_BOM.items() if k != "id"}
        r = client.post("/boms", headers=HEADERS, json=bom)
        assert r.status_code == 422


# ─── Get ──────────────────────────────────────────────────────────────────────

@pytest.mark.db
class TestGetBOM:
    def test_get_existing(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms/BOM-TEST-001", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["id"] == "BOM-TEST-001"

    def test_get_not_found(self, db_client):
        r = client.get("/boms/BOM-DOES-NOT-EXIST", headers=HEADERS)
        assert r.status_code == 404

    def test_get_includes_all_components(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms/BOM-TEST-001", headers=HEADERS)
        assert len(r.json()["components"]) == 3


# ─── Add component ────────────────────────────────────────────────────────────

@pytest.mark.db
class TestAddComponent:
    def test_add_component(self, db_client):
        client.post("/boms", headers=HEADERS, json={**SAMPLE_BOM, "components": []})
        r = client.post("/boms/BOM-TEST-001/components", headers=HEADERS, json={
            "part_id": "P-33333",
            "quantity": 1,
            "reference_designator": "MCU1",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["part_id"] == "P-33333"
        assert body["quantity"] == 1
        assert body["reference_designator"] == "MCU1"
        assert body["part_name"] == "Controller Board CB-2000"

    def test_add_component_bom_not_found(self, db_client):
        r = client.post("/boms/BOM-NOPE/components", headers=HEADERS, json={
            "part_id": "P-33333", "quantity": 1,
        })
        assert r.status_code == 404

    def test_add_component_part_not_found(self, db_client):
        client.post("/boms", headers=HEADERS, json={**SAMPLE_BOM, "components": []})
        r = client.post("/boms/BOM-TEST-001/components", headers=HEADERS, json={
            "part_id": "P-NOPE", "quantity": 1,
        })
        assert r.status_code == 422

    def test_add_component_zero_quantity_returns_422(self, db_client):
        client.post("/boms", headers=HEADERS, json={**SAMPLE_BOM, "components": []})
        r = client.post("/boms/BOM-TEST-001/components", headers=HEADERS, json={
            "part_id": "P-33333", "quantity": 0,  # violates gt=0
        })
        assert r.status_code == 422


# ─── Risk assessment ──────────────────────────────────────────────────────────

@pytest.mark.db
class TestBOMRisk:
    def test_risk_response_schema(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms/BOM-TEST-001/risk", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["bom_id"] == "BOM-TEST-001"
        assert body["total_components"] == 3
        assert "at_risk_count" in body
        assert "components" in body
        assert "at_risk_components" in body

    def test_risk_all_components_present(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms/BOM-TEST-001/risk", headers=HEADERS)
        part_ids = {c["part_id"] for c in r.json()["components"]}
        assert part_ids == {"P-12345", "P-11111", "P-22222"}

    def test_risk_levels_valid(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms/BOM-TEST-001/risk", headers=HEADERS)
        valid_levels = {"NO_SUPPLIER", "SINGLE_SOURCE", "MULTI_SOURCE"}
        for comp in r.json()["components"]:
            assert comp["risk_level"] in valid_levels

    def test_risk_p12345_is_multi_source(self, db_client):
        """P-12345 has 2 suppliers (SUP-001, SUP-002) — should be MULTI_SOURCE."""
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/boms/BOM-TEST-001/risk", headers=HEADERS)
        comp = next(c for c in r.json()["components"] if c["part_id"] == "P-12345")
        assert comp["risk_level"] == "MULTI_SOURCE"
        assert comp["supplier_count"] == 2

    def test_risk_not_found(self, db_client):
        r = client.get("/boms/BOM-NOPE/risk", headers=HEADERS)
        assert r.status_code == 404


# ─── Delete ───────────────────────────────────────────────────────────────────

@pytest.mark.db
class TestDeleteBOM:
    def test_delete_returns_204(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.delete("/boms/BOM-TEST-001", headers=HEADERS)
        assert r.status_code == 204

    def test_delete_removes_from_list(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        client.delete("/boms/BOM-TEST-001", headers=HEADERS)
        r = client.get("/boms", headers=HEADERS)
        ids = [b["id"] for b in r.json()]
        assert "BOM-TEST-001" not in ids

    def test_delete_not_found(self, db_client):
        r = client.delete("/boms/BOM-NOPE", headers=HEADERS)
        assert r.status_code == 404

    def test_delete_preserves_parts(self, db_client):
        """Deleting a BOM must not delete the Parts it referenced."""
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        client.delete("/boms/BOM-TEST-001", headers=HEADERS)
        r = client.get("/parts/P-12345", headers=HEADERS)
        assert r.status_code == 200


# ─── /parts/{id}/boms ─────────────────────────────────────────────────────────

@pytest.mark.db
class TestPartBOMUsage:
    def test_part_bom_usage(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/parts/P-12345/boms", headers=HEADERS)
        assert r.status_code == 200
        bom_ids = [b["bom_id"] for b in r.json()]
        assert "BOM-TEST-001" in bom_ids

    def test_part_not_in_any_bom(self, db_client):
        """P-67890 is not in the sample BOM — should return empty list."""
        r = client.get("/parts/P-67890/boms", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_part_bom_usage_includes_quantity(self, db_client):
        client.post("/boms", headers=HEADERS, json=SAMPLE_BOM)
        r = client.get("/parts/P-12345/boms", headers=HEADERS)
        usage = next(b for b in r.json() if b["bom_id"] == "BOM-TEST-001")
        assert usage["quantity"] == 2

    def test_part_not_found_returns_404(self, db_client):
        r = client.get("/parts/P-NOPE/boms", headers=HEADERS)
        assert r.status_code == 404