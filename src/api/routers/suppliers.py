"""
Suppliers router — CRUD and disruption-impact queries.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_db, verify_token
from src.api.schemas import (
    DisruptionAssessmentResponse,
    SupplierCreate,
    SupplierResponse,
)
from src.graph.neo4j_client import Neo4jClient

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


@router.get("", response_model=List[SupplierResponse], dependencies=[Depends(verify_token)])
def list_suppliers(db: Neo4jClient = Depends(get_db)):
    """Return all suppliers."""
    query = """
        MATCH (s:Supplier)
        RETURN s.id AS id, s.name AS name, s.location AS location,
               s.certifications AS certifications, s.status AS status,
               s.tier AS tier, s.rating AS rating
        ORDER BY s.id
    """
    return db.execute_query(query)


@router.post(
    "",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_token)],
)
def create_supplier(body: SupplierCreate, db: Neo4jClient = Depends(get_db)):
    """Create a new Supplier node."""
    db.create_supplier(
        supplier_id=body.id,
        name=body.name,
        location=body.location,
        certifications=body.certifications,
        status=body.status,
        contact_info=body.contact_info,
        tier=body.tier,
        rating=body.rating,
        established_date=body.established_date,
    )
    return _fetch_supplier(body.id, db)


@router.get("/{supplier_id}", response_model=SupplierResponse, dependencies=[Depends(verify_token)])
def get_supplier(supplier_id: str, db: Neo4jClient = Depends(get_db)):
    """Return a single supplier by ID."""
    return _fetch_supplier(supplier_id, db)


@router.get(
    "/{supplier_id}/disruption",
    response_model=DisruptionAssessmentResponse,
    dependencies=[Depends(verify_token)],
)
def assess_disruption(supplier_id: str, db: Neo4jClient = Depends(get_db)):
    """
    Return an impact assessment if this supplier were disrupted —
    which parts would be affected and how critical they are.
    """
    _assert_supplier_exists(supplier_id, db)
    return db.assess_supplier_disruption(supplier_id)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _fetch_supplier(supplier_id: str, db: Neo4jClient) -> dict:
    query = """
        MATCH (s:Supplier {id: $id})
        RETURN s.id AS id, s.name AS name, s.location AS location,
               s.certifications AS certifications, s.status AS status,
               s.tier AS tier, s.rating AS rating
    """
    rows = db.execute_query(query, {"id": supplier_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id!r} not found")
    return rows[0]


def _assert_supplier_exists(supplier_id: str, db: Neo4jClient) -> None:
    rows = db.execute_query("MATCH (s:Supplier {id: $id}) RETURN s.id", {"id": supplier_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id!r} not found")


# ── AI-powered qualification ───────────────────────────────────────────────────


@router.post("/{supplier_id}/ai-qualify", dependencies=[Depends(verify_token)])
def ai_qualify_supplier(supplier_id: str, db: Neo4jClient = Depends(get_db)):
    """
    Generate a grounded AI qualification memo for a supplier.

    Claude fetches the supplier profile, all parts they supply, criticality
    breakdown, performance metrics, and alternate supplier availability from
    the graph — then writes a structured qualification memo.

    Returns the memo plus the exact graph context Claude used.
    """
    from src.ai.grounded import GroundedClient

    rows = db.execute_query("MATCH (s:Supplier {id: $id}) RETURN s.id", {"id": supplier_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id!r} not found")

    try:
        client = GroundedClient(db)
        response = client.qualify_supplier(supplier_id)
        return response.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI qualification failed: {exc}")
