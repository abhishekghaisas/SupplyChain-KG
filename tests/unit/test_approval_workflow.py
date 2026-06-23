"""
Unit tests for BOMWorkflow and BOMReleasabilityRule.

No Neo4j required — the client is fully mocked.

Run with:
    pytest test_approval_workflow.py -v
"""

from __future__ import annotations

import pytest
from copy import deepcopy
from unittest.mock import MagicMock, call, patch
from typing import Any, Dict, List, Optional

from src.bom.approval_workflow import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    ApprovalRecord,
    BOMReleasabilityRule,
    BOMStatus,
    BOMWorkflow,
    TransitionRecord,
    WorkflowResult,
)
from src.reasoning.rules_engine import RulesEngine, ReasoningResult, RuleSeverity


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _bom(bom_id="BOM-001", status="DRAFT") -> Dict[str, Any]:
    return {"id": bom_id, "name": "Test BOM", "version": "1.0",
            "description": "", "status": status}


def _risk(bom_id="BOM-001", components=None) -> Dict[str, Any]:
    return {"bom_id": bom_id, "total_components": len(components or []),
            "at_risk_count": 0, "components": components or [],
            "at_risk_components": []}


def _comp(part_id, criticality="HIGH", risk_level="MULTI_SOURCE") -> Dict[str, Any]:
    return {"part_id": part_id, "part_name": f"Part {part_id}",
            "criticality": criticality, "quantity": 1.0, "supplier_count": 2,
            "risk_level": risk_level}


def _approval_row(approver_id="alice", notes="LGTM") -> List[Dict]:
    return [{"approver_id": approver_id, "approved_at": "2024-01-15T10:00:00",
             "notes": notes}]


def _make_client(
    bom: Optional[Dict] = None,
    risk: Optional[Dict] = None,
    approval_rows: Optional[List] = None,
    transition_rows: Optional[List] = None,
) -> MagicMock:
    client = MagicMock()
    client.get_bom.return_value = bom
    client.get_bom_risk_assessment.return_value = risk or _risk()
    # execute_query backs both approve() and get_approval() / get_transitions()
    client.execute_query.return_value = approval_rows or []
    client.execute_write.return_value = None
    return client


def _released_rules_engine() -> RulesEngine:
    """Rules engine where BOMReleasabilityRule always passes."""
    engine = RulesEngine()
    engine.register_rule(BOMReleasabilityRule())
    return engine


# ── BOMStatus enum ────────────────────────────────────────────────────────────

class TestBOMStatus:
    def test_all_values_exist(self):
        for v in ("DRAFT", "REVIEW", "RELEASED", "ARCHIVED", "REJECTED"):
            assert BOMStatus(v).value == v

    def test_is_str(self):
        assert isinstance(BOMStatus.DRAFT, str)


# ── ALLOWED_TRANSITIONS ───────────────────────────────────────────────────────

class TestAllowedTransitions:
    def test_draft_to_review_allowed(self):
        assert (BOMStatus.DRAFT, BOMStatus.REVIEW) in ALLOWED_TRANSITIONS

    def test_review_to_released_allowed(self):
        assert (BOMStatus.REVIEW, BOMStatus.RELEASED) in ALLOWED_TRANSITIONS

    def test_review_to_rejected_allowed(self):
        assert (BOMStatus.REVIEW, BOMStatus.REJECTED) in ALLOWED_TRANSITIONS

    def test_released_to_archived_allowed(self):
        assert (BOMStatus.RELEASED, BOMStatus.ARCHIVED) in ALLOWED_TRANSITIONS

    def test_released_to_rejected_allowed(self):
        assert (BOMStatus.RELEASED, BOMStatus.REJECTED) in ALLOWED_TRANSITIONS

    def test_rejected_to_draft_allowed(self):
        assert (BOMStatus.REJECTED, BOMStatus.DRAFT) in ALLOWED_TRANSITIONS

    def test_draft_to_released_not_allowed(self):
        assert (BOMStatus.DRAFT, BOMStatus.RELEASED) not in ALLOWED_TRANSITIONS

    def test_draft_to_archived_not_allowed(self):
        assert (BOMStatus.DRAFT, BOMStatus.ARCHIVED) not in ALLOWED_TRANSITIONS

    def test_archived_to_anything_terminal(self):
        assert BOMStatus.ARCHIVED in TERMINAL_STATUSES

    def test_archived_has_no_outgoing_transitions(self):
        outgoing = [(f, t) for (f, t) in ALLOWED_TRANSITIONS if f == BOMStatus.ARCHIVED]
        assert outgoing == []


# ── BOMReleasabilityRule ──────────────────────────────────────────────────────

class TestBOMReleasabilityRule:
    def _rule(self):
        return BOMReleasabilityRule()

    def test_empty_bom_passes(self):
        r = self._rule().check(_risk(components=[]))
        assert r.passed

    def test_all_multi_source_passes(self):
        comps = [_comp("P-1", "CRITICAL", "MULTI_SOURCE"),
                 _comp("P-2", "HIGH",     "MULTI_SOURCE")]
        r = self._rule().check(_risk(components=comps))
        assert r.passed

    def test_no_supplier_critical_blocks(self):
        comps = [_comp("P-1", "CRITICAL", "NO_SUPPLIER")]
        r = self._rule().check(_risk(components=comps))
        assert not r.passed
        assert "P-1" in r.reason

    def test_no_supplier_high_blocks(self):
        comps = [_comp("P-1", "HIGH", "NO_SUPPLIER")]
        r = self._rule().check(_risk(components=comps))
        assert not r.passed

    def test_no_supplier_medium_does_not_block(self):
        comps = [_comp("P-1", "MEDIUM", "NO_SUPPLIER")]
        r = self._rule().check(_risk(components=comps))
        assert r.passed

    def test_no_supplier_low_does_not_block(self):
        comps = [_comp("P-1", "LOW", "NO_SUPPLIER")]
        r = self._rule().check(_risk(components=comps))
        assert r.passed

    def test_single_source_high_passes_with_reduced_confidence(self):
        comps = [_comp("P-1", "HIGH", "SINGLE_SOURCE")]
        r = self._rule().check(_risk(components=comps))
        assert r.passed
        assert r.confidence < 1.0

    def test_single_source_in_details(self):
        comps = [_comp("P-1", "HIGH", "SINGLE_SOURCE")]
        r = self._rule().check(_risk(components=comps))
        assert "P-1" in r.details["single_source"]

    def test_no_supplier_trumps_single_source(self):
        comps = [_comp("P-1", "CRITICAL", "NO_SUPPLIER"),
                 _comp("P-2", "HIGH",     "SINGLE_SOURCE")]
        r = self._rule().check(_risk(components=comps))
        assert not r.passed
        assert "P-1" in r.reason

    def test_multiple_no_supplier_all_listed(self):
        comps = [_comp("P-1", "CRITICAL", "NO_SUPPLIER"),
                 _comp("P-2", "HIGH",     "NO_SUPPLIER")]
        r = self._rule().check(_risk(components=comps))
        assert "P-1" in r.reason and "P-2" in r.reason

    def test_rule_name(self):
        assert self._rule().name == "BOMReleasabilityRule"

    def test_severity_is_error(self):
        assert self._rule().severity == RuleSeverity.ERROR

    def test_facts_include_bom_id(self):
        r = self._rule().check(_risk(bom_id="BOM-001", components=[]))
        assert any("BOM-001" in f for f in r.facts_used)


# ── BOMWorkflow.approve ───────────────────────────────────────────────────────

class TestApprove:
    def test_returns_approval_record(self):
        client = _make_client(bom=_bom(), approval_rows=_approval_row())
        rec = BOMWorkflow(client).approve("BOM-001", "alice")
        assert isinstance(rec, ApprovalRecord)
        assert rec.approver_id == "alice"

    def test_raises_when_bom_not_found(self):
        client = _make_client(bom=None)
        with pytest.raises(ValueError, match="BOM-001"):
            BOMWorkflow(client).approve("BOM-001", "alice")

    def test_execute_query_called(self):
        client = _make_client(bom=_bom(), approval_rows=_approval_row())
        BOMWorkflow(client).approve("BOM-001", "alice", notes="Looks good")
        client.execute_query.assert_called_once()

    def test_approver_id_passed_to_query(self):
        client = _make_client(bom=_bom(), approval_rows=_approval_row())
        BOMWorkflow(client).approve("BOM-001", "bob")
        args = client.execute_query.call_args[0][1]
        assert args["approver_id"] == "bob"

    def test_notes_passed_to_query(self):
        client = _make_client(bom=_bom(), approval_rows=_approval_row())
        BOMWorkflow(client).approve("BOM-001", "alice", notes="Approved after review")
        args = client.execute_query.call_args[0][1]
        assert args["notes"] == "Approved after review"

    def test_approved_at_in_record(self):
        client = _make_client(bom=_bom(), approval_rows=_approval_row())
        rec = BOMWorkflow(client).approve("BOM-001", "alice")
        assert rec.approved_at  # non-empty

    def test_empty_notes_allowed(self):
        client = _make_client(bom=_bom(), approval_rows=_approval_row(notes=""))
        rec = BOMWorkflow(client).approve("BOM-001", "alice")
        assert rec.notes == ""


# ── BOMWorkflow.transition — state machine ────────────────────────────────────

class TestTransitionStateMachine:
    def test_draft_to_review_succeeds(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", "REVIEW", "alice")
        assert result.success

    def test_rejected_to_draft_succeeds(self):
        client = _make_client(bom=_bom(status="REJECTED"))
        result = BOMWorkflow(client).transition("BOM-001", "DRAFT", "alice")
        assert result.success

    def test_draft_to_released_blocked(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", "RELEASED", "alice")
        assert not result.success
        assert "not allowed" in result.reason.lower()

    def test_draft_to_archived_blocked(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", "ARCHIVED", "alice")
        assert not result.success

    def test_archived_is_terminal(self):
        client = _make_client(bom=_bom(status="ARCHIVED"))
        result = BOMWorkflow(client).transition("BOM-001", "REJECTED", "alice")
        assert not result.success
        assert "ARCHIVED" in result.reason

    def test_invalid_status_raises(self):
        client = _make_client(bom=_bom())
        with pytest.raises(ValueError, match="NONSENSE"):
            BOMWorkflow(client).transition("BOM-001", "NONSENSE", "alice")

    def test_bom_not_found_raises(self):
        client = _make_client(bom=None)
        with pytest.raises(ValueError, match="BOM-001"):
            BOMWorkflow(client).transition("BOM-001", "REVIEW", "alice")

    def test_result_contains_from_and_to_status(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", "REVIEW", "alice")
        assert result.from_status == "DRAFT"
        assert result.to_status   == "REVIEW"

    def test_result_contains_bom_id(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", "REVIEW", "alice")
        assert result.bom_id == "BOM-001"

    def test_execute_write_called_on_success(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        BOMWorkflow(client).transition("BOM-001", "REVIEW", "alice")
        client.execute_write.assert_called_once()

    def test_execute_write_not_called_on_failure(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        BOMWorkflow(client).transition("BOM-001", "ARCHIVED", "alice")
        client.execute_write.assert_not_called()

    def test_valid_targets_listed_in_blocked_reason(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", "ARCHIVED", "alice")
        assert "REVIEW" in result.reason   # REVIEW is a valid target from DRAFT

    def test_enum_value_accepted(self):
        client = _make_client(bom=_bom(status="DRAFT"))
        result = BOMWorkflow(client).transition("BOM-001", BOMStatus.REVIEW, "alice")
        assert result.success


# ── BOMWorkflow.transition — RELEASED gate ────────────────────────────────────

class TestReleaseGate:
    """REVIEW → RELEASED requires rules pass AND human approval."""

    def _workflow(self, components=None, approval_rows=None) -> tuple:
        """Returns (workflow, client) pre-configured for release attempt."""
        comps = components if components is not None else [
            _comp("P-1", "CRITICAL", "MULTI_SOURCE"),
        ]
        client = MagicMock()
        client.get_bom.return_value = _bom(status="REVIEW")
        client.get_bom_risk_assessment.return_value = _risk(components=comps)
        # get_approval() is backed by execute_query
        client.execute_query.return_value = approval_rows if approval_rows is not None \
            else _approval_row()
        client.execute_write.return_value = None
        engine = _released_rules_engine()
        return BOMWorkflow(client, engine), client

    def test_succeeds_when_rules_pass_and_approved(self):
        workflow, _ = self._workflow()
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert result.success

    def test_rules_result_attached_on_success(self):
        workflow, _ = self._workflow()
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert result.rules_result is not None
        assert result.rules_result.passed

    def test_approval_attached_on_success(self):
        workflow, _ = self._workflow()
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert result.approval is not None
        assert result.approval.approver_id == "alice"

    def test_blocked_when_no_supplier_critical(self):
        workflow, client = self._workflow(
            components=[_comp("P-X", "CRITICAL", "NO_SUPPLIER")],
            approval_rows=_approval_row(),
        )
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert not result.success
        assert "rules gate" in result.reason.lower()

    def test_blocked_when_no_approval(self):
        workflow, _ = self._workflow(approval_rows=[])
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert not result.success
        assert "approval" in result.reason.lower()

    def test_blocked_when_rules_fail_even_with_approval(self):
        workflow, _ = self._workflow(
            components=[_comp("P-X", "HIGH", "NO_SUPPLIER")],
            approval_rows=_approval_row(),
        )
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert not result.success

    def test_write_not_called_when_rules_fail(self):
        workflow, client = self._workflow(
            components=[_comp("P-X", "CRITICAL", "NO_SUPPLIER")],
            approval_rows=_approval_row(),
        )
        workflow.transition("BOM-001", "RELEASED", "alice")
        client.execute_write.assert_not_called()

    def test_write_not_called_when_no_approval(self):
        workflow, client = self._workflow(approval_rows=[])
        workflow.transition("BOM-001", "RELEASED", "alice")
        client.execute_write.assert_not_called()

    def test_single_source_warning_does_not_block(self):
        """Single-sourced HIGH parts should warn but not block release."""
        workflow, _ = self._workflow(
            components=[_comp("P-1", "HIGH", "SINGLE_SOURCE")],
            approval_rows=_approval_row(),
        )
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert result.success

    def test_rules_result_attached_on_failure(self):
        workflow, _ = self._workflow(
            components=[_comp("P-X", "CRITICAL", "NO_SUPPLIER")],
            approval_rows=_approval_row(),
        )
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert result.rules_result is not None
        assert not result.rules_result.passed

    def test_medium_no_supplier_does_not_block(self):
        """Medium-criticality parts with no supplier should not block release."""
        workflow, _ = self._workflow(
            components=[_comp("P-1", "MEDIUM", "NO_SUPPLIER"),
                        _comp("P-2", "CRITICAL", "MULTI_SOURCE")],
            approval_rows=_approval_row(),
        )
        result = workflow.transition("BOM-001", "RELEASED", "alice")
        assert result.success


# ── BOMWorkflow.get_status ────────────────────────────────────────────────────

class TestGetStatus:
    def test_returns_current_status(self):
        client = _make_client(bom=_bom(status="REVIEW"))
        assert BOMWorkflow(client).get_status("BOM-001") == "REVIEW"

    def test_returns_none_when_bom_missing(self):
        client = _make_client(bom=None)
        assert BOMWorkflow(client).get_status("BOM-MISSING") is None


# ── BOMWorkflow.get_transitions ───────────────────────────────────────────────

class TestGetTransitions:
    def _transition_row(self, tid, frm, to, actor="alice") -> Dict:
        return {"transition_id": tid, "from_status": frm, "to_status": to,
                "actor": actor, "timestamp": "2024-01-15T10:00:00", "notes": ""}

    def test_returns_list_of_transition_records(self):
        rows = [self._transition_row("t1", "DRAFT", "REVIEW")]
        client = _make_client(bom=_bom(), transition_rows=rows)
        client.execute_query.return_value = rows
        records = BOMWorkflow(client).get_transitions("BOM-001")
        assert len(records) == 1
        assert isinstance(records[0], TransitionRecord)

    def test_transition_fields_populated(self):
        rows = [self._transition_row("t1", "DRAFT", "REVIEW", actor="bob")]
        client = _make_client(bom=_bom())
        client.execute_query.return_value = rows
        rec = BOMWorkflow(client).get_transitions("BOM-001")[0]
        assert rec.from_status == "DRAFT"
        assert rec.to_status   == "REVIEW"
        assert rec.actor       == "bob"
        assert rec.transition_id == "t1"

    def test_empty_history_returns_empty_list(self):
        client = _make_client(bom=_bom())
        client.execute_query.return_value = []
        assert BOMWorkflow(client).get_transitions("BOM-001") == []


# ── BOMWorkflow.get_approval ──────────────────────────────────────────────────

class TestGetApproval:
    def test_returns_approval_record_when_present(self):
        client = _make_client(bom=_bom())
        client.execute_query.return_value = _approval_row("carol", "OK")
        rec = BOMWorkflow(client).get_approval("BOM-001")
        assert rec is not None
        assert rec.approver_id == "carol"
        assert rec.notes       == "OK"

    def test_returns_none_when_not_approved(self):
        client = _make_client(bom=_bom())
        client.execute_query.return_value = []
        assert BOMWorkflow(client).get_approval("BOM-001") is None


# ── WorkflowResult dataclass ──────────────────────────────────────────────────

class TestWorkflowResult:
    def test_success_true(self):
        r = WorkflowResult(success=True, bom_id="B", from_status="DRAFT",
                           to_status="REVIEW", reason="ok")
        assert r.success

    def test_success_false(self):
        r = WorkflowResult(success=False, bom_id="B", from_status="DRAFT",
                           to_status="RELEASED", reason="blocked")
        assert not r.success

    def test_optional_fields_default_none(self):
        r = WorkflowResult(success=True, bom_id="B", from_status="DRAFT",
                           to_status="REVIEW", reason="ok")
        assert r.rules_result is None
        assert r.approval is None