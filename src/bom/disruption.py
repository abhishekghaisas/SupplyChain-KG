"""
Disruption Analysis

Models two disruption scenarios and produces severity scores with
recommended actions for every affected BOM.

Scenarios
─────────
  analyze_supplier_disruption(supplier_id, bom_statuses)
      A supplier becomes unavailable.  Every part they currently supply
      is treated as disrupted; every BOM containing those parts is scored.

  analyze_part_disruption(part_id, bom_statuses)
      A single part becomes unavailable.  Verified substitutes are
      surfaced from the graph; every BOM containing the part is scored.

Severity scoring  (0.0 – 1.0 per affected BOM)
───────────────────────────────────────────────
  Base score is the maximum criticality weight of any disrupted part
  in the BOM:
      CRITICAL → 1.0   HIGH → 0.75   MEDIUM → 0.4   LOW → 0.1

  Multiplied by a sourcing-risk factor:
      No alternate supplier and no substitute → ×1.0  (worst)
      No alternate supplier but substitute exists → ×0.7
      Alternate supplier exists → ×0.4
      Multiple alternates → ×0.2

  Result is clamped to [0.0, 1.0].

Recommended actions
───────────────────
  USE_SUBSTITUTE          — verified COMPATIBLE_WITH substitute exists
  EXPEDITE_ALTERNATE      — other active suppliers exist for the part
  DUAL_SOURCE             — only one supplier; recommend qualifying another
  ESCALATE                — no substitute, no alternate, CRITICAL/HIGH part
  MONITOR                 — LOW/MEDIUM with some sourcing flexibility
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from loguru import logger

from src.graph.neo4j_client import Neo4jClient


# ── Constants ─────────────────────────────────────────────────────────────────

CRITICALITY_WEIGHT: Dict[str, float] = {
    "CRITICAL": 1.0,
    "HIGH": 0.75,
    "MEDIUM": 0.4,
    "LOW": 0.1,
}

# Sourcing-risk multipliers (applied to the criticality base score)
_SOURCING_RISK: Dict[str, float] = {
    "no_alternate_no_substitute": 1.0,
    "no_alternate_has_substitute": 0.7,
    "has_alternate": 0.4,
    "multi_alternate": 0.2,
}

DEFAULT_BOM_STATUSES = ("RELEASED",)


# ── Enums / data classes ──────────────────────────────────────────────────────


class RecommendedAction(str, Enum):
    USE_SUBSTITUTE = "USE_SUBSTITUTE"
    EXPEDITE_ALTERNATE = "EXPEDITE_ALTERNATE"
    DUAL_SOURCE = "DUAL_SOURCE"
    ESCALATE = "ESCALATE"
    MONITOR = "MONITOR"


@dataclass
class SubstituteInfo:
    part_id: str
    part_name: str
    compatibility_type: str
    validation_status: str
    constraints: Dict[str, Any]
    notes: str


@dataclass
class DisruptedPart:
    """One part affected within the context of a disruption scenario."""

    part_id: str
    part_name: str
    criticality: str
    quantity_in_bom: float
    alternate_supplier_count: int  # active suppliers *excluding* the disrupted one
    substitutes: List[SubstituteInfo] = field(default_factory=list)

    @property
    def has_alternate_supplier(self) -> bool:
        return self.alternate_supplier_count > 0

    @property
    def has_substitute(self) -> bool:
        return bool(self.substitutes)

    @property
    def sourcing_risk_key(self) -> str:
        if self.alternate_supplier_count > 1:
            return "multi_alternate"
        if self.alternate_supplier_count == 1:
            return "has_alternate"
        if self.has_substitute:
            return "no_alternate_has_substitute"
        return "no_alternate_no_substitute"

    def severity_contribution(self) -> float:
        base = CRITICALITY_WEIGHT.get(self.criticality, 0.1)
        return base * _SOURCING_RISK[self.sourcing_risk_key]

    def recommended_actions(self) -> List[RecommendedAction]:
        actions: List[RecommendedAction] = []
        crit = self.criticality in ("CRITICAL", "HIGH")

        if self.has_substitute:
            actions.append(RecommendedAction.USE_SUBSTITUTE)

        if self.alternate_supplier_count > 1:
            actions.append(RecommendedAction.EXPEDITE_ALTERNATE)
        elif self.alternate_supplier_count == 1:
            actions.append(RecommendedAction.EXPEDITE_ALTERNATE)
            if crit:
                actions.append(RecommendedAction.DUAL_SOURCE)
        else:
            # No alternate supplier
            if not self.has_substitute and crit:
                actions.append(RecommendedAction.ESCALATE)
            elif not self.has_substitute:
                actions.append(RecommendedAction.MONITOR)
            if crit and not self.has_alternate_supplier:
                actions.append(RecommendedAction.DUAL_SOURCE)

        if not actions:
            actions.append(RecommendedAction.MONITOR)

        # Deduplicate while preserving order
        seen: set = set()
        # type: ignore[func-returns-value]
        return [a for a in actions if not (a in seen or seen.add(a))]


@dataclass
class AffectedBOM:
    bom_id: str
    bom_name: str
    bom_version: str
    bom_status: str
    disrupted_parts: List[DisruptedPart]
    severity_score: float  # 0.0–1.0
    actions: List[RecommendedAction]

    @property
    def severity_label(self) -> str:
        if self.severity_score >= 0.8:
            return "CRITICAL"
        if self.severity_score >= 0.5:
            return "HIGH"
        if self.severity_score >= 0.2:
            return "MEDIUM"
        return "LOW"


@dataclass
class DisruptionReport:
    """Top-level result returned by both analysis entry points."""

    scenario: str  # "SUPPLIER" or "PART"
    disrupted_id: str  # supplier_id or part_id
    disrupted_name: str
    bom_statuses: List[str]  # statuses that were searched
    affected_boms: List[AffectedBOM]
    total_parts_affected: int

    @property
    def critical_boms(self) -> List[AffectedBOM]:
        return [b for b in self.affected_boms if b.severity_label == "CRITICAL"]

    @property
    def summary(self) -> str:
        n = len(self.affected_boms)
        c = len(self.critical_boms)
        return (
            f"{self.scenario} disruption of {self.disrupted_name!r}: "
            f"{n} BOM(s) affected, {c} critical"
        )

    def format_report(self) -> str:
        lines = [
            "",
            "=" * 72,
            f"DISRUPTION ANALYSIS — {self.scenario}",
            f"  Disrupted: {self.disrupted_name} ({self.disrupted_id})",
            f"  BOM scope: {', '.join(self.bom_statuses)}",
            f"  Summary:   {self.summary}",
            "=" * 72,
        ]
        if not self.affected_boms:
            lines.append("  No affected BOMs found.")
        for bom in sorted(self.affected_boms, key=lambda b: b.severity_score, reverse=True):
            lines += [
                "",
                f"  [{bom.severity_label}] {bom.bom_id} — {bom.bom_name} "
                f"v{bom.bom_version} ({bom.bom_status})"
                f"  severity={bom.severity_score:.2f}",
                f"  Actions: {', '.join(a.value for a in bom.actions)}",
            ]
            for dp in bom.disrupted_parts:
                lines.append(
                    f"    • {dp.part_id} {dp.part_name} [{dp.criticality}]"
                    f"  qty={dp.quantity_in_bom}"
                    f"  alternates={dp.alternate_supplier_count}"
                    + (f"  substitutes={len(dp.substitutes)}" if dp.substitutes else "")
                )
        lines.append("")
        return "\n".join(lines)


# ── DisruptionAnalyzer ────────────────────────────────────────────────────────


class DisruptionAnalyzer:
    """
    Runs supplier and part disruption analyses against the Neo4j graph.

    Args:
        client: Connected Neo4jClient instance.
    """

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    # ── Public API ────────────────────────────────────────────────────────

    def analyze_supplier_disruption(
        self,
        supplier_id: str,
        bom_statuses: tuple[str, ...] | list[str] = DEFAULT_BOM_STATUSES,
    ) -> DisruptionReport:
        """
        Analyse the impact of a supplier becoming unavailable.

        Finds every part the supplier currently supplies, then finds every
        BOM (in the given statuses) that contains those parts.

        Args:
            supplier_id:  Supplier to model as disrupted.
            bom_statuses: BOM statuses to include (default: RELEASED only).

        Returns:
            DisruptionReport

        Raises:
            ValueError: Supplier not found.
        """
        supplier = self._fetch_supplier(supplier_id)
        if supplier is None:
            raise ValueError(f"Supplier not found: {supplier_id!r}")

        logger.info(
            f"Supplier disruption analysis: {supplier_id!r} "
            f"({supplier['name']}), scope={list(bom_statuses)}"
        )

        # All parts currently supplied by this supplier
        supplied_parts = self._fetch_supplied_parts(supplier_id)
        if not supplied_parts:
            logger.info(f"Supplier {supplier_id!r} supplies no active parts")
            return DisruptionReport(
                scenario="SUPPLIER",
                disrupted_id=supplier_id,
                disrupted_name=supplier["name"],
                bom_statuses=list(bom_statuses),
                affected_boms=[],
                total_parts_affected=0,
            )

        part_ids = [p["part_id"] for p in supplied_parts]
        part_meta = {p["part_id"]: p for p in supplied_parts}

        affected_boms = self._build_affected_boms(
            part_ids=part_ids,
            part_meta=part_meta,
            disrupted_supplier_id=supplier_id,
            bom_statuses=list(bom_statuses),
        )

        return DisruptionReport(
            scenario="SUPPLIER",
            disrupted_id=supplier_id,
            disrupted_name=supplier["name"],
            bom_statuses=list(bom_statuses),
            affected_boms=affected_boms,
            total_parts_affected=len(part_ids),
        )

    def analyze_part_disruption(
        self,
        part_id: str,
        bom_statuses: tuple[str, ...] | list[str] = DEFAULT_BOM_STATUSES,
    ) -> DisruptionReport:
        """
        Analyse the impact of a single part becoming unavailable.

        Surfaces verified substitutes from the graph and finds every BOM
        containing the part.

        Args:
            part_id:      Part to model as disrupted.
            bom_statuses: BOM statuses to include (default: RELEASED only).

        Returns:
            DisruptionReport

        Raises:
            ValueError: Part not found.
        """
        part = self._fetch_part(part_id)
        if part is None:
            raise ValueError(f"Part not found: {part_id!r}")

        logger.info(
            f"Part disruption analysis: {part_id!r} "
            f"({part['name']}), scope={list(bom_statuses)}"
        )

        part_meta = {
            part_id: {
                "part_id": part_id,
                "part_name": part["name"],
                "criticality": part["criticality"],
            }
        }

        affected_boms = self._build_affected_boms(
            part_ids=[part_id],
            part_meta=part_meta,
            disrupted_supplier_id=None,  # all suppliers are disrupted for this part
            bom_statuses=list(bom_statuses),
        )

        return DisruptionReport(
            scenario="PART",
            disrupted_id=part_id,
            disrupted_name=part["name"],
            bom_statuses=list(bom_statuses),
            affected_boms=affected_boms,
            total_parts_affected=1,
        )

    # ── Graph queries ─────────────────────────────────────────────────────

    def _fetch_supplier(self, supplier_id: str) -> Optional[Dict[str, Any]]:
        rows = self._client.execute_query(
            "MATCH (s:Supplier {id: $id}) " "RETURN s.id AS id, s.name AS name, s.status AS status",
            {"id": supplier_id},
        )
        return rows[0] if rows else None

    def _fetch_part(self, part_id: str) -> Optional[Dict[str, Any]]:
        rows = self._client.execute_query(
            "MATCH (p:Part {id: $id}) "
            "RETURN p.id AS id, p.name AS name, p.criticality AS criticality",
            {"id": part_id},
        )
        return rows[0] if rows else None

    def _fetch_supplied_parts(self, supplier_id: str) -> List[Dict[str, Any]]:
        """All parts currently supplied by a supplier (valid_to IS NULL)."""
        query = """
        MATCH (s:Supplier {id: $supplier_id})-[r:SUPPLIES]->(p:Part)
        WHERE r.valid_to IS NULL
        RETURN p.id          AS part_id,
               p.name        AS part_name,
               p.criticality AS criticality
        """
        return self._client.execute_query(query, {"supplier_id": supplier_id})

    def _fetch_boms_containing_parts(
        self,
        part_ids: List[str],
        bom_statuses: List[str],
    ) -> List[Dict[str, Any]]:
        """
        For each part_id, find every BOM (filtered by status) that contains it,
        along with the quantity used.
        """
        query = """
        MATCH (b:BOM)-[:CONTAINS]->(c:Component)-[:REFERENCES]->(p:Part)
        WHERE p.id IN $part_ids
          AND b.status IN $bom_statuses
        RETURN b.id      AS bom_id,
               b.name    AS bom_name,
               b.version AS bom_version,
               b.status  AS bom_status,
               p.id      AS part_id,
               c.quantity AS quantity
        ORDER BY b.id, p.id
        """
        return self._client.execute_query(
            query, {"part_ids": part_ids, "bom_statuses": bom_statuses}
        )

    def _fetch_alternate_supplier_count(
        self,
        part_id: str,
        excluded_supplier_id: Optional[str],
    ) -> int:
        """Count active suppliers for a part, optionally excluding one."""
        if excluded_supplier_id:
            query = """
            MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: $part_id})
            WHERE r.valid_to IS NULL
              AND s.status = 'ACTIVE'
              AND s.id <> $excluded_supplier_id
            RETURN count(s) AS cnt
            """
            params: Dict[str, Any] = {
                "part_id": part_id,
                "excluded_supplier_id": excluded_supplier_id,
            }
        else:
            query = """
            MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: $part_id})
            WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
            RETURN count(s) AS cnt
            """
            params = {"part_id": part_id}

        rows = self._client.execute_query(query, params)
        return int(rows[0]["cnt"]) if rows else 0

    def _fetch_substitutes(self, part_id: str) -> List[SubstituteInfo]:
        """Return verified substitutes for a part from COMPATIBLE_WITH edges."""
        query = """
        MATCH (original:Part {id: $part_id})-[r:COMPATIBLE_WITH]->(sub:Part)
        WHERE r.validation_status = 'VERIFIED'
        RETURN sub.id   AS part_id,
               sub.name AS part_name,
               r.compatibility_type  AS compatibility_type,
               r.validation_status   AS validation_status,
               r.constraints_json    AS constraints_json,
               r.notes               AS notes
        """
        rows = self._client.execute_query(query, {"part_id": part_id})
        result = []
        for row in rows:
            import json

            try:
                constraints = json.loads(row.get("constraints_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                constraints = {}
            result.append(
                SubstituteInfo(
                    part_id=row["part_id"],
                    part_name=row["part_name"],
                    compatibility_type=row.get("compatibility_type", ""),
                    validation_status=row.get("validation_status", ""),
                    constraints=constraints,
                    notes=row.get("notes") or "",
                )
            )
        return result

    # ── Assembly helpers ──────────────────────────────────────────────────

    def _build_affected_boms(
        self,
        part_ids: List[str],
        part_meta: Dict[str, Dict[str, Any]],
        disrupted_supplier_id: Optional[str],
        bom_statuses: List[str],
    ) -> List[AffectedBOM]:
        """
        For each BOM that contains any of the disrupted parts, build an
        AffectedBOM with DisruptedPart entries, a severity score, and actions.
        """
        bom_rows = self._fetch_boms_containing_parts(part_ids, bom_statuses)

        # Group rows by BOM
        bom_parts: Dict[str, Dict] = {}
        for row in bom_rows:
            bid = row["bom_id"]
            if bid not in bom_parts:
                bom_parts[bid] = {
                    "bom_id": bid,
                    "bom_name": row["bom_name"],
                    "bom_version": row["bom_version"],
                    "bom_status": row["bom_status"],
                    "parts": [],
                }
            bom_parts[bid]["parts"].append(row)

        affected: List[AffectedBOM] = []
        for bom_data in bom_parts.values():
            disrupted_parts = self._build_disrupted_parts(
                bom_data["parts"], part_meta, disrupted_supplier_id
            )
            if not disrupted_parts:
                continue

            severity = self._compute_severity(disrupted_parts)
            actions = self._aggregate_actions(disrupted_parts)

            affected.append(
                AffectedBOM(
                    bom_id=bom_data["bom_id"],
                    bom_name=bom_data["bom_name"],
                    bom_version=bom_data["bom_version"],
                    bom_status=bom_data["bom_status"],
                    disrupted_parts=disrupted_parts,
                    severity_score=severity,
                    actions=actions,
                )
            )

        return affected

    def _build_disrupted_parts(
        self,
        bom_part_rows: List[Dict[str, Any]],
        part_meta: Dict[str, Dict[str, Any]],
        disrupted_supplier_id: Optional[str],
    ) -> List[DisruptedPart]:
        parts = []
        for row in bom_part_rows:
            pid = row["part_id"]
            meta = part_meta.get(pid, {})

            alt_count = self._fetch_alternate_supplier_count(pid, disrupted_supplier_id)
            substitutes = self._fetch_substitutes(pid)

            parts.append(
                DisruptedPart(
                    part_id=pid,
                    part_name=meta.get("part_name", ""),
                    criticality=meta.get("criticality", "LOW"),
                    quantity_in_bom=float(row.get("quantity") or 0),
                    alternate_supplier_count=alt_count,
                    substitutes=substitutes,
                )
            )
        return parts

    @staticmethod
    def _compute_severity(disrupted_parts: List[DisruptedPart]) -> float:
        """Severity = max contribution across all disrupted parts, clamped to [0,1]."""
        if not disrupted_parts:
            return 0.0
        return min(1.0, max(dp.severity_contribution() for dp in disrupted_parts))

    @staticmethod
    def _aggregate_actions(
        disrupted_parts: List[DisruptedPart],
    ) -> List[RecommendedAction]:
        """
        Union of all per-part actions, ordered by priority:
        ESCALATE > USE_SUBSTITUTE > EXPEDITE_ALTERNATE > DUAL_SOURCE > MONITOR
        """
        priority = [
            RecommendedAction.ESCALATE,
            RecommendedAction.USE_SUBSTITUTE,
            RecommendedAction.EXPEDITE_ALTERNATE,
            RecommendedAction.DUAL_SOURCE,
            RecommendedAction.MONITOR,
        ]
        seen: set = set()
        for dp in disrupted_parts:
            seen.update(dp.recommended_actions())
        return [a for a in priority if a in seen]
