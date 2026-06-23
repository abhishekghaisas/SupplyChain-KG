"""
conftest.py — shared fixtures for integration tests.

Requires Neo4j running locally at bolt://localhost:7687
(default credentials: neo4j / supplychainkg, matching config.py defaults).

All test nodes use IDs prefixed with "TEST-" so teardown never touches
production data — even if tests run against a shared instance.

Usage:
    pytest tests/integration/ -v               # all integration tests
    pytest tests/integration/ -v -m db         # only DB-touching tests
    pytest tests/integration/ -v -k "parts"    # just the parts suite
"""

from __future__ import annotations

import json
import pytest
from typing import Generator
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.graph.neo4j_client import Neo4jClient
from src.api.main import app

# ── Constants ─────────────────────────────────────────────────────────────────

TEST_PREFIX = "TEST-"
API_HEADERS = {"X-API-Key": "dev-api-key"}

# ── Seed data — shared across all integration test modules ────────────────────

SEED_PARTS = [
    {
        "id":          "TEST-P-001",
        "name":        "Servo Motor SM-400",
        "description": "High-torque servo motor, 400W",
        "category":    "electronic",
        "criticality": "HIGH",
        "specifications": {"power_rating": "400W", "voltage": "24V DC",
                           "certifications": ["CE", "UL", "RoHS"]},
        "unit_of_measure": "EA",
    },
    {
        "id":          "TEST-P-002",
        "name":        "Servo Motor SM-450",
        "description": "Upgraded servo motor, 450W",
        "category":    "electronic",
        "criticality": "HIGH",
        "specifications": {"power_rating": "450W", "voltage": "24V DC",
                           "certifications": ["CE", "UL", "RoHS", "ISO9001"]},
        "unit_of_measure": "EA",
    },
    {
        "id":          "TEST-P-003",
        "name":        "Mounting Bracket MB-100",
        "description": "Steel mounting bracket",
        "category":    "mechanical",
        "criticality": "MEDIUM",
        "specifications": {"material": "Steel", "load_capacity_kg": 50},
        "unit_of_measure": "EA",
    },
    {
        "id":          "TEST-P-004",
        "name":        "Controller Board CB-2000",
        "description": "Motor controller with CAN interface",
        "category":    "electronic",
        "criticality": "CRITICAL",
        "specifications": {"processor": "ARM Cortex-M4",
                           "certifications": ["CE", "UL", "RoHS", "IATF16949"]},
        "unit_of_measure": "EA",
    },
]

SEED_SUPPLIERS = [
    {
        "id":           "TEST-SUP-001",
        "name":         "Precision Motors Inc",
        "location":     "Germany",
        "certifications": ["ISO9001", "ISO14001", "IATF16949"],
        "status":       "ACTIVE",
        "tier":         1,
        "rating":       4.5,
    },
    {
        "id":           "TEST-SUP-002",
        "name":         "Asia Electronics Co",
        "location":     "Taiwan",
        "certifications": ["ISO9001", "ISO14001"],
        "status":       "ACTIVE",
        "tier":         2,
        "rating":       4.2,
    },
]

SEED_RELATIONSHIPS = [
    # TEST-SUP-001 supplies TEST-P-001 and TEST-P-002
    {"supplier_id": "TEST-SUP-001", "part_id": "TEST-P-001",
     "lead_time_days": 21, "price": 285.50, "currency": "USD",
     "valid_from": "2023-01-01"},
    {"supplier_id": "TEST-SUP-001", "part_id": "TEST-P-002",
     "lead_time_days": 28, "price": 325.00, "currency": "USD",
     "valid_from": "2024-01-01"},
    # TEST-SUP-002 also supplies TEST-P-001 (multi-source)
    {"supplier_id": "TEST-SUP-002", "part_id": "TEST-P-001",
     "lead_time_days": 35, "price": 245.00, "currency": "USD",
     "valid_from": "2023-06-01"},
    # TEST-P-003 only from TEST-SUP-002 (single-source risk)
    {"supplier_id": "TEST-SUP-002", "part_id": "TEST-P-003",
     "lead_time_days": 14, "price": 45.00, "currency": "USD",
     "valid_from": "2023-03-01"},
    # TEST-P-004 has NO supplier (critical risk)
]

SEED_COMPATIBILITY = {
    "original_part_id":  "TEST-P-001",
    "substitute_part_id": "TEST-P-002",
    "compatibility_type": "FORM_FIT_FUNCTION",
    "validation_status":  "VERIFIED",
    "validated_by":       "test@example.com",
    "validated_date":     "2024-01-15",
    "constraints":        {"requires_firmware_update": True},
    "notes":              "SM-450 is drop-in replacement",
}


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def db_client() -> Generator[Neo4jClient, None, None]:
    """
    Session-scoped Neo4j client.

    Connects once, yields, then removes all TEST- prefixed nodes.
    Tests that create their own nodes should also clean up after themselves
    if they need isolation stronger than the session teardown provides.
    """
    client = Neo4jClient()
    client.connect()
    yield client
    # Teardown — remove only test nodes
    client.execute_write(
        "MATCH (n) WHERE n.id STARTS WITH $prefix DETACH DELETE n",
        {"prefix": TEST_PREFIX},
    )
    # Remove Approver nodes created during tests
    client.execute_write(
        "MATCH (a:Approver) WHERE a.id STARTS WITH $prefix DETACH DELETE a",
        {"prefix": TEST_PREFIX},
    )
    client.close()


@pytest.fixture(scope="session")
def seed_data(db_client: Neo4jClient) -> dict:
    """
    Load test fixtures into Neo4j once per session.

    Returns the seed data dicts so tests can reference IDs without
    hardcoding strings.
    """
    # Parts
    for p in SEED_PARTS:
        db_client.create_part(
            part_id=p["id"],
            name=p["name"],
            description=p["description"],
            category=p["category"],
            criticality=p["criticality"],
            specifications=p["specifications"],
            unit_of_measure=p["unit_of_measure"],
        )

    # Suppliers
    for s in SEED_SUPPLIERS:
        db_client.create_supplier(
            supplier_id=s["id"],
            name=s["name"],
            location=s["location"],
            certifications=s["certifications"],
            status=s["status"],
            tier=s["tier"],
            rating=s["rating"],
        )

    # Supply relationships
    for r in SEED_RELATIONSHIPS:
        db_client.create_supplies_relationship(
            supplier_id=r["supplier_id"],
            part_id=r["part_id"],
            valid_from=r["valid_from"],
            lead_time_days=r["lead_time_days"],
            price=r["price"],
            currency=r["currency"],
        )

    # Compatibility
    c = SEED_COMPATIBILITY
    db_client.execute_write(
        """
        MATCH (orig:Part {id: $orig_id})
        MATCH (sub:Part  {id: $sub_id})
        CREATE (orig)-[:COMPATIBLE_WITH {
            compatibility_type:  $compat_type,
            validation_status:   $val_status,
            validated_by:        $validated_by,
            validated_date:      date($validated_date),
            constraints_json:    $constraints_json,
            notes:               $notes,
            created_at:          datetime()
        }]->(sub)
        """,
        {
            "orig_id":         c["original_part_id"],
            "sub_id":          c["substitute_part_id"],
            "compat_type":     c["compatibility_type"],
            "val_status":      c["validation_status"],
            "validated_by":    c["validated_by"],
            "validated_date":  c["validated_date"],
            "constraints_json": json.dumps(c["constraints"]),
            "notes":           c["notes"],
        },
    )

    return {
        "parts":        SEED_PARTS,
        "suppliers":    SEED_SUPPLIERS,
        "relationships": SEED_RELATIONSHIPS,
        "compatibility": SEED_COMPATIBILITY,
    }


# ── HTTP test client ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_client(db_client: Neo4jClient) -> TestClient:
    """
    FastAPI TestClient wired to the real Neo4j db_client via dependency override.

    All HTTP-layer integration tests use this fixture.
    """
    from src.api.dependencies import get_db

    def _override_get_db():
        yield db_client

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Helpers exposed to test modules ──────────────────────────────────────────

def unique_id(prefix: str) -> str:
    """Generate a unique TEST- prefixed ID safe to create and delete."""
    import uuid
    return f"TEST-{prefix}-{uuid.uuid4().hex[:8].upper()}"