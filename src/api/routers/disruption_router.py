"""
Disruption analysis router.

Endpoints
─────────
  GET /disruption/supplier/{supplier_id}
      Model the impact of a supplier going down.
      Returns affected BOMs with severity scores and recommended actions.

  GET /disruption/part/{part_id}
      Model the impact of a single part becoming unavailable.
      Surfaces verified substitutes and scores every affected BOM.

Query parameters (both endpoints):
  statuses  — comma-separated BOM statuses to include (default: RELEASED)
              e.g. ?statuses=RELEASED,REVIEW
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import get_db, verify_token
from src.api.schemas import DisruptionReportResponse
from src.graph.neo4j_client import Neo4jClient

router = APIRouter(prefix="/disruption", tags=["Disruption"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_statuses(statuses: Optional[str]) -> list[str]:
    """Parse comma-separated statuses query param, defaulting to RELEASED."""
    if not statuses:
        return ["RELEASED"]
    return [s.strip().upper() for s in statuses.split(",") if s.strip()]


def _serialise_report(report) -> dict:
    """Convert a DisruptionReport dataclass to a dict matching DisruptionReportResponse."""
    return {
        "scenario":              report.scenario,
        "disrupted_id":          report.disrupted_id,
        "disrupted_name":        report.disrupted_name,
        "bom_statuses":          report.bom_statuses,
        "total_parts_affected":  report.total_parts_affected,
        "summary":               report.summary,
        "affected_boms": [
            {
                "bom_id":       bom.bom_id,
                "bom_name":     bom.bom_name,
                "bom_version":  bom.bom_version,
                "bom_status":   bom.bom_status,
                "severity_score": bom.severity_score,
                "severity_label": bom.severity_label,
                "actions":      [a.value for a in bom.actions],
                "disrupted_parts": [
                    {
                        "part_id":                 dp.part_id,
                        "part_name":               dp.part_name,
                        "criticality":             dp.criticality,
                        "quantity_in_bom":         dp.quantity_in_bom,
                        "alternate_supplier_count": dp.alternate_supplier_count,
                        "has_substitute":          dp.has_substitute,
                        "substitutes": [
                            {"part_id": s.part_id, "part_name": s.part_name,
                             "compatibility_type": s.compatibility_type,
                             "notes": s.notes}
                            for s in dp.substitutes
                        ],
                    }
                    for dp in bom.disrupted_parts
                ],
            }
            for bom in report.affected_boms
        ],
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/supplier/{supplier_id}", response_model=DisruptionReportResponse,
            dependencies=[Depends(verify_token)])
def analyse_supplier_disruption(
    supplier_id: str,
    statuses: Optional[str] = Query(
        None,
        description="Comma-separated BOM statuses to include. Default: RELEASED",
        example="RELEASED,REVIEW",
    ),
    db: Neo4jClient = Depends(get_db),
):
    """
    Model the impact of a supplier becoming unavailable.

    Finds every part the supplier currently supplies, then every BOM
    (in the given statuses) containing those parts. Each BOM is scored
    for severity and given recommended actions.

    **Severity score** (0.0–1.0):
    - Based on criticality of affected parts × sourcing risk factor
    - 1.0 = CRITICAL part with no alternate supplier and no substitute

    **Recommended actions:**
    - ESCALATE — no substitute, no alternate, CRITICAL/HIGH part
    - USE_SUBSTITUTE — verified compatible substitute exists
    - EXPEDITE_ALTERNATE — other active suppliers exist
    - DUAL_SOURCE — only one supplier; recommend qualifying a second
    - MONITOR — LOW/MEDIUM criticality with some flexibility
    """
    from src.bom.disruption import DisruptionAnalyzer

    bom_statuses = _parse_statuses(statuses)

    try:
        report = DisruptionAnalyzer(db).analyze_supplier_disruption(
            supplier_id=supplier_id,
            bom_statuses=bom_statuses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Serialise first, then rerank the plain dicts (avoids mutating the dataclass)
    from src.ai.grounded import rerank_disruption_boms
    serialised = _serialise_report(report)
    serialised["affected_boms"] = rerank_disruption_boms(serialised["affected_boms"])
    return serialised


@router.get("/part/{part_id}", response_model=DisruptionReportResponse,
            dependencies=[Depends(verify_token)])
def analyse_part_disruption(
    part_id: str,
    statuses: Optional[str] = Query(
        None,
        description="Comma-separated BOM statuses to include. Default: RELEASED",
        example="RELEASED",
    ),
    db: Neo4jClient = Depends(get_db),
):
    """
    Model the impact of a single part becoming unavailable.

    Surfaces verified substitutes from the graph's COMPATIBLE_WITH
    relationships and finds every BOM containing the part.

    Useful for:
    - End-of-life part planning
    - Shortage scenario modelling
    - Substitute qualification prioritisation
    """
    from src.bom.disruption import DisruptionAnalyzer

    bom_statuses = _parse_statuses(statuses)

    try:
        report = DisruptionAnalyzer(db).analyze_part_disruption(
            part_id=part_id,
            bom_statuses=bom_statuses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    from src.ai.grounded import rerank_disruption_boms
    serialised = _serialise_report(report)
    serialised["affected_boms"] = rerank_disruption_boms(serialised["affected_boms"])
    return serialised


# ── AI-powered narrative ──────────────────────────────────────────────────────

@router.post("/ai-narrate", dependencies=[Depends(verify_token)])
def ai_narrate_disruption(
    body: dict,
    db: Neo4jClient = Depends(get_db),
):
    """
    Generate a plain-English executive summary of a disruption analysis.

    Expects a JSON body with the pre-computed disruption report:
      {
        "disrupted_id":   "SUP-001",
        "disrupted_type": "SUPPLIER",
        "report":         { ... disruption report from GET /disruption/supplier/{id} ... }
      }

    Claude writes an executive summary grounded in the report data —
    no training knowledge, only what the graph returned.
    """
    from src.ai.grounded import GroundedClient

    disrupted_id   = body.get("disrupted_id")
    disrupted_type = body.get("disrupted_type")
    report         = body.get("report")

    if not disrupted_id or not disrupted_type or not report:
        raise HTTPException(
            status_code=422,
            detail="Body must include disrupted_id, disrupted_type, and report"
        )

    try:
        client   = GroundedClient(db)
        response = client.narrate_disruption(disrupted_id, disrupted_type, report)
        return response.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI narration failed: {exc}")