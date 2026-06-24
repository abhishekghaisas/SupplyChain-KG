"""
Parts router — CRUD and supplier/compatibility queries for parts.
"""

import json  # noqa: E402
from typing import List, Optional  # noqa: E402

from fastapi import APIRouter, Depends, HTTPException, Query, status  # noqa: E402

from src.api.dependencies import get_db, verify_token  # noqa: E402
from src.api.schemas import BOMUsageResponse  # noqa: E402
from src.api.schemas import (  # noqa: E402
    CompatibilityResponse,
    PartCreate,
    PartResponse,
    SupplierForPartResponse,
)
from src.graph.neo4j_client import Neo4jClient  # noqa: E402

router = APIRouter(prefix="/parts", tags=["Parts"])


@router.get("", response_model=List[PartResponse], dependencies=[Depends(verify_token)])
def list_parts(
    category: Optional[str] = Query(None, description="Filter by category"),
    criticality: Optional[str] = Query(None, description="Filter by criticality"),
    id_prefix: Optional[str] = Query(None, description="Filter by ID prefix, e.g. P-123"),
    db: Neo4jClient = Depends(get_db),
):
    """Return all parts, with optional category/criticality filters."""
    filters = []
    params: dict = {}

    if category:
        filters.append("p.category = $category")
        params["category"] = category
    if criticality:
        filters.append("p.criticality = $criticality")
        params["criticality"] = criticality
    if id_prefix:
        filters.append("p.id STARTS WITH $id_prefix")
        params["id_prefix"] = id_prefix

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = f"""
        MATCH (p:Part)
        {where}
        RETURN p.id AS id, p.name AS name, p.description AS description,
               p.category AS category, p.criticality AS criticality,
               p.specifications_json AS specifications_json,
               p.unit_of_measure AS unit_of_measure
        ORDER BY p.id
    """
    rows = db.execute_query(query, params)
    return [_row_to_part(r) for r in rows]


@router.post(
    "",
    response_model=PartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_token)],
)
def create_part(body: PartCreate, db: Neo4jClient = Depends(get_db)):
    """Create a new Part node."""
    db.create_part(
        part_id=body.id,
        name=body.name,
        description=body.description,
        category=body.category,
        criticality=body.criticality,
        specifications=body.specifications,
        unit_of_measure=body.unit_of_measure,
    )
    return _fetch_part(body.id, db)


@router.get("/{part_id}", response_model=PartResponse, dependencies=[Depends(verify_token)])
def get_part(part_id: str, db: Neo4jClient = Depends(get_db)):
    """Return a single part by ID."""
    return _fetch_part(part_id, db)


@router.get(
    "/{part_id}/suppliers",
    response_model=List[SupplierForPartResponse],
    dependencies=[Depends(verify_token)],
)
def get_part_suppliers(
    part_id: str,
    as_of: Optional[str] = Query(
        None, description="ISO date for historical query, e.g. 2023-06-01"
    ),
    db: Neo4jClient = Depends(get_db),
):
    """Return current (or historical) suppliers for a part."""
    _assert_part_exists(part_id, db)
    if as_of:
        rows = db.query_suppliers_at_date(part_id, as_of)
    else:
        rows = db.query_current_suppliers(part_id)
    return rows


@router.get(
    "/{part_id}/compatibility",
    response_model=List[CompatibilityResponse],
    dependencies=[Depends(verify_token)],
)
def get_part_compatibility(part_id: str, db: Neo4jClient = Depends(get_db)):
    """Return verified substitute parts for a given part."""
    _assert_part_exists(part_id, db)
    query = """
        MATCH (original:Part {id: $part_id})-[r:COMPATIBLE_WITH]->(sub:Part)
        RETURN original.id AS original_part_id,
               sub.id      AS substitute_part_id,
               r.compatibility_type  AS compatibility_type,
               r.validation_status   AS validation_status,
               r.validated_by        AS validated_by,
               toString(r.validated_date) AS validated_date,
               r.notes               AS notes
    """
    return db.execute_query(query, {"part_id": part_id})


@router.get(
    "/{part_id}/boms", response_model=List[BOMUsageResponse], dependencies=[Depends(verify_token)]
)
def get_part_bom_usage(part_id: str, db: Neo4jClient = Depends(get_db)):
    """Return all BOMs that contain this part."""
    _assert_part_exists(part_id, db)
    return db.get_boms_affected_by_part(part_id)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _row_to_part(row: dict) -> dict:
    specs_raw = row.get("specifications_json") or "{}"
    try:
        specs = json.loads(specs_raw)
    except (json.JSONDecodeError, TypeError):
        specs = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description", ""),
        "category": row["category"],
        "criticality": row["criticality"],
        "specifications": specs,
        "unit_of_measure": row.get("unit_of_measure"),
    }


def _fetch_part(part_id: str, db: Neo4jClient) -> dict:
    query = """
        MATCH (p:Part {id: $id})
        RETURN p.id AS id, p.name AS name, p.description AS description,
               p.category AS category, p.criticality AS criticality,
               p.specifications_json AS specifications_json,
               p.unit_of_measure AS unit_of_measure
    """
    rows = db.execute_query(query, {"id": part_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Part {part_id!r} not found")
    return _row_to_part(rows[0])


def _assert_part_exists(part_id: str, db: Neo4jClient) -> None:
    rows = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": part_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Part {part_id!r} not found")


# ── AI-powered substitute suggestion ──────────────────────────────────────────


from pydantic import BaseModel as _BaseModel  # noqa: E402


class SubstitutePersistRequest(_BaseModel):
    suggestions: list
    min_confidence: float = 0.5


@router.post("/{part_id}/suggest-substitutes", dependencies=[Depends(verify_token)])
def suggest_substitutes(
    part_id: str,
    max_candidates: int = 5,
    db: Neo4jClient = Depends(get_db),
):
    """
    Find and evaluate substitute candidates for a part using AI.

    Steps:
      1. Vector search finds semantically similar parts in the same category
      2. Claude compares specifications and produces structured reasoning
      3. Returns candidates with confidence scores and per-spec explanations

    Candidates are returned but NOT written to the graph until you call
    POST /{part_id}/persist-substitutes with the ones you want to keep.
    This gives engineers a chance to review before committing.
    """
    from src.ai.substitute_suggester import SubstituteSuggester

    rows = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": part_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Part {part_id!r} not found")

    try:
        suggester = SubstituteSuggester(db)
        suggestions = suggester.suggest(part_id, max_candidates=max_candidates)
        return {
            "part_id": part_id,
            "candidates": len(suggestions),
            "suggestions": [s.to_dict() for s in suggestions],
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Substitute analysis failed: {exc}")


@router.post("/{part_id}/persist-substitutes", dependencies=[Depends(verify_token)])
def persist_substitutes(
    part_id: str,
    body: SubstitutePersistRequest,
    db: Neo4jClient = Depends(get_db),
):
    """
    Write selected substitute suggestions to the graph as INFERRED relationships.

    Call this after reviewing the suggestions from POST /{part_id}/suggest-substitutes.
    Only suggestions with confidence >= min_confidence are written.

    Written relationships have validation_status = 'INFERRED' — they appear in
    BOM reviews and disruption analysis as "inferred substitute — requires validation."
    Engineers can then VERIFY or REJECT them via the compatibility tab.
    """
    from src.ai.substitute_suggester import (
        SubstituteSuggester,
        SubstituteSuggestion,
        SpecComparison,
    )  # noqa: E501

    rows = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": part_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Part {part_id!r} not found")

    try:
        # Reconstruct suggestion objects from the request body
        suggester = SubstituteSuggester(db)
        suggestions = []
        for s in body.suggestions:
            suggestions.append(
                SubstituteSuggestion(
                    source_part_id=s["source_part_id"],
                    source_part_name=s["source_part_name"],
                    candidate_part_id=s["candidate_part_id"],
                    candidate_part_name=s["candidate_part_name"],
                    semantic_score=s["semantic_score"],
                    confidence=s["confidence"],
                    verdict=s["verdict"],
                    summary=s["summary"],
                    spec_comparisons=[
                        SpecComparison(
                            spec_name=sc["spec"],
                            source_value=sc["source"],
                            candidate_value=sc["candidate"],
                            match=sc["match"],
                            material=sc["material"],
                            note=sc["note"],
                        )
                        for sc in s.get("spec_comparisons", [])
                    ],
                    matching_specs=s.get("matching_specs", []),
                    differing_specs=s.get("differing_specs", []),
                    reasoning=s.get("reasoning", ""),
                )
            )

        written = suggester.persist(part_id, suggestions, body.min_confidence)
        return {
            "part_id": part_id,
            "written": written,
            "message": f"Wrote {written} inferred substitute relationship(s) to the graph",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Persist failed: {exc}")
