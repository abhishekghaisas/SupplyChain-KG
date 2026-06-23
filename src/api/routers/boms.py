"""
BOM (Bill of Materials) router — full CRUD, versioning, approval workflow, AI review.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from src.api.dependencies import get_db, verify_token
from src.api.schemas import (
    BOMApprovalResponse, BOMApproveRequest, BOMCloneRequest, BOMCloneResponse,
    BOMCreate, BOMDetailResponse, BOMDiffResponse, BOMLineageResponse,
    BOMResponse, BOMRiskResponse, BOMTransitionHistoryResponse,
    BOMTransitionRequest, BOMTransitionResponse, BOMUsageResponse,
    ComponentCreate, ComponentResponse,
)
from src.graph.neo4j_client import Neo4jClient

router = APIRouter(prefix="/boms", tags=["BOMs"])


def _assert_bom_exists(bom_id: str, db: Neo4jClient) -> None:
    if not db.get_bom(bom_id):
        raise HTTPException(status_code=404, detail=f"BOM {bom_id!r} not found")


def _build_detail(bom_id: str, db: Neo4jClient) -> dict:
    header = db.get_bom(bom_id)
    if not header:
        raise HTTPException(status_code=404, detail=f"BOM {bom_id!r} not found")
    return {**header, "components": db.get_bom_components(bom_id)}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[BOMResponse], dependencies=[Depends(verify_token)])
def list_boms(
    status: Optional[str] = Query(None),
    db: Neo4jClient = Depends(get_db),
):
    return db.list_boms(status=status)


@router.post("", response_model=BOMDetailResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(verify_token)])
def create_bom(body: BOMCreate, db: Neo4jClient = Depends(get_db)):
    db.create_bom(
        bom_id=body.id, name=body.name, description=body.description,
        version=body.version, status=body.status,
    )
    for comp in body.components:
        rows = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": comp.part_id})
        if not rows:
            raise HTTPException(status_code=422, detail=f"Part {comp.part_id!r} not found")
        db.add_bom_component(
            bom_id=body.id, part_id=comp.part_id, quantity=comp.quantity,
            reference_designator=comp.reference_designator,
            unit_of_measure=comp.unit_of_measure, notes=comp.notes,
        )
    return _build_detail(body.id, db)


@router.get("/{bom_id}", response_model=BOMDetailResponse, dependencies=[Depends(verify_token)])
def get_bom(bom_id: str, db: Neo4jClient = Depends(get_db)):
    _assert_bom_exists(bom_id, db)
    return _build_detail(bom_id, db)


@router.delete("/{bom_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(verify_token)])
def delete_bom(bom_id: str, db: Neo4jClient = Depends(get_db)):
    if not db.delete_bom(bom_id):
        raise HTTPException(status_code=404, detail=f"BOM {bom_id!r} not found")


@router.post("/{bom_id}/components", response_model=ComponentResponse,
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_token)])
def add_component(bom_id: str, body: ComponentCreate, db: Neo4jClient = Depends(get_db)):
    _assert_bom_exists(bom_id, db)
    rows = db.execute_query("MATCH (p:Part {id: $id}) RETURN p.id", {"id": body.part_id})
    if not rows:
        raise HTTPException(status_code=422, detail=f"Part {body.part_id!r} not found")
    db.add_bom_component(
        bom_id=bom_id, part_id=body.part_id, quantity=body.quantity,
        reference_designator=body.reference_designator,
        unit_of_measure=body.unit_of_measure, notes=body.notes,
    )
    components = db.get_bom_components(bom_id)
    match = next((c for c in components if c["part_id"] == body.part_id), None)
    if not match:
        raise HTTPException(status_code=500, detail="Component created but could not be retrieved")
    return match


@router.get("/{bom_id}/risk", response_model=BOMRiskResponse, dependencies=[Depends(verify_token)])
def get_bom_risk(bom_id: str, db: Neo4jClient = Depends(get_db)):
    _assert_bom_exists(bom_id, db)
    return db.get_bom_risk_assessment(bom_id)


# ── Versioning ─────────────────────────────────────────────────────────────────

@router.post("/{bom_id}/clone", response_model=BOMCloneResponse,
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_token)])
def clone_bom(bom_id: str, body: BOMCloneRequest, db: Neo4jClient = Depends(get_db)):
    from src.bom.versioning import BOMVersionManager
    _assert_bom_exists(bom_id, db)
    if db.get_bom(body.new_bom_id):
        raise HTTPException(status_code=409, detail=f"BOM {body.new_bom_id!r} already exists")
    try:
        BOMVersionManager(db).clone(
            source_bom_id=bom_id, new_bom_id=body.new_bom_id,
            new_version=body.new_version, new_name=body.new_name,
            new_description=body.new_description, new_status=body.new_status,
            cloned_by=body.cloned_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    new_bom = db.get_bom(body.new_bom_id)
    return BOMCloneResponse(
        source_bom_id=bom_id, new_bom_id=body.new_bom_id,
        new_version=body.new_version, new_status=new_bom["status"],
        cloned_by=body.cloned_by or "system",
    )


@router.get("/{bom_id}/diff/{other_bom_id}", response_model=BOMDiffResponse,
            dependencies=[Depends(verify_token)])
def diff_boms(bom_id: str, other_bom_id: str, db: Neo4jClient = Depends(get_db)):
    from src.bom.versioning import BOMVersionManager
    _assert_bom_exists(bom_id, db)
    _assert_bom_exists(other_bom_id, db)
    diff = BOMVersionManager(db).diff(bom_id, other_bom_id)
    return BOMDiffResponse(
        bom_id_a=diff.bom_id_a, bom_id_b=diff.bom_id_b,
        version_a=diff.version_a, version_b=diff.version_b,
        summary=diff.summary, has_changes=diff.has_changes,
        added=[{"part_id": c.part_id, "part_name": c.part_name,
                "criticality": c.criticality, "quantity": c.quantity,
                "unit_of_measure": c.unit_of_measure} for c in diff.added],
        removed=[{"part_id": c.part_id, "part_name": c.part_name,
                  "criticality": c.criticality, "quantity": c.quantity,
                  "unit_of_measure": c.unit_of_measure} for c in diff.removed],
        modified=[{"part_id": c.part_id, "part_name": c.part_name,
                   "changes": c.changes} for c in diff.modified],
    )


@router.get("/{bom_id}/lineage", response_model=BOMLineageResponse,
            dependencies=[Depends(verify_token)])
def get_bom_lineage(bom_id: str, db: Neo4jClient = Depends(get_db)):
    from src.bom.versioning import BOMVersionManager
    _assert_bom_exists(bom_id, db)
    lineage = BOMVersionManager(db).get_version_lineage(bom_id)
    return BOMLineageResponse(bom_id=bom_id, lineage=lineage)


# ── Approval workflow ──────────────────────────────────────────────────────────

@router.post("/{bom_id}/approve", response_model=BOMApprovalResponse,
             dependencies=[Depends(verify_token)])
def approve_bom(bom_id: str, body: BOMApproveRequest, db: Neo4jClient = Depends(get_db)):
    from src.bom.approval_workflow import BOMWorkflow
    try:
        record = BOMWorkflow(db).approve(bom_id, body.approver_id, body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return BOMApprovalResponse(
        bom_id=bom_id, approver_id=record.approver_id,
        approved_at=record.approved_at, notes=record.notes,
    )


@router.post("/{bom_id}/transition", response_model=BOMTransitionResponse,
             dependencies=[Depends(verify_token)])
def transition_bom(bom_id: str, body: BOMTransitionRequest, db: Neo4jClient = Depends(get_db)):
    from src.bom.approval_workflow import BOMWorkflow
    try:
        result = BOMWorkflow(db).transition(
            bom_id=bom_id, to_status=body.to_status,
            actor=body.actor, notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not result.success:
        raise HTTPException(status_code=422, detail=result.reason)
    return BOMTransitionResponse(
        bom_id=bom_id, from_status=result.from_status, to_status=result.to_status,
        actor=body.actor,
        rules_passed=result.rules_result.passed if result.rules_result else None,
        rules_summary=result.rules_result.summary if result.rules_result else None,
        approval=result.approval.approver_id if result.approval else None,
    )


@router.get("/{bom_id}/transitions", response_model=BOMTransitionHistoryResponse,
            dependencies=[Depends(verify_token)])
def get_bom_transitions(bom_id: str, db: Neo4jClient = Depends(get_db)):
    from src.bom.approval_workflow import BOMWorkflow
    _assert_bom_exists(bom_id, db)
    transitions = BOMWorkflow(db).get_transitions(bom_id)
    return BOMTransitionHistoryResponse(
        bom_id=bom_id,
        transitions=[
            {"transition_id": t.transition_id, "from_status": t.from_status,
             "to_status": t.to_status, "actor": t.actor,
             "timestamp": t.timestamp, "notes": t.notes}
            for t in transitions
        ],
    )


@router.get("/{bom_id}/approval", dependencies=[Depends(verify_token)])
def get_bom_approval(bom_id: str, db: Neo4jClient = Depends(get_db)):
    from src.bom.approval_workflow import BOMWorkflow
    _assert_bom_exists(bom_id, db)
    record = BOMWorkflow(db).get_approval(bom_id)
    if record is None:
        return JSONResponse(content=None)
    return BOMApprovalResponse(
        bom_id=bom_id, approver_id=record.approver_id,
        approved_at=record.approved_at, notes=record.notes,
    )


# ── AI review ─────────────────────────────────────────────────────────────────

@router.post("/{bom_id}/ai-review", dependencies=[Depends(verify_token)])
def ai_review_bom(bom_id: str, db: Neo4jClient = Depends(get_db)):
    """Generate a grounded AI review of a BOM before approval."""
    from src.ai.grounded import GroundedClient
    _assert_bom_exists(bom_id, db)
    try:
        return GroundedClient(db).review_bom(bom_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI review failed: {exc}")


# ── Used by parts router ───────────────────────────────────────────────────────

def get_part_bom_usage(part_id: str, db: Neo4jClient) -> List[BOMUsageResponse]:
    return db.get_boms_affected_by_part(part_id)