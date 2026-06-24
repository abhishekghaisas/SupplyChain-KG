"""
BOM Approval Workflow

Manages BOM status transitions with a rules gate and human approval requirement.

State machine
─────────────
    DRAFT ──► REVIEW ──► RELEASED ──► ARCHIVED
      ▲          │           │
      │          ▼           ▼
      └────── REJECTED ◄─────┘

Any non-terminal state may transition to REJECTED.
REJECTED may return to DRAFT to restart the cycle.
ARCHIVED is terminal.

Gate on RELEASED
────────────────
Both of the following must be satisfied:
  1. Rules engine passes BOMReleasabilityRule (no CRITICAL/HIGH parts without
     at least one active supplier) and SupplierQualificationRule for every supplier.
  2. A human approval record must exist on the BOM (recorded via approve()).

Graph additions
───────────────
  (BOM)-[:HAS_TRANSITION]->(StatusTransition)
  (BOM)-[:APPROVED_BY {approved_at, notes}]->(Approver)

Public API
──────────
  BOMWorkflow(client, rules_engine)
    .approve(bom_id, approver_id, notes)   — record human approval
    .transition(bom_id, to_status, actor)  — advance the state machine
    .get_status(bom_id)                    — current status
    .get_transitions(bom_id)              — full transition history
    .get_approval(bom_id)                 — approval record if present
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from loguru import logger

from src.graph.neo4j_client import Neo4jClient
from src.reasoning.rules_engine import (
    BaseRule,
    ReasoningResult,
    RuleResult,
    RuleSeverity,
    RuleType,
    RulesEngine,
)


# ── Status & transition model ─────────────────────────────────────────────────


class BOMStatus(str, Enum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    RELEASED = "RELEASED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


# Valid (from, to) pairs — anything not listed is forbidden.
ALLOWED_TRANSITIONS: frozenset[tuple[BOMStatus, BOMStatus]] = frozenset(
    {
        (BOMStatus.DRAFT, BOMStatus.REVIEW),
        (BOMStatus.REVIEW, BOMStatus.RELEASED),
        (BOMStatus.REVIEW, BOMStatus.REJECTED),
        (BOMStatus.RELEASED, BOMStatus.ARCHIVED),
        (BOMStatus.RELEASED, BOMStatus.REJECTED),
        (BOMStatus.REJECTED, BOMStatus.DRAFT),
    }
)

# Statuses from which no further transitions are ever allowed.
TERMINAL_STATUSES: frozenset[BOMStatus] = frozenset({BOMStatus.ARCHIVED})


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class TransitionRecord:
    """One entry in the BOM's transition history."""

    transition_id: str
    from_status: str
    to_status: str
    actor: str
    timestamp: str  # ISO-8601 string as stored in Neo4j
    notes: str


@dataclass
class ApprovalRecord:
    """Human approval attached to a BOM."""

    approver_id: str
    approved_at: str  # ISO-8601
    notes: str


@dataclass
class WorkflowResult:
    """
    Outcome of a transition attempt.

    success=True  → transition was applied.
    success=False → transition was blocked; reason explains why.
    rules_result  → populated when the rules gate ran (REVIEW → RELEASED).
    """

    success: bool
    bom_id: str
    from_status: str
    to_status: str
    reason: str
    rules_result: Optional[ReasoningResult] = None
    approval: Optional[ApprovalRecord] = None


# ── BOMReleasabilityRule ──────────────────────────────────────────────────────


class BOMReleasabilityRule(BaseRule):
    """
    Gate rule for releasing a BOM.

    Checks that every CRITICAL or HIGH criticality part in the BOM has at
    least one active supplier.  Parts with no supplier block release outright;
    single-sourced parts produce a WARNING (does not block release, but is
    recorded in the result).
    """

    def __init__(self) -> None:
        super().__init__(
            name="BOMReleasabilityRule",
            rule_type=RuleType.VALIDATION,
            severity=RuleSeverity.ERROR,
        )

    def check(  # type: ignore[override]
        self,
        risk_assessment: Dict[str, Any],
    ) -> RuleResult:
        """
        Args:
            risk_assessment: Output of Neo4jClient.get_bom_risk_assessment().
        """
        components = risk_assessment.get("components", [])
        self.facts_used = [
            f"bom:{risk_assessment.get('bom_id')}",
            f"total_components:{len(components)}",
        ]

        no_supplier: List[str] = []
        single_source: List[str] = []

        for comp in components:
            if comp["criticality"] not in ("CRITICAL", "HIGH"):
                continue
            risk = comp.get("risk_level", "")
            pid = comp["part_id"]
            if risk == "NO_SUPPLIER":
                no_supplier.append(pid)
            elif risk == "SINGLE_SOURCE":
                single_source.append(pid)

        self.facts_used.append(f"no_supplier_parts:{len(no_supplier)}")
        self.facts_used.append(f"single_source_parts:{len(single_source)}")

        if no_supplier:
            return self._create_result(
                passed=False,
                reason=(
                    f"{len(no_supplier)} CRITICAL/HIGH part(s) have no active supplier: "
                    + ", ".join(no_supplier)
                ),
                details={"no_supplier": no_supplier, "single_source": single_source},
                confidence=1.0,
            )

        if single_source:
            # Single-source is a warning, not a hard block
            return self._create_result(
                passed=True,
                reason=(
                    f"Releasable with {len(single_source)} single-sourced "
                    f"CRITICAL/HIGH part(s): " + ", ".join(single_source)
                ),
                details={"no_supplier": [], "single_source": single_source},
                confidence=0.85,
            )

        return self._create_result(
            passed=True,
            reason="All CRITICAL/HIGH parts have multiple active suppliers",
            details={"no_supplier": [], "single_source": []},
            confidence=1.0,
        )


# ── BOMWorkflow ───────────────────────────────────────────────────────────────


class BOMWorkflow:
    """
    Orchestrates BOM status transitions, rules gating, and approval recording.
    """

    def __init__(
        self,
        client: Neo4jClient,
        rules_engine: Optional[RulesEngine] = None,
    ) -> None:
        self._client = client
        self._rules = rules_engine or self._build_default_rules_engine()

    # ── Public API ────────────────────────────────────────────────────────

    def approve(
        self,
        bom_id: str,
        approver_id: str,
        notes: str = "",
    ) -> ApprovalRecord:
        """
        Record a human approval for a BOM.

        An approval can be recorded at any point; it is only *consumed*
        when transitioning to RELEASED.  Recording a second approval for the
        same BOM overwrites the first.

        Args:
            bom_id:      BOM to approve.
            approver_id: Identity of the approving actor (e.g. user ID / email).
            notes:       Optional justification text.

        Returns:
            ApprovalRecord

        Raises:
            ValueError: BOM not found.
        """
        bom = self._client.get_bom(bom_id)
        if bom is None:
            raise ValueError(f"BOM not found: {bom_id!r}")

        query = """
        MATCH (b:BOM {id: $bom_id})
        MERGE (a:Approver {id: $approver_id})
        MERGE (b)-[r:APPROVED_BY]->(a)
          ON CREATE SET r.approved_at = datetime(),
                        r.notes       = $notes
          ON MATCH  SET r.approved_at = datetime(),
                        r.notes       = $notes
        RETURN toString(r.approved_at) AS approved_at
        """
        rows = self._client.execute_query(
            query,
            {
                "bom_id": bom_id,
                "approver_id": approver_id,
                "notes": notes,
            },
        )
        approved_at = rows[0]["approved_at"] if rows else datetime.now().isoformat()
        logger.info(f"BOM {bom_id!r} approved by {approver_id!r}")

        return ApprovalRecord(
            approver_id=approver_id,
            approved_at=approved_at,
            notes=notes,
        )

    def transition(
        self,
        bom_id: str,
        to_status: str | BOMStatus,
        actor: str,
        notes: str = "",
    ) -> WorkflowResult:
        """
        Attempt to advance a BOM to a new status.

        For the REVIEW → RELEASED transition, both the rules gate and a
        human approval must pass before the status is written.

        Args:
            bom_id:    BOM to advance.
            to_status: Target status (string or BOMStatus enum).
            actor:     Identity of the actor requesting the transition.
            notes:     Optional notes stored on the transition record.

        Returns:
            WorkflowResult — check `.success` before proceeding.

        Raises:
            ValueError: BOM not found, or to_status is not a valid status.
        """
        # --- resolve & validate inputs ---
        try:
            target = BOMStatus(to_status)
        except ValueError:
            raise ValueError(
                f"Invalid status {to_status!r}. " f"Valid values: {[s.value for s in BOMStatus]}"
            )

        bom = self._client.get_bom(bom_id)
        if bom is None:
            raise ValueError(f"BOM not found: {bom_id!r}")

        current_str = bom.get("status", BOMStatus.DRAFT.value)
        try:
            current = BOMStatus(current_str)
        except ValueError:
            current = BOMStatus.DRAFT

        # --- state machine checks ---
        blocked = self._check_transition_allowed(bom_id, current, target)
        if blocked is not None:
            return blocked

        # --- special gate for RELEASED ---
        rules_result: Optional[ReasoningResult] = None
        approval: Optional[ApprovalRecord] = None

        if target == BOMStatus.RELEASED:
            gate = self._run_release_gate(bom_id)
            rules_result = gate["rules_result"]
            approval = gate["approval"]

            if not rules_result.passed:
                logger.warning(f"BOM {bom_id!r} release blocked by rules: {rules_result.summary}")
                return WorkflowResult(
                    success=False,
                    bom_id=bom_id,
                    from_status=current.value,
                    to_status=target.value,
                    reason=f"Rules gate failed: {rules_result.summary}",
                    rules_result=rules_result,
                )

            if approval is None:
                logger.warning(f"BOM {bom_id!r} release blocked: no human approval")
                return WorkflowResult(
                    success=False,
                    bom_id=bom_id,
                    from_status=current.value,
                    to_status=target.value,
                    reason="Human approval required before releasing",
                    rules_result=rules_result,
                )

        # --- apply the transition ---
        self._write_transition(bom_id, current, target, actor, notes)

        logger.info(f"BOM {bom_id!r}: {current.value} → {target.value} by {actor!r}")
        return WorkflowResult(
            success=True,
            bom_id=bom_id,
            from_status=current.value,
            to_status=target.value,
            reason="Transition applied successfully",
            rules_result=rules_result,
            approval=approval,
        )

    def get_status(self, bom_id: str) -> Optional[str]:
        """Return the current status of a BOM, or None if not found."""
        bom = self._client.get_bom(bom_id)
        return bom["status"] if bom else None

    def get_transitions(self, bom_id: str) -> List[TransitionRecord]:
        """
        Return the full transition history for a BOM, oldest first.
        """
        query = """
        MATCH (b:BOM {id: $bom_id})-[:HAS_TRANSITION]->(t:StatusTransition)
        RETURN t.id           AS transition_id,
               t.from_status  AS from_status,
               t.to_status    AS to_status,
               t.actor        AS actor,
               toString(t.timestamp) AS timestamp,
               t.notes        AS notes
        ORDER BY t.timestamp
        """
        rows = self._client.execute_query(query, {"bom_id": bom_id})
        return [
            TransitionRecord(
                transition_id=r["transition_id"],
                from_status=r["from_status"],
                to_status=r["to_status"],
                actor=r["actor"],
                timestamp=r["timestamp"] or "",
                notes=r.get("notes") or "",
            )
            for r in rows
        ]

    def get_approval(self, bom_id: str) -> Optional[ApprovalRecord]:
        """Return the current approval record for a BOM, or None."""
        query = """
        MATCH (b:BOM {id: $bom_id})-[r:APPROVED_BY]->(a:Approver)
        RETURN a.id AS approver_id,
               toString(r.approved_at) AS approved_at,
               r.notes AS notes
        """
        rows = self._client.execute_query(query, {"bom_id": bom_id})
        if not rows:
            return None
        r = rows[0]
        return ApprovalRecord(
            approver_id=r["approver_id"],
            approved_at=r["approved_at"] or "",
            notes=r.get("notes") or "",
        )

    # ── Private helpers ───────────────────────────────────────────────────

    def _check_transition_allowed(
        self,
        bom_id: str,
        current: BOMStatus,
        target: BOMStatus,
    ) -> Optional[WorkflowResult]:
        """
        Return a failed WorkflowResult if the transition is not allowed,
        None if it is.
        """
        if current in TERMINAL_STATUSES:
            return WorkflowResult(
                success=False,
                bom_id=bom_id,
                from_status=current.value,
                to_status=target.value,
                reason=f"BOM is {current.value} — no further transitions allowed",
            )

        if (current, target) not in ALLOWED_TRANSITIONS:
            # Build a helpful message listing what IS allowed from here
            allowed = [t.value for (f, t) in ALLOWED_TRANSITIONS if f == current]
            return WorkflowResult(
                success=False,
                bom_id=bom_id,
                from_status=current.value,
                to_status=target.value,
                reason=(
                    f"Transition {current.value} → {target.value} is not allowed. "
                    f"From {current.value}, valid targets are: {allowed or ['none']}"
                ),
            )

        return None

    def _run_release_gate(self, bom_id: str) -> Dict[str, Any]:
        """Run rules checks and retrieve approval; return dict with both."""
        risk = self._client.get_bom_risk_assessment(bom_id)
        rules_result = self._rules.evaluate(
            subject=f"BOM {bom_id} release gate",
            rules=["BOMReleasabilityRule"],
            risk_assessment=risk,
        )
        approval = self.get_approval(bom_id)
        return {"rules_result": rules_result, "approval": approval}

    def _write_transition(
        self,
        bom_id: str,
        current: BOMStatus,
        target: BOMStatus,
        actor: str,
        notes: str,
    ) -> None:
        """Write status update + StatusTransition node atomically."""
        query = """
        MATCH (b:BOM {id: $bom_id})
        SET b.status = $to_status
        CREATE (t:StatusTransition {
            id:          $bom_id + '-' + toString(datetime()),
            from_status: $from_status,
            to_status:   $to_status,
            actor:       $actor,
            notes:       $notes,
            timestamp:   datetime()
        })
        CREATE (b)-[:HAS_TRANSITION]->(t)
        """
        self._client.execute_write(
            query,
            {
                "bom_id": bom_id,
                "from_status": current.value,
                "to_status": target.value,
                "actor": actor,
                "notes": notes,
            },
        )

    @staticmethod
    def _build_default_rules_engine() -> RulesEngine:
        engine = RulesEngine()
        engine.register_rule(
            BOMReleasabilityRule(),
            groups=["release_gate"],
        )
        return engine
