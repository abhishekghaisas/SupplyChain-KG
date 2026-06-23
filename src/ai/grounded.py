"""
Grounded AI — RAG foundation for all Claude-powered features.

Core principle
──────────────
Claude is grounded in graph data, not in training knowledge.
Every call to Claude in this system follows this pattern:

  1. Fetch relevant facts from Neo4j (graph context)
  2. Inject facts into a structured system prompt
  3. Instruct Claude to reason ONLY from the provided data
  4. Return both the response AND the exact context used (auditability)

Claude is explicitly told to:
  - Cite specific IDs, names, and values from the context
  - Say "not on record" when data is absent — never guess
  - Flag data gaps as risks rather than filling them with assumptions

This module provides:
  GroundedContext    — dataclass holding fetched graph data + metadata
  GroundedResponse   — dataclass holding Claude's response + audit trail
  GroundedClient     — the main class; one method per feature type

Usage:
  from src.ai.grounded import GroundedClient
  from src.graph.neo4j_client import Neo4jClient

  with Neo4jClient() as db:
      client = GroundedClient(db)
      response = client.review_bom("BOM-001")
      print(response.content)
      print(response.context_used)   # exact data Claude saw
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from loguru import logger

from src.config import get_settings


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class GroundedContext:
    """
    Graph data fetched before a Claude call.

    Stored alongside the response so every AI output is fully auditable:
    "Claude said X because it saw Y data from the graph."
    """
    subject:      str                    # e.g. "BOM-001", "SUP-001"
    subject_type: str                    # "bom", "supplier", "disruption"
    data:         Dict[str, Any]         # raw graph data injected into prompt
    fetched_at:   str = field(default_factory=lambda: datetime.utcnow().isoformat())
    data_sources: List[str] = field(default_factory=list)  # which queries ran


@dataclass
class GroundedResponse:
    """
    Claude's response with full audit trail.

    content       — the generated text
    context_used  — exact graph data Claude was given (for verification)
    model         — which Claude model ran
    prompt_tokens — input token count (cost tracking)
    output_tokens — output token count (cost tracking)
    """
    content:       str
    context_used:  GroundedContext
    model:         str
    prompt_tokens:        int
    output_tokens:        int
    cache_tokens_written: int = 0   # tokens written to Anthropic cache
    cache_tokens_read:    int = 0   # tokens served from cache (cheaper)
    generated_at:  str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    @property
    def cache_savings_pct(self) -> float:
        """Approximate % cost saved by prompt caching on this call."""
        if self.cache_tokens_read == 0:
            return 0.0
        # Cache reads cost ~10% of normal; savings = 90% of cached tokens
        saved = self.cache_tokens_read * 0.9
        total_without_cache = self.prompt_tokens + self.cache_tokens_read
        return round(saved / total_without_cache * 100, 1) if total_without_cache else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content":       self.content,
            "model":         self.model,
            "generated_at":  self.generated_at,
            "token_usage":   {
                "prompt":              self.prompt_tokens,
                "output":              self.output_tokens,
                "total":               self.total_tokens,
                "cache_tokens_written": self.cache_tokens_written,
                "cache_tokens_read":    self.cache_tokens_read,
                "cache_savings_pct":    self.cache_savings_pct,
            },
            "context": {
                "subject":      self.context_used.subject,
                "subject_type": self.context_used.subject_type,
                "fetched_at":   self.context_used.fetched_at,
                "data_sources": self.context_used.data_sources,
                "data":         self.context_used.data,
            },
        }


# ── Grounded client ───────────────────────────────────────────────────────────

class GroundedClient:
    """
    Claude client that grounds every response in Neo4j graph data.

    All methods follow the same pattern:
      1. Fetch context from the graph
      2. Build a structured prompt with injected data
      3. Call Claude with explicit grounding instructions
      4. Return GroundedResponse with full audit trail
    """

    # System prompt preamble applied to every call — the grounding contract
    _GROUNDING_PREAMBLE = """You are a supply chain intelligence assistant with access to a live knowledge graph.

CRITICAL RULES — you must follow these without exception:
1. Base ALL your analysis ONLY on the data provided in this prompt.
2. Never use your training knowledge to fill in supply chain facts (prices, lead times, certifications, supplier names, part specifications). If a fact is not in the provided data, say it is "not on record."
3. When you cite a fact, reference the specific ID, name, or value from the data (e.g. "P-12345 (Servo Motor SM-400)" not just "the servo motor").
4. If critical data is missing or incomplete, flag it explicitly as a risk or gap — never assume or guess.
5. Be concise and direct. This analysis will be read by engineers making purchasing and approval decisions.

GRAPH SCHEMA (for your reference — all data in this prompt comes from this schema):

Node types and properties:

Part
  id              — unique identifier, e.g. "P-12345"
  name            — human-readable name, e.g. "Servo Motor SM-400"
  description     — detailed description
  category        — "electronic" | "mechanical" | "electrical" | "software" | "other"
  criticality     — "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  unit_of_measure — e.g. "EA" (each)
  specifications_json — JSON blob of technical specifications

Supplier
  id              — unique identifier, e.g. "SUP-001"
  name            — company name
  location        — country or region, e.g. "Germany"
  certifications  — list of quality certifications, e.g. ["ISO9001", "IATF16949"]
  status          — "ACTIVE" | "INACTIVE" | "PROBATION"
  tier            — integer 1, 2, or 3 (1 = direct, 3 = sub-tier)
  rating          — float 0.0 to 5.0 (overall supplier rating)

BOM (Bill of Materials)
  id              — unique identifier, e.g. "BOM-001"
  name            — descriptive name
  version         — version string, e.g. "1.0", "2.0"
  status          — "DRAFT" | "REVIEW" | "RELEASED" | "ARCHIVED" | "REJECTED"
  description     — optional description

Component (junction node between BOM and Part)
  component_id    — unique identifier
  quantity        — numeric quantity required
  unit_of_measure — e.g. "EA"
  reference_designator — engineering reference, e.g. "M1", "C1"
  notes           — optional notes

Transition (BOM status change record)
  transition_id   — unique identifier
  from_status     — previous status
  to_status       — new status
  actor           — who made the change (email or system)
  timestamp       — ISO datetime string
  notes           — optional notes

Relationship types:

(Supplier)-[:SUPPLIES {
  valid_from         — Date when this supply relationship started
  valid_to           — Date when it ended (NULL = currently active)
  lead_time_days     — typical lead time in days
  price              — unit price
  currency           — ISO currency code, e.g. "USD"
  min_order_quantity — minimum order quantity
  on_time_delivery_rate — float 0.0 to 1.0 (historical on-time rate)
  quality_rating     — float 0.0 to 5.0 (quality assessment)
  source             — source document or system for this data
  confidence         — float 0.0 to 1.0 (data confidence level)
}]->(Part)

(BOM)-[:CONTAINS]->(Component)-[:REFERENCES]->(Part)

(Part)-[:COMPATIBLE_WITH {
  compatibility_type  — e.g. "FORM_FIT_FUNCTION" | "FUNCTIONAL"
  validation_status   — "VERIFIED" | "PENDING" | "REJECTED"
  validated_by        — engineer email or system
  validated_date      — ISO date string
  notes               — substitution notes and constraints
}]->(Part)   [Part can substitute for the source Part]

(BOM)-[:HAS_TRANSITION]->(Transition)

RISK CLASSIFICATION REFERENCE:
  CRITICAL part with 0 suppliers  → CRITICAL risk
  CRITICAL part with 1 supplier   → HIGH risk (single source)
  HIGH part with 1 supplier       → MEDIUM-HIGH risk
  Any part with 0 verified substitutes and 1 supplier → elevated risk
  Supplier rating < 3.5           → qualification concern
  On-time delivery rate < 0.85    → delivery reliability concern
  Lead time > 45 days             → supply continuity concern

OUTPUT FORMATTING RULES:
  - Always begin with a one-line summary or recommendation in bold
  - Use ## for major sections, ### for sub-sections
  - Reference part IDs in the format: P-12345 (Part Name)
  - Reference supplier IDs in the format: SUP-001 (Supplier Name)
  - For risk levels use: CRITICAL / HIGH / MEDIUM / LOW (uppercase)
  - When data is absent, write: "not on record" (never omit or guess)
  - Bullet points for lists; prose for explanations
  - Maximum response length: concise enough for an engineer to read in 2 minutes

EXTENDED ANALYSIS GUIDELINES:

BOM Review:
  Assess these dimensions for every BOM review:
  1. Supply resilience: Does every component have at least two active suppliers?
     Single-source components represent a critical risk for HIGH and CRITICAL parts.
  2. Substitute availability: For each CRITICAL or HIGH part, is there a VERIFIED
     compatible substitute? Absent substitute means no fallback if the part fails.
  3. Lead time exposure: Components with lead times over 30 days reduce agility.
     Note this especially for CRITICAL components.
  4. Certification coverage: Suppliers of CRITICAL parts must hold required certs
     (ISO9001, IATF16949). Missing certifications are a qualification gap.
  5. Delivery reliability: Suppliers with on_time_delivery_rate below 0.85 have
     late delivery patterns. Flag for sole-source CRITICAL/HIGH parts.
  6. Status appropriateness: A BOM with single-source CRITICAL parts should not
     proceed to RELEASED without explicit risk acknowledgment.

Supplier Qualification:
  1. Active status only: INACTIVE or PROBATION requires escalation.
  2. Certification completeness: Compare recorded certs to part requirements.
     ISO9001 baseline; automotive needs IATF16949; aerospace needs AS9100.
     No recorded certs = data gap, treat as unknown risk.
  3. Rating thresholds: Below 3.5 = significant concern. 3.5-4.0 = monitor.
     Above 4.5 = excellent.
  4. Single-source exposure: If this supplier is sole source for any CRITICAL
     or HIGH part, state which parts and which BOMs are affected.
  5. Geographic concentration: All parts from one region = concentration risk.
  6. Tier classification: Tier 3 suppliers in critical path should be elevated.

Disruption Narrative:
  1. Lead with worst-case immediate impact.
  2. Distinguish immediate actions from longer-term remediation.
  3. For each BOM: is production at risk today / this week / this quarter?
  4. ESCALATE = no substitute and no alternate supplier — list these first.
  5. Data gaps (no substitute, no alternate on record) = HIGH risk until proven safe.
  6. End with a prioritised action list: first, second, third action.

Natural Language Query Interpretation:
  1. State total result count before listing items.
  2. Group by criticality/severity; highest-risk first.
  3. If empty: explain whether this is good news or a data gap.
  4. Connect findings to business impact: "P-33333 has only one supplier —
     if American Components LLC is disrupted, BOM-001 cannot be fulfilled."
  5. No generic caveats — be specific and actionable.

COMMON CYPHER PATTERNS FOR REFERENCE:

Single-source parts:
  MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
  WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
  WITH p, COUNT(DISTINCT s) AS supplier_count
  WHERE supplier_count = 1
  RETURN p.id, p.name, p.criticality

Parts without verified substitutes:
  MATCH (p:Part)
  WHERE NOT EXISTS {
    (p)-[:COMPATIBLE_WITH {validation_status: 'VERIFIED'}]->(:Part)
  }
  RETURN p.id, p.name, p.criticality

High lead time (over 30 days):
  MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
  WHERE r.valid_to IS NULL AND r.lead_time_days > 30
  RETURN p.id, p.name, p.criticality, s.name, r.lead_time_days
  ORDER BY r.lead_time_days DESC

BOMs with critical single-source parts:
  MATCH (b:BOM)-[:CONTAINS]->(:Component)-[:REFERENCES]->(p:Part)
  WHERE p.criticality IN ['CRITICAL', 'HIGH']
  WITH b, p
  MATCH (s:Supplier)-[r:SUPPLIES]->(p)
  WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
  WITH b, p, COUNT(DISTINCT s) AS supplier_count
  WHERE supplier_count = 1
  RETURN b.id, b.name, b.status, p.id, p.name, p.criticality

Supplier performance with quality ratings:
  MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
  WHERE r.valid_to IS NULL AND r.quality_rating IS NOT NULL
  RETURN s.id, s.name, p.id, p.name, r.quality_rating,
         r.on_time_delivery_rate, r.lead_time_days
  ORDER BY r.quality_rating DESC

DATA QUALITY NOTES:
  - quality_rating and on_time_delivery_rate may be null for newly extracted
    suppliers (IDs starting SUP-EXT-). Treat null as "not on record."
  - specifications_json is a JSON string; parse to read individual spec values.
  - certifications is a list; use ANY() or IN for membership checks in Cypher.
  - valid_to IS NULL = currently active supply relationship.
  - BOM status transitions are ordered by timestamp; latest = current status.
  - Suppliers extracted from documents use SUP-EXT- prefix; manually entered
    suppliers use SUP-NNN format. Both are valid and queryable the same way.

RESPONSE QUALITY CHECKLIST:
  Every part cited: P-12345 (Servo Motor SM-400) — ID and name
  Every supplier cited: SUP-001 (Precision Motors Inc) — ID and name
  Missing data: say "not on record" not omit
  Risk levels: CRITICAL / HIGH / MEDIUM / LOW in uppercase
  First sentence: key finding, not background
  No training knowledge facts — only graph data provided
  Actions: specific, ordered by priority, assigned where inferable

SUPPLY CHAIN DOMAIN KNOWLEDGE:

RISK LEVELS BY CRITICALITY AND SUPPLIER COUNT:
  CRITICAL part + 0 suppliers + no substitute  → CRITICAL RISK (escalate immediately)
  CRITICAL part + 1 supplier  + no substitute  → HIGH RISK (dual-source urgently)
  CRITICAL part + 1 supplier  + substitute     → MEDIUM RISK (validate substitute)
  CRITICAL part + 2+ suppliers                 → LOW-MEDIUM RISK (adequate)
  HIGH part    + 0 suppliers                   → HIGH RISK
  HIGH part    + 1 supplier                    → MEDIUM RISK
  HIGH part    + 2+ suppliers                  → LOW RISK
  MEDIUM/LOW   + any suppliers                 → LOW RISK

SUPPLIER PERFORMANCE THRESHOLDS:
  on_time_delivery_rate >= 0.95  Excellent
  on_time_delivery_rate >= 0.85  Acceptable
  on_time_delivery_rate >= 0.75  Monitor closely
  on_time_delivery_rate <  0.75  Corrective action required
  quality_rating >= 4.5          Preferred supplier
  quality_rating >= 3.5          Qualified supplier
  quality_rating <  3.5          Probationary or re-qualify
  lead_time_days <= 14           Short (low supply risk)
  lead_time_days <= 30           Standard
  lead_time_days <= 60           Long (plan ahead)
  lead_time_days >  60           Critical (safety stock required)

BOM APPROVAL CRITERIA:
  APPROVE if:
    All CRITICAL and HIGH parts have 2+ active suppliers OR verified substitutes
    No supplier has on_time_delivery_rate < 0.75 for CRITICAL parts
    All suppliers of CRITICAL parts hold required quality certifications
  FLAG FOR REVIEW if:
    Any CRITICAL part has exactly 1 supplier with no verified substitute
    Any supplier for a CRITICAL part has quality_rating < 4.0
    Any CRITICAL part has lead_time_days > 45
    Certification data is missing for suppliers of CRITICAL parts
  REJECT if:
    Any CRITICAL part has 0 suppliers and no verified substitute
    A key supplier has INACTIVE or PROBATION status

SUPPLIER QUALIFICATION CRITERIA:
  QUALIFIED: status=ACTIVE, rating>=3.5, ISO9001, on_time>=0.85
  CONDITIONALLY QUALIFIED: rating 3.0-3.5, missing one non-critical cert, on_time 0.75-0.85
  NOT QUALIFIED: status!=ACTIVE, rating<3.0, missing critical cert, on_time<0.75

DISRUPTION RESPONSE PLAYBOOK:
  ESCALATE: No substitute, no alternate for CRITICAL/HIGH → emergency sourcing
  USE_SUBSTITUTE: Verified substitute exists → update BOM, notify engineering
  EXPEDITE_ALTERNATE: Alternate supplier exists → place expedite order
  DUAL_SOURCE: Single-sourced but risk acceptable → qualify second supplier (3-6 months)
  MONITOR: LOW/MEDIUM risk → standard monitoring, no immediate action

CERTIFICATION REFERENCE:
  ISO9001    Quality management (universal baseline)
  ISO14001   Environmental management
  IATF16949  Automotive quality (required for automotive supply chain)
  AS9100     Aerospace quality (required for aerospace parts)
  ITAR       International Traffic in Arms (US defense/aerospace)
  RoHS       Restriction of Hazardous Substances (EU electronics)
  CE         European market access
  UL         North American safety certification
  ISO13485   Medical devices quality management

COMMON METRICS TO INCLUDE IN ANALYSIS:
  Supplier concentration: percentage of parts with single-source dependency
  Critical coverage: percentage of CRITICAL parts with 2+ active suppliers
  Substitute coverage: percentage of CRITICAL/HIGH parts with verified substitutes
  Average lead time by criticality tier
  Supplier rating distribution across the portfolio
  On-time delivery rate weighted by part criticality

EXTENDED CYPHER QUERY PATTERNS FOR REFERENCE:

-- Parts with no active supplier:
MATCH (p:Part)
WHERE NOT EXISTS {
  MATCH (s:Supplier)-[r:SUPPLIES]->(p)
  WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
}
RETURN p.id, p.name, p.criticality
ORDER BY p.criticality DESC

-- Single-source CRITICAL and HIGH parts:
MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
  AND p.criticality IN ['CRITICAL', 'HIGH']
WITH p, COUNT(DISTINCT s) AS supplier_count
WHERE supplier_count = 1
RETURN p.id, p.name, p.criticality, supplier_count

-- Parts with verified substitutes:
MATCH (p:Part)-[r:COMPATIBLE_WITH]->(sub:Part)
WHERE r.validation_status = 'VERIFIED'
RETURN p.id, p.name, sub.id AS substitute_id, sub.name AS substitute_name,
       r.compatibility_type, r.confidence

-- Supplier performance summary:
MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
RETURN s.id, s.name, s.location, s.rating,
       AVG(r.quality_rating) AS avg_quality,
       AVG(r.on_time_delivery_rate) AS avg_delivery,
       COUNT(DISTINCT p) AS parts_supplied
ORDER BY avg_quality DESC

-- BOMs at risk (contain single-source CRITICAL/HIGH parts):
MATCH (b:BOM)-[:CONTAINS]->(:Component)-[:REFERENCES]->(p:Part)
WHERE p.criticality IN ['CRITICAL', 'HIGH']
MATCH (s:Supplier)-[r:SUPPLIES]->(p)
WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
WITH b, p, COUNT(DISTINCT s) AS supplier_count
WHERE supplier_count <= 1
RETURN b.id, b.name, b.status,
       COLLECT(p.id + ' (' + p.criticality + ')') AS at_risk_parts

-- Lead time exposure for CRITICAL parts:
MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
WHERE r.valid_to IS NULL AND p.criticality = 'CRITICAL'
RETURN p.id, p.name, s.name AS supplier, r.lead_time_days
ORDER BY r.lead_time_days DESC

-- Supplier geographic concentration:
MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part)
WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
  AND p.criticality IN ['CRITICAL', 'HIGH']
RETURN s.location, COUNT(DISTINCT s) AS suppliers,
       COUNT(DISTINCT p) AS parts_supplied
ORDER BY parts_supplied DESC"""

    def __init__(self, db, model: Optional[str] = None):
        """
        Args:
            db:    Connected Neo4jClient instance.
            model: Claude model to use. Defaults to config claude_model.
        """
        settings = get_settings()
        self.db      = db
        self.model   = model or settings.claude_model
        self.client  = Anthropic(api_key=settings.anthropic_api_key)

    def _call(
        self,
        context:    GroundedContext,
        user_prompt: str,
        max_tokens:  int = 1500,
    ) -> GroundedResponse:
        """
        Core call with prompt caching.

        System prompt split into two blocks:
          1. _GROUNDING_PREAMBLE  - static, cache_control=ephemeral.
             Cache hits cost ~10% of normal input token price. TTL 5 min.
          2. Graph data           - dynamic per subject, not cached.
        """
        context_json = json.dumps(context.data, indent=2, default=str)

        graph_data_block = (
            "--- GRAPH DATA (ground truth - reason only from this) ---\n"
            f"Subject: {context.subject} ({context.subject_type})\n"
            f"Fetched: {context.fetched_at}\n\n"
            f"{context_json}\n"
            "--- END GRAPH DATA ---"
        )

        logger.info(
            f"Grounded call: subject={context.subject!r} "
            f"type={context.subject_type} model={self.model}"
        )

        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self._GROUNDING_PREAMBLE,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": graph_data_block,
                },
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )

        response_text = message.content[0].text

        cache_tokens_written = getattr(message.usage, "cache_creation_input_tokens", 0) or 0
        cache_tokens_read    = getattr(message.usage, "cache_read_input_tokens", 0) or 0

        logger.info(
            f"Grounded response: {message.usage.input_tokens} in / "
            f"{message.usage.output_tokens} out / "
            f"cache_write={cache_tokens_written} cache_read={cache_tokens_read}"
        )

        return GroundedResponse(
            content=response_text,
            context_used=context,
            model=self.model,
            prompt_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_tokens_written=cache_tokens_written,
            cache_tokens_read=cache_tokens_read,
        )

    # ── Context fetchers ──────────────────────────────────────────────────────

    def _fetch_bom_context(self, bom_id: str) -> GroundedContext:
        """Fetch complete BOM data: header, components, risk, approval history."""
        sources = []

        # BOM header
        bom_rows = self.db.execute_query(
            "MATCH (b:BOM {id: $id}) RETURN b", {"id": bom_id}
        )
        sources.append("BOM header")
        bom = dict(bom_rows[0]["b"]) if bom_rows else {}

        # Components with part details
        components = self.db.execute_query(
            """
            MATCH (b:BOM {id: $id})-[:CONTAINS]->(c:Component)-[:REFERENCES]->(p:Part)
            RETURN p.id AS part_id, p.name AS part_name,
                   p.criticality AS criticality, p.category AS category,
                   c.quantity AS quantity, c.unit_of_measure AS unit_of_measure,
                   c.reference_designator AS reference_designator
            ORDER BY p.criticality DESC
            """,
            {"id": bom_id},
        )
        sources.append("BOM components")

        # Supplier coverage per part
        supplier_coverage = []
        for comp in components:
            suppliers = self.db.execute_query(
                """
                MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: $part_id})
                WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
                RETURN s.id AS supplier_id, s.name AS supplier_name,
                       r.lead_time_days AS lead_time_days, r.price AS price
                """,
                {"part_id": comp["part_id"]},
            )
            substitutes = self.db.execute_query(
                """
                MATCH (p:Part {id: $part_id})-[r:COMPATIBLE_WITH]->(sub:Part)
                WHERE r.validation_status = 'VERIFIED'
                RETURN sub.id AS substitute_id, sub.name AS substitute_name,
                       r.compatibility_type AS compatibility_type
                """,
                {"part_id": comp["part_id"]},
            )
            supplier_coverage.append({
                **dict(comp),
                "suppliers":   suppliers,
                "substitutes": substitutes,
            })
        sources.append("Supplier coverage per component")

        # Approval and transition history
        transitions = self.db.execute_query(
            """
            MATCH (b:BOM {id: $id})-[:HAS_TRANSITION]->(t:Transition)
            RETURN t.from_status AS from_status, t.to_status AS to_status,
                   t.actor AS actor, t.timestamp AS timestamp
            ORDER BY t.timestamp
            """,
            {"id": bom_id},
        )
        sources.append("Transition history")

        return GroundedContext(
            subject=bom_id,
            subject_type="bom",
            data={
                "bom":               bom,
                "component_count":   len(components),
                "components":        supplier_coverage,
                "transitions":       transitions,
            },
            data_sources=sources,
        )

    def _fetch_supplier_context(self, supplier_id: str) -> GroundedContext:
        """Fetch supplier profile + all parts they supply + BOM exposure."""
        sources = []

        supplier_rows = self.db.execute_query(
            "MATCH (s:Supplier {id: $id}) RETURN s", {"id": supplier_id}
        )
        sources.append("Supplier profile")
        supplier = dict(supplier_rows[0]["s"]) if supplier_rows else {}

        parts_supplied = self.db.execute_query(
            """
            MATCH (s:Supplier {id: $id})-[r:SUPPLIES]->(p:Part)
            WHERE r.valid_to IS NULL
            RETURN p.id AS part_id, p.name AS part_name,
                   p.criticality AS criticality, p.category AS category,
                   r.lead_time_days AS lead_time_days, r.price AS price,
                   r.on_time_delivery_rate AS on_time_delivery_rate,
                   r.quality_rating AS quality_rating
            ORDER BY p.criticality DESC
            """,
            {"id": supplier_id},
        )
        sources.append("Parts supplied")

        # Alternative suppliers for each part
        for part in parts_supplied:
            alts = self.db.execute_query(
                """
                MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: $part_id})
                WHERE r.valid_to IS NULL AND s.id <> $supplier_id AND s.status = 'ACTIVE'
                RETURN s.id AS alt_supplier_id, s.name AS alt_supplier_name
                """,
                {"part_id": part["part_id"], "supplier_id": supplier_id},
            )
            part["alternate_suppliers"] = alts
        sources.append("Alternate suppliers per part")

        return GroundedContext(
            subject=supplier_id,
            subject_type="supplier",
            data={
                "supplier":      supplier,
                "parts_supplied": parts_supplied,
                "parts_count":   len(parts_supplied),
            },
            data_sources=sources,
        )

    def _fetch_disruption_context(
        self, disrupted_id: str, disrupted_type: str, report: Dict[str, Any]
    ) -> GroundedContext:
        """Wrap a pre-computed disruption report as grounded context."""
        return GroundedContext(
            subject=disrupted_id,
            subject_type="disruption",
            data={
                "disrupted_id":   disrupted_id,
                "disrupted_type": disrupted_type,
                "report":         report,
            },
            data_sources=["disruption_analysis_report"],
        )

    # ── Public AI methods ─────────────────────────────────────────────────────

    def review_bom(self, bom_id: str) -> GroundedResponse:
        """
        Write a structured pre-approval BOM review grounded in graph data.

        Covers: component risk, supplier coverage, single-source parts,
        missing certifications, and a clear approve/flag recommendation.
        """
        context = self._fetch_bom_context(bom_id)

        prompt = """Write a structured pre-approval BOM review.

Use exactly this format:

## BOM Review: {bom_id}

**Overall recommendation:** APPROVE / FLAG FOR REVIEW / REJECT
(one line, direct)

### Supply Risk Summary
List each component. For each one state:
- Part ID and name
- Criticality
- Number of active suppliers
- Whether a verified substitute exists
- Risk level: LOW / MEDIUM / HIGH / CRITICAL

### Critical Findings
Bullet points. Only include genuine risks — missing suppliers, single-source
CRITICAL/HIGH parts, no substitute on record, certifications not in graph.
If there are none, say "No critical findings."

### Data Gaps
List any components where supplier data, certifications, or substitute
information is absent from the graph. These are unknown risks.

### Recommendation
Two to three sentences. Direct. Reference specific part IDs and supplier names
from the data.""".format(bom_id=bom_id)

        return self._call(context, prompt, max_tokens=1200)

    def qualify_supplier(self, supplier_id: str) -> GroundedResponse:
        """
        Write a supplier qualification memo grounded in graph data.

        Covers: certifications, rating, parts supplied, criticality exposure,
        single-source risk, and a qualification recommendation.
        """
        context = self._fetch_supplier_context(supplier_id)

        prompt = """Write a supplier qualification memo.

Use exactly this format:

## Supplier Qualification: {supplier_id}

**Qualification status:** QUALIFIED / CONDITIONALLY QUALIFIED / NOT QUALIFIED

### Profile
- Name, location, tier, rating (from graph data only)
- Certifications on record (list them; note if none are recorded)

### Supply Exposure
- Number of parts supplied
- Criticality breakdown (how many CRITICAL / HIGH / MEDIUM / LOW parts)
- Parts where this supplier is the ONLY active source (single-source risk)

### Performance Indicators
Lead times and quality ratings from the graph. If not on record, say so.

### Risk Assessment
What happens if this supplier becomes unavailable? Which BOMs are affected?
Which parts have no alternate source?

### Recommendation
Two to three sentences. Reference specific part IDs and criticality levels.""".format(
            supplier_id=supplier_id
        )

        return self._call(context, prompt, max_tokens=1200)

    def narrate_disruption(
        self,
        disrupted_id:   str,
        disrupted_type: str,
        report:         Dict[str, Any],
    ) -> GroundedResponse:
        """
        Write a plain-English executive summary of a disruption analysis.

        Takes the pre-computed disruption report (from DisruptionAnalyzer)
        and narrates it for a non-technical audience.
        """
        context = self._fetch_disruption_context(disrupted_id, disrupted_type, report)

        entity_label = "supplier" if disrupted_type == "SUPPLIER" else "part"

        prompt = f"""Write a plain-English executive summary of this disruption scenario.

The audience is a supply chain manager who needs to act quickly.

Use exactly this format:

## Disruption Alert: {{disrupted_id}}

**Scenario:** If {entity_label} {{disrupted_id}} becomes unavailable

**Immediate impact:** One sentence — how many BOMs affected, highest severity.

### Affected BOMs
For each affected BOM (highest severity first):
- BOM name and ID
- Severity: LOW / MEDIUM / HIGH / CRITICAL
- Which specific parts are disrupted
- What mitigation is available (substitutes, alternate suppliers)
- Recommended action (from the report's action list)

### Mitigation Summary
What can be done right now vs. what requires longer-term action.
Reference specific part IDs and supplier names from the data.

### Data Gaps
Any affected parts where no substitute and no alternate supplier exists.
These are the highest-priority items to address.""".format(
            disrupted_id=disrupted_id
        )

        return self._call(context, prompt, max_tokens=1500)




# ── Reranking ─────────────────────────────────────────────────────────────────

_CRITICALITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
_SEVERITY_RANK    = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def rerank_search_results(
    results: List[Dict],
    query:   str,
    boost_entity_type: Optional[str] = None,
) -> List[Dict]:
    """
    Rerank semantic search results by composite score.

    Score = semantic_score
            + criticality_bonus  (parts/suppliers with higher criticality rank higher)
            + entity_type_bonus  (boost a specific type if requested)

    This ensures a search for "critical motor" surfaces CRITICAL parts
    above LOW parts even if they have similar semantic scores.
    """
    def _score(r: Dict) -> float:
        base      = float(r.get("score", 0.0))
        crit_rank = _CRITICALITY_RANK.get(
            (r.get("data") or {}).get("criticality", ""), 0
        )
        type_bonus = 0.05 if (boost_entity_type and r.get("entity_type") == boost_entity_type) else 0.0
        return base + (crit_rank * 0.02) + type_bonus

    return sorted(results, key=_score, reverse=True)


def rerank_nl_query_rows(
    rows:     List[Dict],
    question: str,
) -> List[Dict]:
    """
    Rerank NL query result rows before Claude interprets them.

    Applies domain heuristics:
    - CRITICAL/HIGH parts and their data float to the top
    - Single-source indicators (supplier_count=1) get a boost
    - Rows with more non-null fields rank higher (more data = more useful)
    """
    def _score(row: Dict) -> float:
        score = 0.0
        vals  = list(row.values())

        # Criticality bonus
        for v in vals:
            if isinstance(v, str):
                score += _CRITICALITY_RANK.get(v.upper(), 0) * 0.1

        # Single-source risk bonus
        for k, v in row.items():
            if "count" in k.lower() and isinstance(v, (int, float)) and v == 1:
                score += 0.3
            if "criticality" in k.lower() and isinstance(v, str):
                score += _CRITICALITY_RANK.get(v.upper(), 0) * 0.2

        # Data completeness bonus
        non_null = sum(1 for v in vals if v is not None)
        score += non_null * 0.01

        return score

    return sorted(rows, key=_score, reverse=True)


def rerank_disruption_boms(affected_boms: List[Dict]) -> List[Dict]:
    """
    Rerank disruption-affected BOMs by composite urgency score.

    Score = severity_score
            × (1 + 0.3 if ESCALATE in actions)
            × (1 + critical_parts_ratio)

    BOMs that need immediate escalation and have a high proportion of
    critical/high parts float to the top regardless of raw severity score.
    """
    def _score(bom: Dict) -> float:
        base     = float(bom.get("severity_score", 0.0))
        actions  = bom.get("actions", [])
        parts    = bom.get("disrupted_parts", [])

        escalate_bonus = 0.3 if "ESCALATE" in actions else 0.0

        if parts:
            critical_count = sum(
                1 for p in parts
                if p.get("criticality") in ("CRITICAL", "HIGH")
            )
            critical_ratio = critical_count / len(parts)
        else:
            critical_ratio = 0.0

        return base * (1 + escalate_bonus) * (1 + critical_ratio)

    return sorted(affected_boms, key=_score, reverse=True)

# ── Natural language graph query ───────────────────────────────────────────────

# Graph schema description — injected into every NL query prompt.
# This is the "grounding" for query generation: Claude knows exactly what
# nodes, relationships, and properties exist, so it can't hallucinate schema.
_GRAPH_SCHEMA = """
GRAPH SCHEMA
============

Node types and their properties:

Part
  id              String  — unique, e.g. "P-12345"
  name            String  — human-readable name
  description     String
  category        String  — "electronic" | "mechanical" | "electrical" | "software" | "other"
  criticality     String  — "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  unit_of_measure String  — e.g. "EA"
  specifications_json String — JSON blob of technical specs

Supplier
  id              String  — unique, e.g. "SUP-001"
  name            String
  location        String  — country or region
  certifications  List    — e.g. ["ISO9001", "IATF16949"]
  status          String  — "ACTIVE" | "INACTIVE"
  tier            Integer — 1, 2, or 3
  rating          Float   — 0.0 to 5.0

BOM (Bill of Materials)
  id              String  — unique, e.g. "BOM-001"
  name            String
  version         String  — e.g. "1.0"
  status          String  — "DRAFT" | "REVIEW" | "RELEASED" | "ARCHIVED" | "REJECTED"

Component
  component_id    String
  quantity        Float
  unit_of_measure String
  reference_designator String

Transition
  transition_id   String
  from_status     String
  to_status       String
  actor           String
  timestamp       String

Relationship types:

(Supplier)-[:SUPPLIES {
  valid_from         Date,
  valid_to           Date or null (null = currently active),
  lead_time_days     Integer,
  price              Float,
  currency           String,
  on_time_delivery_rate Float,
  quality_rating     Float
}]->(Part)

(BOM)-[:CONTAINS]->(Component)-[:REFERENCES]->(Part)

(Part)-[:COMPATIBLE_WITH {
  compatibility_type  String  — e.g. "FORM_FIT_FUNCTION",
  validation_status   String  — "VERIFIED" | "PENDING",
  notes               String
}]->(Part)

(BOM)-[:HAS_TRANSITION]->(Transition)

QUERY TIPS:
- Active supply relationships: WHERE r.valid_to IS NULL
- Active suppliers: WHERE s.status = 'ACTIVE'
- Single-source parts: parts where COUNT of active suppliers = 1
- Use OPTIONAL MATCH for left-join style queries
- Always LIMIT results to at most 50 unless the user asks for all
"""


@dataclass
class NLQueryResult:
    """Result of a natural language graph query."""
    question:      str
    cypher:        str           # the generated Cypher query
    raw_results:   List[Dict]    # raw Neo4j results
    answer:        str           # Claude's plain-English interpretation
    row_count:     int
    model:                str
    prompt_tokens:        int
    output_tokens:        int
    cache_tokens_written: int = 0
    cache_tokens_read:    int = 0
    generated_at:  str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question":    self.question,
            "cypher":      self.cypher,
            "row_count":   self.row_count,
            "answer":      self.answer,
            "raw_results": self.raw_results[:20],
            "model":       self.model,
            "token_usage": {
                "prompt":               self.prompt_tokens,
                "output":               self.output_tokens,
                "total":                self.prompt_tokens + self.output_tokens,
                "cache_tokens_written": self.cache_tokens_written,
                "cache_tokens_read":    self.cache_tokens_read,
            },
            "generated_at": self.generated_at,
        }


class NLQueryEngine:
    """
    Natural language to Cypher query engine.

    Grounded in the graph schema — Claude can only reference nodes,
    relationships, and properties that actually exist.

    Two-shot design:
      1. generate_cypher(question) → Cypher string
      2. interpret_results(question, cypher, results) → plain English answer

    If Cypher generation fails validation, raises ValueError with the reason.
    """

    # Safety: only allow read queries
    _FORBIDDEN = ("CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE",
                  "CALL apoc.create", "CALL apoc.merge")

    def __init__(self, db, model: Optional[str] = None):
        settings = get_settings()
        self.db     = db
        self.model  = model or settings.claude_model
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self._prompt_tokens  = 0
        self._output_tokens  = 0
        self._cache_written  = 0
        self._cache_read     = 0

    def _call(self, system: str, user: str, max_tokens: int = 800) -> str:
        """Call Claude with cached system prompt (schema is static — ideal for caching)."""
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        self._prompt_tokens  += msg.usage.input_tokens
        self._output_tokens  += msg.usage.output_tokens
        self._cache_written  += getattr(msg.usage, "cache_creation_input_tokens", 0) or 0
        self._cache_read     += getattr(msg.usage, "cache_read_input_tokens", 0) or 0
        return msg.content[0].text.strip()

    def _validate_cypher(self, cypher: str) -> None:
        """Reject any write operations."""
        upper = cypher.upper()
        for forbidden in self._FORBIDDEN:
            if forbidden in upper:
                raise ValueError(
                    f"Generated query contains forbidden operation: {forbidden}. "
                    "Only read queries (MATCH/RETURN) are allowed."
                )

    def generate_cypher(self, question: str) -> str:
        """
        Convert a natural language question to a Cypher query.

        Returns the raw Cypher string. Raises ValueError if the question
        cannot be answered from the schema or requires a write operation.
        """
        system = f"""You are a Neo4j Cypher expert. Convert natural language questions into Cypher queries.

{_GRAPH_SCHEMA}

RULES:
1. Return ONLY the Cypher query — no explanation, no markdown fences, no preamble.
2. Only use node types, relationship types, and properties defined in the schema above.
3. Only generate read queries (MATCH/RETURN). Never write CREATE, MERGE, SET, DELETE.
4. Always include a LIMIT clause (default 20, up to 50).
5. Use meaningful variable names and return column aliases.
6. If the question cannot be answered from this schema, return exactly: CANNOT_ANSWER"""

        raw = self._call(system, f"Question: {question}")

        # Strip markdown fences if present
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
            if raw.startswith("cypher"):
                raw = raw[6:]
        raw = raw.strip()

        if raw == "CANNOT_ANSWER":
            raise ValueError(
                "This question cannot be answered from the supply chain graph. "
                "Try asking about parts, suppliers, BOMs, or their relationships."
            )

        self._validate_cypher(raw)
        return raw

    def interpret_results(
        self,
        question: str,
        cypher:   str,
        results:  List[Dict],
    ) -> str:
        """
        Interpret raw Neo4j query results as a cited plain-English answer.

        Every factual claim must cite the supporting row(s) using the format:
          **P-12345** (Servo Motor SM-400) [row 1]

        This makes every claim in the answer directly traceable to the data.
        """
        system = """You are a supply chain analyst interpreting graph query results.

CITATION RULES — mandatory for every factual claim:
1. When you mention a specific part, supplier, or BOM from the results, cite it as:
   **<ID>** (<Name>) [row <N>]
   Example: **P-33333** (Controller Board CB-2000) [row 3]

2. Row numbers are 1-indexed from the results list provided (row 1 = first result).

3. If multiple rows support a claim, cite all of them: [row 1, row 3, row 5]

4. Claims about counts or totals cite the full range: [rows 1-18]

5. If a claim comes from the absence of data (e.g. empty results), write [no data found].

6. Never make a claim without a citation. If you cannot cite it, do not say it.

ANSWER RULES:
1. Answer the question directly based ONLY on the provided results.
2. If results are empty, say clearly that nothing matching was found [no data found].
3. Do not add commentary beyond what the data supports.
4. Use bullet points for lists. Keep the answer under 350 words.
5. Lead with the most important finding."""

        # Number the rows explicitly so Claude can cite them accurately
        numbered_results = []
        for i, row in enumerate(results[:20], 1):
            numbered_results.append({"row": i, **row})

        results_preview = json.dumps(numbered_results, indent=2, default=str)
        if len(results) > 20:
            results_preview += f"\n... and {len(results) - 20} more rows (rows 21+)"

        user = f"""Question: {question}

Cypher query that was run:
{cypher}

Results ({len(results)} rows total — showing first 20 with row numbers):
{results_preview}

Answer the question with inline citations for every factual claim."""

        return self._call(system, user, max_tokens=700)

    def query(self, question: str) -> NLQueryResult:
        """
        Run a full natural language query: generate Cypher, execute, interpret.

        Args:
            question: Plain English question about the supply chain graph.

        Returns:
            NLQueryResult with the Cypher, raw results, and plain-English answer.
        """
        self._prompt_tokens = 0
        self._output_tokens = 0
        self._cache_written = 0
        self._cache_read    = 0

        logger.info(f"NL query: {question!r}")

        # Step 1: generate Cypher
        cypher = self.generate_cypher(question)
        logger.info(f"Generated Cypher: {cypher[:100]}…")

        # Step 2: execute
        try:
            results = self.db.execute_query(cypher)
        except Exception as exc:
            raise ValueError(f"Query execution failed: {exc}")

        logger.info(f"Query returned {len(results)} rows")

        # Step 3: rerank rows by domain relevance before interpretation
        results_ranked = rerank_nl_query_rows(results, question)

        # Step 4: interpret reranked results
        answer = self.interpret_results(question, cypher, results_ranked)

        return NLQueryResult(
            question=question,
            cypher=cypher,
            raw_results=results_ranked,
            answer=answer,
            row_count=len(results),
            model=self.model,
            prompt_tokens=self._prompt_tokens,
            output_tokens=self._output_tokens,
            cache_tokens_written=self._cache_written,
            cache_tokens_read=self._cache_read,
        )