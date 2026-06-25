"""
Tests for the extraction endpoints.

The Claude API is mocked throughout so these tests run without a real
API key and without making any network calls.

DB tests (marked @pytest.mark.db) require Neo4j running with sample data loaded.
No-DB tests exercise extraction logic, schema validation, and persist
behaviour against a live Neo4j but with a fake Claude response.
"""

import json
from unittest.mock import MagicMock, patch

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

# ─── Shared fixtures / helpers ────────────────────────────────────────────────

SAMPLE_ENTITIES = {
    "parts": [
        {
            "part_id": "P-EXT-001",
            "name": "Hydraulic Pump HP-200",
            "category": "mechanical",
            "specifications": {"flow_rate": "200 L/min", "pressure": "250 bar"},
            "unit_of_measure": "EA",
        }
    ],
    "suppliers": [
        {
            "name": "Nordic Hydraulics AB",
            "location": "Sweden",
            "certifications": ["ISO9001", "ISO14001"],
            "contact_info": {"email": "sales@nordichydraulics.se"},
        }
    ],
    "relationships": [
        {
            "supplier_name": "Nordic Hydraulics AB",
            "part_id": "P-EXT-001",
            "lead_time_days": 28,
            "price": 1250.0,
            "currency": "USD",
        }
    ],
}

SAMPLE_CATALOG_TEXT = """
SUPPLIER CATALOG — Nordic Hydraulics AB
Location: Sweden | Certifications: ISO9001, ISO14001
Contact: sales@nordichydraulics.se

PRODUCT LISTING
---------------
Part No: P-EXT-001
Name: Hydraulic Pump HP-200
Category: Mechanical
Flow Rate: 200 L/min | Max Pressure: 250 bar
Unit Price: $1,250.00 | Lead Time: 28 days
"""


def _mock_extractor(entities: dict = None):
    """Return a mock ClaudeEntityExtractor whose extract_with_direct_api returns fixed data."""
    from src.ingestion.entity_extractor import ExtractionResult

    mock = MagicMock()
    mock.extract_with_direct_api.return_value = ExtractionResult(
        entities=[entities or SAMPLE_ENTITIES],
        confidence=0.95,
        source="catalog",
        extraction_method="claude_mock_direct",
    )
    return mock


# ─── Auth ─────────────────────────────────────────────────────────────────────

def test_extract_requires_auth():
    r = client.post("/extraction/extract", json={"text": "some document text"})
    assert r.status_code == 401


def test_extract_and_persist_requires_auth():
    r = client.post("/extraction/extract-and-persist", json={"text": "some document text"})
    assert r.status_code == 401


# ─── Input validation ─────────────────────────────────────────────────────────

def test_extract_text_too_short_returns_422():
    r = client.post("/extraction/extract", headers=HEADERS,
                    json={"text": "short"})  # min_length=10
    assert r.status_code == 422


def test_extract_missing_text_returns_422():
    r = client.post("/extraction/extract", headers=HEADERS,
                    json={"document_type": "catalog"})
    assert r.status_code == 422


# ─── Core extraction (mocked Claude) ─────────────────────────────────────────

class TestExtract:
    def test_extract_returns_correct_structure(self):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
                "document_type": "catalog",
                "source": "test_catalog",
            })

        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "test_catalog"
        assert body["document_type"] == "catalog"
        assert body["confidence"] == 0.95
        assert body["parts_found"] == 1
        assert body["suppliers_found"] == 1
        assert body["relationships_found"] == 1
        assert body["persist_summary"] is None  # persist=False by default

    def test_extract_entities_content(self):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
            })

        body = r.json()
        parts = body["entities"]["parts"]
        assert len(parts) == 1
        assert parts[0]["part_id"] == "P-EXT-001"
        assert parts[0]["name"] == "Hydraulic Pump HP-200"
        assert parts[0]["specifications"]["flow_rate"] == "200 L/min"

        suppliers = body["entities"]["suppliers"]
        assert suppliers[0]["name"] == "Nordic Hydraulics AB"
        assert "ISO9001" in suppliers[0]["certifications"]

    def test_extract_default_document_type(self):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
            })
        assert r.json()["document_type"] == "unknown"

    def test_extract_empty_entities(self):
        """Claude returns empty lists — should still succeed with zero counts."""
        empty = {"parts": [], "suppliers": [], "relationships": []}
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor(empty)):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
            })
        assert r.status_code == 200
        body = r.json()
        assert body["parts_found"] == 0
        assert body["suppliers_found"] == 0
        assert body["relationships_found"] == 0

    def test_extract_502_on_claude_failure(self):
        """If Claude raises an exception, endpoint returns 502."""
        mock = MagicMock()
        mock.extract_with_direct_api.side_effect = RuntimeError("Claude API timeout")
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=mock):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
            })
        assert r.status_code == 502
        assert "Extraction failed" in r.json()["detail"]


# ─── Persist behaviour (DB required) ─────────────────────────────────────────

@pytest.mark.db
class TestExtractPersist:
    def _cleanup(self, db_client):
        db_client.execute_write(
            "MATCH (n) WHERE n.id IN ['P-EXT-001'] OR n.id STARTS WITH 'SUP-EXT-' DETACH DELETE n"
        )

    def test_persist_false_does_not_write(self, db_client):
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor()):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
                "persist": False,
            })
        assert r.status_code == 200
        assert r.json()["persist_summary"] is None

        # Nothing written
        rows = db_client.execute_query(
            "MATCH (p:Part {id: 'P-EXT-001'}) RETURN p.id"
        )
        assert rows == []

    def test_persist_true_creates_nodes(self, db_client):
        self._cleanup(db_client)
        try:
            with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                       return_value=_mock_extractor()):
                r = client.post("/extraction/extract", headers=HEADERS, json={
                    "text": SAMPLE_CATALOG_TEXT,
                    "persist": True,
                })
            assert r.status_code == 200
            summary = r.json()["persist_summary"]
            assert summary["parts_created"] == 1
            assert summary["suppliers_created"] == 1
            assert summary["parts_skipped"] == 0
            assert summary["errors"] == []

            # Verify part is in Neo4j
            rows = db_client.execute_query(
                "MATCH (p:Part {id: 'P-EXT-001'}) RETURN p.name AS name"
            )
            assert rows[0]["name"] == "Hydraulic Pump HP-200"

            # Verify supplier is in Neo4j
            rows = db_client.execute_query(
                "MATCH (s:Supplier {id: 'SUP-EXT-NORDIC_HYDRAULICS_AB'}) RETURN s.name AS name"
            )
            assert rows[0]["name"] == "Nordic Hydraulics AB"
        finally:
            self._cleanup(db_client)

    def test_persist_idempotent(self, db_client):
        """Calling extract+persist twice should skip on the second call."""
        self._cleanup(db_client)
        try:
            payload = {
                "text": SAMPLE_CATALOG_TEXT,
                "persist": True,
            }
            with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                       return_value=_mock_extractor()):
                r1 = client.post("/extraction/extract", headers=HEADERS, json=payload)
            with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                       return_value=_mock_extractor()):
                r2 = client.post("/extraction/extract", headers=HEADERS, json=payload)

            s1 = r1.json()["persist_summary"]
            s2 = r2.json()["persist_summary"]
            assert s1["parts_created"] == 1
            assert s2["parts_created"] == 0
            assert s2["parts_skipped"] == 1
        finally:
            self._cleanup(db_client)

    def test_extract_and_persist_alias(self, db_client):
        """The /extract-and-persist endpoint should behave identically to persist=true."""
        self._cleanup(db_client)
        try:
            with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                       return_value=_mock_extractor()):
                r = client.post("/extraction/extract-and-persist", headers=HEADERS, json={
                    "text": SAMPLE_CATALOG_TEXT,
                })
            assert r.status_code == 200
            summary = r.json()["persist_summary"]
            assert summary is not None
            assert summary["parts_created"] == 1
        finally:
            self._cleanup(db_client)

    def test_persist_relationship_created(self, db_client):
        """After persist, a SUPPLIES relationship should exist between the new nodes."""
        self._cleanup(db_client)
        try:
            with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                       return_value=_mock_extractor()):
                client.post("/extraction/extract", headers=HEADERS, json={
                    "text": SAMPLE_CATALOG_TEXT,
                    "persist": True,
                })

            rows = db_client.execute_query("""
                MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: 'P-EXT-001'})
                RETURN s.name AS supplier, r.price AS price, r.lead_time_days AS lead_time
            """)
            assert len(rows) == 1
            assert rows[0]["price"] == 1250.0
            assert rows[0]["lead_time"] == 28
        finally:
            self._cleanup(db_client)

    def test_part_missing_id_reported_in_errors(self, db_client):
        """A part with no ID should appear in errors, not crash the endpoint."""
        bad_entities = {
            "parts": [{"name": "Mystery Part", "category": "electronic"}],  # no part_id
            "suppliers": [],
            "relationships": [],
        }
        with patch("src.api.routers.extraction.ClaudeEntityExtractor",
                   return_value=_mock_extractor(bad_entities)):
            r = client.post("/extraction/extract", headers=HEADERS, json={
                "text": SAMPLE_CATALOG_TEXT,
                "persist": True,
            })
        assert r.status_code == 200
        summary = r.json()["persist_summary"]
        assert summary["parts_created"] == 0
        assert len(summary["errors"]) == 1
        assert "missing ID" in summary["errors"][0]