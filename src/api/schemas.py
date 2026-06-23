"""
Pydantic schemas for API request and response models.
"""

from typing import Any, Dict, List, Optional
from datetime import date
from pydantic import BaseModel, Field


# ─── Parts ────────────────────────────────────────────────────────────────────

class PartCreate(BaseModel):
    id: str = Field(..., description="Unique part ID, e.g. P-12345")
    name: str
    description: str
    category: str = Field(..., description="electronic | mechanical | electrical")
    criticality: str = Field(..., description="LOW | MEDIUM | HIGH | CRITICAL")
    specifications: Dict[str, Any] = Field(default_factory=dict)
    unit_of_measure: str = Field(default="EA")


class PartResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    criticality: str
    specifications: Dict[str, Any] = {}
    unit_of_measure: Optional[str] = None

    model_config = {"from_attributes": True}


class CompatibilityResponse(BaseModel):
    original_part_id: str
    substitute_part_id: str
    compatibility_type: str
    validation_status: str
    validated_by: Optional[str] = None
    validated_date: Optional[str] = None
    notes: Optional[str] = None


# ─── Suppliers ────────────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    id: str = Field(..., description="Unique supplier ID, e.g. SUP-001")
    name: str
    location: str
    certifications: List[str] = Field(default_factory=list)
    status: str = Field(default="ACTIVE")
    tier: int = Field(default=2, ge=1, le=3)
    rating: float = Field(default=0.0, ge=0.0, le=5.0)
    contact_info: Dict[str, str] = Field(default_factory=dict)
    established_date: Optional[str] = None


class SupplierResponse(BaseModel):
    id: str
    name: str
    location: str
    certifications: List[str] = []
    status: str
    tier: Optional[int] = None
    rating: Optional[float] = None

    model_config = {"from_attributes": True}


class SupplierForPartResponse(BaseModel):
    supplier_id: str
    supplier_name: str
    location: Optional[str] = None
    lead_time_days: int
    price: float
    currency: str = "USD"
    on_time_delivery_rate: Optional[float] = None


class DisruptionAssessmentResponse(BaseModel):
    supplier_id: str
    affected_parts_count: int
    affected_parts: List[Dict[str, Any]]
    critical_parts: List[Dict[str, Any]]


# ─── Reasoning ────────────────────────────────────────────────────────────────

class RuleResultResponse(BaseModel):
    passed: bool
    rule_name: str
    rule_type: str
    reason: str
    failure_severity: str = Field(
        description="Severity that applies if this rule fails — not an indicator of the current result"
    )
    confidence: float
    details: Dict[str, Any] = {}
    facts_used: List[str] = []


class CompatibilityCheckRequest(BaseModel):
    original_part_id: str
    substitute_part_id: str


class CompatibilityCheckResponse(BaseModel):
    original_part_id: str
    substitute_part_id: str
    result: RuleResultResponse
    provenance: Optional[Dict[str, Any]] = None


class LeadTimeCheckRequest(BaseModel):
    supplier_lead_time_days: int = Field(..., ge=1)
    required_date: date
    order_date: Optional[date] = None


class LeadTimeCheckResponse(BaseModel):
    feasible: bool
    result: RuleResultResponse


class SupplierQualificationRequest(BaseModel):
    supplier_id: str
    required_certifications: List[str] = Field(default_factory=list)
    min_rating: float = Field(default=3.5, ge=0.0, le=5.0)


class SupplierQualificationResponse(BaseModel):
    supplier_id: str
    qualified: bool
    result: RuleResultResponse



# ─── BOM ──────────────────────────────────────────────────────────────────────

class ComponentCreate(BaseModel):
    part_id: str
    quantity: float = Field(..., gt=0)
    reference_designator: str = Field(default="", description="e.g. U1, R3, J2")
    unit_of_measure: str = Field(default="EA")
    notes: str = Field(default="")


class BOMCreate(BaseModel):
    id: str = Field(..., description="Unique BOM ID, e.g. BOM-001")
    name: str
    description: str = Field(default="")
    version: str = Field(default="1.0")
    status: str = Field(default="DRAFT", description="DRAFT | RELEASED | OBSOLETE")
    components: List[ComponentCreate] = Field(
        default_factory=list,
        description="Optional: supply components inline to create BOM and add parts in one call"
    )


class ComponentResponse(BaseModel):
    component_id: str
    part_id: str
    part_name: str
    category: str
    criticality: str
    quantity: float
    reference_designator: Optional[str] = None
    unit_of_measure: str = "EA"
    notes: Optional[str] = None


class BOMResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    version: str
    status: str
    component_count: Optional[int] = None
    created_at: Optional[str] = None


class BOMDetailResponse(BOMResponse):
    components: List[ComponentResponse] = []


class RiskComponentResponse(BaseModel):
    part_id: str
    part_name: str
    criticality: str
    quantity: float
    supplier_count: int
    risk_level: str  # NO_SUPPLIER | SINGLE_SOURCE | MULTI_SOURCE


class BOMRiskResponse(BaseModel):
    bom_id: str
    total_components: int
    at_risk_count: int
    components: List[RiskComponentResponse]
    at_risk_components: List[RiskComponentResponse]


class BOMUsageResponse(BaseModel):
    bom_id: str
    bom_name: str
    version: str
    status: str
    quantity: float

# ─── Extraction ───────────────────────────────────────────────────────────────

class ExtractionRequest(BaseModel):
    text: str = Field(..., min_length=10, description="Document text to extract entities from")
    document_type: str = Field(default="unknown", description="catalog | bom | price_list | purchase_order")
    source: str = Field(default="api_request")
    persist: bool = Field(
        default=False,
        description="If true, write extracted parts and suppliers into Neo4j after extraction"
    )


class ExtractedPartSummary(BaseModel):
    part_id: str
    name: str
    category: Optional[str] = None


class ExtractedSupplierSummary(BaseModel):
    name: str
    location: Optional[str] = None


class PersistSummary(BaseModel):
    parts_created: int
    suppliers_created: int
    parts_skipped: int
    suppliers_skipped: int
    errors: List[str] = []


class ExtractionResponse(BaseModel):
    source: str
    document_type: str
    confidence: float
    extraction_method: str
    entities: Dict[str, Any]
    parts_found: int
    suppliers_found: int
    relationships_found: int
    persist_summary: Optional[PersistSummary] = None


# ─── Shared ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    cache: Optional[Dict[str, Any]] = None

# ─── BOM Versioning ───────────────────────────────────────────────────────────

class BOMCloneRequest(BaseModel):
    new_bom_id:      str = Field(..., description="ID for the new BOM")
    new_version:     str = Field(..., description="Version string, e.g. '2.0'")
    new_name:        Optional[str] = Field(default=None, description="Override name; defaults to source name")
    new_description: Optional[str] = Field(default=None)
    new_status:      str = Field(default="DRAFT")
    cloned_by:       Optional[str] = Field(default=None, description="Actor ID")


class BOMCloneResponse(BaseModel):
    source_bom_id: str
    new_bom_id:    str
    new_version:   str
    new_status:    str
    cloned_by:     str


class BOMDiffResponse(BaseModel):
    bom_id_a:   str
    bom_id_b:   str
    version_a:  str
    version_b:  str
    summary:    str
    has_changes: bool
    added:    List[Dict[str, Any]]
    removed:  List[Dict[str, Any]]
    modified: List[Dict[str, Any]]


class BOMLineageResponse(BaseModel):
    bom_id:  str
    lineage: List[Dict[str, Any]]


# ─── BOM Approval ─────────────────────────────────────────────────────────────

class BOMApproveRequest(BaseModel):
    approver_id: str = Field(..., description="Identity of the approver (user ID / email)")
    notes:       str = Field(default="")


class BOMApprovalResponse(BaseModel):
    bom_id:      str
    approver_id: str
    approved_at: str
    notes:       str


class BOMTransitionRequest(BaseModel):
    to_status: str = Field(..., description="DRAFT | REVIEW | RELEASED | ARCHIVED | REJECTED")
    actor:     str = Field(..., description="Identity of the actor requesting the transition")
    notes:     str = Field(default="")


class BOMTransitionResponse(BaseModel):
    bom_id:        str
    from_status:   str
    to_status:     str
    actor:         str
    rules_passed:  Optional[bool] = None
    rules_summary: Optional[str]  = None
    approval:      Optional[str]  = None   # approver_id if approval was present


class BOMTransitionHistoryResponse(BaseModel):
    bom_id:      str
    transitions: List[Dict[str, Any]]


# ─── Disruption ───────────────────────────────────────────────────────────────

class DisruptionReportResponse(BaseModel):
    scenario:             str
    disrupted_id:         str
    disrupted_name:       str
    bom_statuses:         List[str]
    total_parts_affected: int
    summary:              str
    affected_boms:        List[Dict[str, Any]]