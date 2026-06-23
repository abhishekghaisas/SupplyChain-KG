"""
Integration tests — Extraction router (DB tests).

These tests exercise the full persist path: Claude is mocked but
Neo4j is real, so we verify nodes are actually written/skipped/cleaned.

Replaces and extends the @pytest.mark.db class from the uploaded
test_extraction.py.

Requires: Neo4j running locally, seed_data fixture loaded.
"""

from __future__ import annotations

from unittest.mock import patch
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.db

HEADERS = {"X-API-Key": "dev-api-key"}

# ── Shared mock data ──────────────────────────────────────────────────────────

CATALOG_TEXT = """
SUPPLIER CATALOG — Nordic Hydraulics AB
Location: Sweden | Certifications: ISO9001, ISO14001
Contact: sales@nordichydraulics.se

Part No: P-EXT-TEST-001
Name: Hydraulic Pump HP-200
Category: Mechanical
Unit Price: $1,250.00 | Lead Time: 28 days
"""

SAMPLE_ENTITIES: Dict[str, Any] = {
    "parts": [
        {
            "part_id":        "TEST-EXT-P-001",
            "name":           "Hydraulic Pump HP-200",
            "category":       "mechanical",
            "specifications": {"flow_rate": "200 L/min", "pressure": "250 bar"},
            "unit_of_measure": "EA",
        }
    ],
    "suppliers": [
        {
            "name":           "Nordic Hydraulics Test AB",
            "location":       "Sweden",
            "certifications": ["ISO9001", "ISO14001"],
            "contact_info":   {"email": "sales@nordichydraulics.se"},
        }
    ],
    "relationships": [
        {
            "supplier_name":  "Nordic Hydraulics Test AB",
            "part_id":        "TEST-EXT-P-001",
            "lead_time_days": 28,
            "price":          1250.0,
            "currency":       "USD",
        }
    ],
}

# Supplier ID derived by extraction.py logic
EXPECTED_SUPPLIER_ID = "SUP-EXT-NORDIC_HYDRAULICS_TE"    # [:20] of "NORDIC_HYDRAULICS_TEST_AB"


@dataclass
class _ExtractionResult:
    entities: List[Dict[str, Any]]
    confidence: float
    source: str
    extraction_method: str
    raw_response: Optional[str] = None


def _mock_extractor(entities=None):
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.extract_with_direct_api.return_value = _ExtractionResult(
        entities=[entities or SAMPLE_ENTITIES],
        confidence=0.95,
        source="catalog",
        extraction_method="claude_mock_direct",
    )
    return mock


# ── Cleanup helper ────────────────────────────────────────────────────────────

def _cleanup(db_client):
    db_client.execute_write(
        "MATCH (n) WHERE n.id IN [$pid, $sid] DETACH DELETE n",
        {"pid": "TEST-EXT-P-001", "sid": EXPECTED_SUPPLIER_ID},
    )


# ── persist=False ─────────────────────────────────────────────────────────────

class TestExtractNoPersist:
    def test_persist_false_does_not_write_part(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = test_client.post("/extraction/extract", headers=HEADERS, json={
                "text":    CATALOG_TEXT,
                "persist": False,
            })
        assert r.status_code == 200
        assert r.json()["persist_summary"] is None

        rows = db_client.execute_query(
            "MATCH (p:Part {id: $id}) RETURN p.id", {"id": "TEST-EXT-P-001"}
        )
        assert rows == []

    def test_persist_false_does_not_write_supplier(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            test_client.post("/extraction/extract", headers=HEADERS, json={
                "text": CATALOG_TEXT, "persist": False,
            })
        rows = db_client.execute_query(
            "MATCH (s:Supplier {id: $id}) RETURN s.id", {"id": EXPECTED_SUPPLIER_ID}
        )
        assert rows == []


# ── persist=True ──────────────────────────────────────────────────────────────

class TestExtractWithPersist:
    @pytest.fixture(autouse=True)
    def cleanup(self, db_client):
        _cleanup(db_client)
        yield
        _cleanup(db_client)

    def test_persist_creates_part(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = test_client.post("/extraction/extract", headers=HEADERS, json={
                "text": CATALOG_TEXT, "persist": True,
            })
        assert r.status_code == 200
        summary = r.json()["persist_summary"]
        assert summary["parts_created"] == 1

        rows = db_client.execute_query(
            "MATCH (p:Part {id: $id}) RETURN p.name AS name",
            {"id": "TEST-EXT-P-001"},
        )
        assert rows[0]["name"] == "Hydraulic Pump HP-200"

    def test_persist_creates_supplier(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = test_client.post("/extraction/extract", headers=HEADERS, json={
                "text": CATALOG_TEXT, "persist": True,
            })
        assert r.json()["persist_summary"]["suppliers_created"] == 1

        rows = db_client.execute_query(
            "MATCH (s:Supplier {id: $id}) RETURN s.name AS name",
            {"id": EXPECTED_SUPPLIER_ID},
        )
        assert rows[0]["name"] == "Nordic Hydraulics Test AB"

    def test_persist_creates_supplies_relationship(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            test_client.post("/extraction/extract", headers=HEADERS, json={
                "text": CATALOG_TEXT, "persist": True,
            })

        rows = db_client.execute_query(
            """
            MATCH (s:Supplier {id: $sid})-[r:SUPPLIES]->(p:Part {id: $pid})
            RETURN r.price AS price, r.lead_time_days AS lead_time
            """,
            {"sid": EXPECTED_SUPPLIER_ID, "pid": "TEST-EXT-P-001"},
        )
        assert len(rows) == 1
        assert rows[0]["price"] == 1250.0
        assert rows[0]["lead_time"] == 28

    def test_persist_idempotent_second_call_skips(self, test_client, db_client, seed_data):
        payload = {"text": CATALOG_TEXT, "persist": True}
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r1 = test_client.post("/extraction/extract", headers=HEADERS, json=payload)
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r2 = test_client.post("/extraction/extract", headers=HEADERS, json=payload)

        s1 = r1.json()["persist_summary"]
        s2 = r2.json()["persist_summary"]
        assert s1["parts_created"] == 1
        assert s2["parts_created"] == 0
        assert s2["parts_skipped"] == 1

    def test_part_missing_id_reported_in_errors(self, test_client, db_client, seed_data):
        bad_entities = {
            "parts":         [{"name": "Mystery Part", "category": "electronic"}],
            "suppliers":     [],
            "relationships": [],
        }
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor(bad_entities)):
            r = test_client.post("/extraction/extract", headers=HEADERS, json={
                "text": CATALOG_TEXT, "persist": True,
            })
        assert r.status_code == 200
        summary = r.json()["persist_summary"]
        assert summary["parts_created"] == 0
        assert len(summary["errors"]) >= 1
        assert "missing ID" in summary["errors"][0]

    def test_persist_summary_in_response(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = test_client.post("/extraction/extract", headers=HEADERS, json={
                "text": CATALOG_TEXT, "persist": True,
            })
        summary = r.json()["persist_summary"]
        assert "parts_created" in summary
        assert "suppliers_created" in summary
        assert "parts_skipped" in summary
        assert "errors" in summary


# ── extract-and-persist alias ─────────────────────────────────────────────────

class TestExtractAndPersistAlias:
    @pytest.fixture(autouse=True)
    def cleanup(self, db_client):
        _cleanup(db_client)
        yield
        _cleanup(db_client)

    def test_alias_always_persists(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = test_client.post("/extraction/extract-and-persist", headers=HEADERS,
                                 json={"text": CATALOG_TEXT})
        assert r.status_code == 200
        summary = r.json()["persist_summary"]
        assert summary is not None
        assert summary["parts_created"] == 1

    def test_alias_writes_to_db(self, test_client, db_client, seed_data):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            test_client.post("/extraction/extract-and-persist", headers=HEADERS,
                             json={"text": CATALOG_TEXT})
        rows = db_client.execute_query(
            "MATCH (p:Part {id: $id}) RETURN p.id", {"id": "TEST-EXT-P-001"}
        )
        assert len(rows) == 1