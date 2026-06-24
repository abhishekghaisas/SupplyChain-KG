"""
from __future__ import annotations

Extraction router — accepts raw document text and returns structured entities
extracted by the Claude-powered neural component.

Endpoints:
  POST /extraction/extract
    Extract entities and return them. Optionally persist to Neo4j with persist=true.

  POST /extraction/extract-and-persist  (convenience alias, always persists)
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from src.api.dependencies import get_db, verify_token
from src.api.schemas import (
    ExtractionRequest,
    ExtractionResponse,
    PersistSummary,
)
from src.graph.neo4j_client import Neo4jClient
from src.ingestion.entity_extractor import ClaudeEntityExtractor


def _stable_supplier_id(name: str) -> str:
    """
    Derive a stable, canonical supplier ID from a name.

    Uses only alphanumeric characters and underscores, normalises case and
    whitespace, and limits to 30 chars so IDs stay readable.

    Examples:
      "Nordic Hydraulics AB"  → SUP-EXT-NORDIC_HYDRAULICS_AB
      "nordic hydraulics ab"  → SUP-EXT-NORDIC_HYDRAULICS_AB  (same)
      "Acme Corp."            → SUP-EXT-ACME_CORP
    """
    import re

    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper().strip())
    slug = slug.strip("_")[:30]
    return f"SUP-EXT-{slug}"


router = APIRouter(prefix="/extraction", tags=["Extraction"])


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _persist_entities(
    entities: Dict[str, Any],
    db: Neo4jClient,
    source: str,
) -> PersistSummary:
    """
    Write extracted parts, suppliers, and supply relationships into Neo4j.

    Skips records that already exist (by part ID / supplier name) rather than
    erroring, so the endpoint is safely idempotent.
    """
    parts_created = parts_skipped = 0
    suppliers_created = suppliers_skipped = 0
    errors: List[str] = []

    # ── Parts ────────────────────────────────────────────────────────────────
    for part in entities.get("parts", []):
        part_id = part.get("part_id") or part.get("id")
        if not part_id:
            errors.append(f"Part missing ID, skipped: {part.get('name', '?')}")
            continue
        try:
            existing = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": part_id})
            if existing:
                parts_skipped += 1
                logger.debug(f"Part {part_id} already exists, skipping")
                continue

            db.create_part(
                part_id=part_id,
                name=part.get("name", "Unknown"),
                description=part.get("description", ""),
                category=part.get("category", "unknown"),
                criticality=part.get("criticality", "MEDIUM"),
                specifications=part.get("specifications", {}),
                unit_of_measure=part.get("unit_of_measure", "EA"),
            )
            parts_created += 1
        except Exception as exc:
            msg = f"Failed to create part {part_id}: {exc}"
            logger.error(msg)
            errors.append(msg)

    # ── Suppliers ────────────────────────────────────────────────────────────
    # Extracted suppliers may not have an ID — derive one from the name
    for supplier in entities.get("suppliers", []):
        name = supplier.get("name", "")
        if not name:
            errors.append("Supplier missing name, skipped")
            continue

        supplier_id = supplier.get("supplier_id") or _stable_supplier_id(name)
        try:
            existing = db.execute_query(
                "MATCH (s:Supplier {id: $id}) RETURN s.id", {"id": supplier_id}
            )
            if existing:
                suppliers_skipped += 1
                logger.debug(f"Supplier {supplier_id} already exists, skipping")
                continue

            db.create_supplier(
                supplier_id=supplier_id,
                name=name,
                location=supplier.get("location", "Unknown"),
                certifications=supplier.get("certifications", []),
                status="ACTIVE",
                contact_info=supplier.get("contact_info", {}),
            )
            suppliers_created += 1
        except Exception as exc:
            msg = f"Failed to create supplier {supplier_id}: {exc}"
            logger.error(msg)
            errors.append(msg)

    # ── Supply relationships ──────────────────────────────────────────────────
    # Only attempt if both the part and supplier already exist in the graph
    for rel in entities.get("relationships", []):
        part_id = rel.get("part_id")
        supplier_name = rel.get("supplier_name", "")
        supplier_id = _stable_supplier_id(supplier_name)

        if not part_id or not supplier_name:
            continue
        try:
            # Verify both nodes exist before creating relationship
            part_exists = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": part_id})
            sup_exists = db.execute_query(
                "MATCH (s:Supplier {id: $id}) RETURN s.id", {"id": supplier_id}
            )
            if not part_exists or not sup_exists:
                logger.debug(f"Skipping relationship {supplier_id}→{part_id}: node(s) not found")
                continue

            # Idempotency: skip if relationship already exists
            rel_exists = db.execute_query(
                """
                MATCH (s:Supplier {id: $sid})-[r:SUPPLIES]->(p:Part {id: $pid})
                WHERE r.valid_to IS NULL
                RETURN r
                """,
                {"sid": supplier_id, "pid": part_id},
            )
            if rel_exists:
                continue

            from datetime import date

            db.create_supplies_relationship(
                supplier_id=supplier_id,
                part_id=part_id,
                valid_from=str(date.today()),
                lead_time_days=rel.get("lead_time_days") or 30,
                price=rel.get("price") or 0.0,
                currency=rel.get("currency", "USD"),
                source=source,
                confidence=0.8,  # Extracted data gets lower confidence than manual entry
            )
        except Exception as exc:
            msg = f"Failed to create relationship {supplier_name}→{part_id}: {exc}"
            logger.error(msg)
            errors.append(msg)

    return PersistSummary(
        parts_created=parts_created,
        suppliers_created=suppliers_created,
        parts_skipped=parts_skipped,
        suppliers_skipped=suppliers_skipped,
        errors=errors,
    )


def _build_response(
    body: ExtractionRequest,
    entities: Dict[str, Any],
    confidence: float,
    extraction_method: str,
    persist_summary: Optional[PersistSummary],
) -> ExtractionResponse:
    return ExtractionResponse(
        source=body.source,
        document_type=body.document_type,
        confidence=confidence,
        extraction_method=extraction_method,
        entities=entities,
        parts_found=len(entities.get("parts", [])),
        suppliers_found=len(entities.get("suppliers", [])),
        relationships_found=len(entities.get("relationships", [])),
        persist_summary=persist_summary,
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/extract", response_model=ExtractionResponse, dependencies=[Depends(verify_token)])
def extract_entities(
    body: ExtractionRequest,
    db: Neo4jClient = Depends(get_db),
):
    """
    Extract parts, suppliers, and supply relationships from raw document text.

    Set **persist=true** to automatically write the extracted entities into
    Neo4j. Already-existing records are skipped (idempotent).
    """
    try:
        extractor = ClaudeEntityExtractor()
        result = extractor.extract_with_direct_api(
            text=body.text,
            document_type=body.document_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {exc}") from exc

    entities = result.entities[0] if result.entities else {}

    persist_summary = None
    if body.persist:
        persist_summary = _persist_entities(entities, db, source=body.source)

    return _build_response(
        body, entities, result.confidence, result.extraction_method, persist_summary
    )


@router.post(
    "/extract-and-persist", response_model=ExtractionResponse, dependencies=[Depends(verify_token)]
)
def extract_and_persist(
    body: ExtractionRequest,
    db: Neo4jClient = Depends(get_db),
):
    """
    Convenience alias — always extracts **and** persists to Neo4j.

    Equivalent to calling /extract with persist=true.
    """
    body.persist = True
    return extract_entities(body, db)
