"""
Reasoning router — exposes the symbolic rules engine over HTTP.

Each endpoint runs one or more rules from supply_chain_rules.py,
returns structured pass/fail results with confidence, and (for
compatibility checks) includes a full provenance chain.
"""


from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_db, verify_token
from src.api.schemas import (
    CompatibilityCheckRequest,
    CompatibilityCheckResponse,
    LeadTimeCheckRequest,
    LeadTimeCheckResponse,
    RuleResultResponse,
    SupplierQualificationRequest,
    SupplierQualificationResponse,
)
from src.graph.neo4j_client import Neo4jClient
from src.reasoning import (
    LeadTimeFeasibilityRule,
    PartCompatibilityRule,
    ProvenanceTracker,
    SupplierQualificationRule,
)

router = APIRouter(prefix="/reasoning", tags=["Reasoning"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _rule_result_to_schema(r) -> RuleResultResponse:
    return RuleResultResponse(
        passed=r.passed,
        rule_name=r.rule_name,
        rule_type=r.rule_type.value,
        reason=r.reason,
        failure_severity=r.severity.value,
        confidence=r.confidence,
        details=r.details,
        facts_used=r.facts_used,
    )


def _fetch_part(part_id: str, db: Neo4jClient) -> dict:
    query = """
        MATCH (p:Part {id: $id})
        RETURN p.id AS id, p.name AS name, p.category AS category,
               p.criticality AS criticality, p.specifications_json AS specifications_json
    """
    rows = db.execute_query(query, {"id": part_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Part {part_id!r} not found")
    return rows[0]


def _fetch_supplier(supplier_id: str, db: Neo4jClient) -> dict:
    query = """
        MATCH (s:Supplier {id: $id})
        RETURN s.id AS id, s.name AS name, s.location AS location,
               s.certifications AS certifications, s.status AS status,
               s.rating AS rating
    """
    rows = db.execute_query(query, {"id": supplier_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id!r} not found")
    return rows[0]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/compatibility", response_model=CompatibilityCheckResponse,
             dependencies=[Depends(verify_token)])
def check_compatibility(body: CompatibilityCheckRequest, db: Neo4jClient = Depends(get_db)):
    """
    Run the PartCompatibilityRule against two live parts from the graph.

    Returns a pass/fail result with a provenance chain showing every
    fact that was used in the decision.
    """
    original = _fetch_part(body.original_part_id, db)
    substitute = _fetch_part(body.substitute_part_id, db)

    tracker = ProvenanceTracker(
        f"Compatibility check: {body.original_part_id} → {body.substitute_part_id}"
    )
    source_id = tracker.add_source("neo4j_knowledge_graph", "database",
                                   {"parts": [body.original_part_id, body.substitute_part_id]})

    rule = PartCompatibilityRule()
    result = rule.check(original_part=original, substitute_part=substitute, db=db)

    tracker.add_validation(
        rule_name=result.rule_name,
        passed=result.passed,
        reason=result.reason,
        details=result.details,
        parent_id=source_id,
    )
    tracker.add_decision(
        decision="COMPATIBLE" if result.passed else "NOT_COMPATIBLE",
        rationale=result.reason,
        confidence=result.confidence,
    )

    return CompatibilityCheckResponse(
        original_part_id=body.original_part_id,
        substitute_part_id=body.substitute_part_id,
        result=_rule_result_to_schema(result),
        provenance=tracker.get_summary(),
    )


@router.post("/lead-time", response_model=LeadTimeCheckResponse,
             dependencies=[Depends(verify_token)])
def check_lead_time(body: LeadTimeCheckRequest):
    """
    Check whether a supplier's lead time allows delivery by the required date.

    This rule is pure date arithmetic — no database lookup needed.
    """
    rule = LeadTimeFeasibilityRule()
    result = rule.check(
        supplier_lead_time_days=body.supplier_lead_time_days,
        required_date=body.required_date,
        order_date=body.order_date,
    )
    return LeadTimeCheckResponse(feasible=result.passed, result=_rule_result_to_schema(result))


@router.post("/qualify-supplier", response_model=SupplierQualificationResponse,
             dependencies=[Depends(verify_token)])
def qualify_supplier(body: SupplierQualificationRequest, db: Neo4jClient = Depends(get_db)):
    """
    Check whether a supplier meets the given certification and rating requirements.
    """
    supplier = _fetch_supplier(body.supplier_id, db)
    rule = SupplierQualificationRule()
    result = rule.check(
        supplier=supplier,
        required_certifications=body.required_certifications,
        min_rating=body.min_rating,
    )
    return SupplierQualificationResponse(
        supplier_id=body.supplier_id,
        qualified=result.passed,
        result=_rule_result_to_schema(result),
    )
