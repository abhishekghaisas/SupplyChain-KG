"""
Tests for PartCompatibilityRule — graph pre-approval + spec comparison.

The rule has two decision paths:
  1. VERIFIED COMPATIBLE_WITH edge in the graph → pass immediately (db kwarg)
  2. Spec comparison fallback → used when no db or no pre-approved edge

Run with:
    pytest test_part_compatibility_rule.py -v
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock
from typing import Any, Dict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _part(pid: str, category: str = "electronic", voltage: str = "24V DC",
          power: str = "400W", certs: list = None) -> Dict[str, Any]:
    specs = {"voltage": voltage, "power_rating": power,
             "certifications": certs or ["CE", "UL", "RoHS"]}
    return {
        "id":                 pid,
        "category":           category,
        "specifications_json": json.dumps(specs),
    }


def _db_with_compat(original_id: str, substitute_id: str,
                    compat_type: str = "FORM_FIT_FUNCTION",
                    notes: str = "Drop-in replacement") -> MagicMock:
    """Mock db that returns a VERIFIED COMPATIBLE_WITH row."""
    db = MagicMock()
    db.execute_query.return_value = [{
        "compatibility_type": compat_type,
        "notes":              notes,
        "validated_date":     "2024-01-15",
        "validated_by":       "engineering@company.com",
    }]
    return db


def _db_no_compat() -> MagicMock:
    """Mock db that returns no COMPATIBLE_WITH row."""
    db = MagicMock()
    db.execute_query.return_value = []
    return db


def _db_error() -> MagicMock:
    """Mock db whose execute_query raises an exception."""
    db = MagicMock()
    db.execute_query.side_effect = RuntimeError("connection refused")
    return db


# ── Rule metadata ─────────────────────────────────────────────────────────────

class TestRuleMetadata:
    def test_name(self, rule):
        assert rule.name == "PartCompatibilityRule"

    def test_rule_type_is_compatibility(self, rule):
        from src.reasoning.rules_engine import RuleType
        assert rule.rule_type == RuleType.COMPATIBILITY

    def test_severity_is_critical(self, rule):
        from src.reasoning.rules_engine import RuleSeverity
        assert rule.severity == RuleSeverity.CRITICAL


# ── Path 1: graph pre-approval ────────────────────────────────────────────────

class TestGraphPreApproval:
    def test_verified_edge_passes(self, rule):
        """A VERIFIED COMPATIBLE_WITH edge in the graph should pass regardless of specs."""
        original   = _part("P-001", power="400W")
        substitute = _part("P-002", power="450W")   # spec differs — would normally fail
        db = _db_with_compat("P-001", "P-002")

        result = rule.check(original, substitute, db=db)

        assert result.passed

    def test_verified_edge_confidence_is_high(self, rule):
        result = rule.check(_part("P-001"), _part("P-002", power="450W"),
                            db=_db_with_compat("P-001", "P-002"))
        assert result.confidence >= 0.99

    def test_verified_edge_reason_mentions_engineering(self, rule):
        result = rule.check(_part("P-001"), _part("P-002", power="450W"),
                            db=_db_with_compat("P-001", "P-002"))
        assert "engineering" in result.reason.lower() or "verified" in result.reason.lower()

    def test_verified_edge_details_contain_source(self, rule):
        result = rule.check(_part("P-001"), _part("P-002", power="450W"),
                            db=_db_with_compat("P-001", "P-002"))
        assert result.details["source"] == "COMPATIBLE_WITH_relationship"

    def test_verified_edge_details_contain_notes(self, rule):
        result = rule.check(_part("P-001"), _part("P-002", power="450W"),
                            db=_db_with_compat("P-001", "P-002", notes="SM-450 is drop-in"))
        assert "SM-450" in result.details["notes"]

    def test_graph_lookup_uses_correct_ids(self, rule):
        db = _db_with_compat("P-001", "P-002")
        rule.check(_part("P-001"), _part("P-002"), db=db)
        call_params = db.execute_query.call_args[0][1]
        assert call_params["orig_id"] == "P-001"
        assert call_params["sub_id"]  == "P-002"

    def test_graph_source_in_facts_used(self, rule):
        result = rule.check(_part("P-001"), _part("P-002", power="450W"),
                            db=_db_with_compat("P-001", "P-002"))
        assert any("COMPATIBLE_WITH" in f for f in result.facts_used)

    def test_no_verified_edge_falls_through_to_spec(self, rule):
        """When graph has no COMPATIBLE_WITH edge, spec comparison runs."""
        # Same specs — should pass via spec comparison
        result = rule.check(_part("P-001"), _part("P-002"), db=_db_no_compat())
        assert result.passed

    def test_no_verified_edge_spec_mismatch_fails(self, rule):
        """No pre-approved edge + spec mismatch = fail."""
        result = rule.check(
            _part("P-001", power="400W"),
            _part("P-002", power="450W"),
            db=_db_no_compat(),
        )
        assert not result.passed
        assert "power_rating" in result.reason

    def test_db_error_falls_through_to_spec(self, rule):
        """If the graph lookup raises, the rule should not crash — fall through to spec."""
        # Same specs — spec comparison should pass
        result = rule.check(_part("P-001"), _part("P-002"), db=_db_error())
        assert result.passed   # spec comparison path

    def test_db_error_with_spec_mismatch_fails(self, rule):
        """DB error + spec mismatch = spec comparison still runs and fails."""
        result = rule.check(
            _part("P-001", power="400W"),
            _part("P-002", power="450W"),
            db=_db_error(),
        )
        assert not result.passed


# ── Path 2: spec-based comparison (no db) ────────────────────────────────────

class TestSpecComparison:
    def test_no_db_same_specs_passes(self, rule):
        result = rule.check(_part("P-001"), _part("P-002"))
        assert result.passed

    def test_no_db_power_mismatch_fails(self, rule):
        result = rule.check(_part("P-001", power="400W"), _part("P-002", power="450W"))
        assert not result.passed
        assert "power_rating" in result.reason

    def test_no_db_voltage_mismatch_fails(self, rule):
        result = rule.check(_part("P-001", voltage="24V DC"), _part("P-002", voltage="48V DC"))
        assert not result.passed
        assert "voltage" in result.reason

    def test_no_db_category_mismatch_fails(self, rule):
        result = rule.check(_part("P-001", category="electronic"),
                            _part("P-002", category="mechanical"))
        assert not result.passed
        assert "category" in result.reason.lower()

    def test_no_db_missing_certification_fails(self, rule):
        result = rule.check(
            _part("P-001", certs=["CE", "UL", "RoHS"]),
            _part("P-002", certs=["CE", "UL"]),   # missing RoHS
        )
        assert not result.passed
        assert "RoHS" in result.reason

    def test_no_db_superset_certs_passes(self, rule):
        """Substitute with more certs than original should pass."""
        result = rule.check(
            _part("P-001", certs=["CE", "UL"]),
            _part("P-002", certs=["CE", "UL", "RoHS", "ISO9001"]),
        )
        assert result.passed

    def test_no_db_invalid_json_fails(self, rule):
        bad_part = {"id": "P-BAD", "category": "electronic",
                    "specifications_json": "not json"}
        result = rule.check(bad_part, _part("P-002"))
        assert not result.passed
        assert result.confidence == 0.0

    def test_no_db_part_ids_in_facts(self, rule):
        rule.check(_part("P-001"), _part("P-002"))
        assert "original_part:P-001"   in rule.facts_used
        assert "substitute_part:P-002" in rule.facts_used


# ── Real-world scenario: P-12345 → P-67890 ───────────────────────────────────

class TestRealWorldScenario:
    """The exact scenario from the live system."""

    P_12345 = {
        "id":       "P-12345",
        "category": "electronic",
        "specifications_json": json.dumps({
            "power_rating": "400W",
            "voltage":      "24V DC",
            "certifications": ["CE", "UL", "RoHS"],
        }),
    }
    P_67890 = {
        "id":       "P-67890",
        "category": "electronic",
        "specifications_json": json.dumps({
            "power_rating": "450W",           # differs — would fail spec check
            "voltage":      "24V DC",
            "certifications": ["CE", "UL", "RoHS", "ISO9001"],
        }),
    }

    def test_without_db_fails_on_power_rating(self, rule):
        """Spec comparison alone flags 400W vs 450W as a mismatch."""
        result = rule.check(self.P_12345, self.P_67890)
        assert not result.passed
        assert "power_rating" in result.reason

    def test_with_db_verified_edge_passes(self, rule):
        """With a VERIFIED COMPATIBLE_WITH edge, the same pair should pass."""
        db = _db_with_compat(
            "P-12345", "P-67890",
            compat_type="FORM_FIT_FUNCTION",
            notes="SM-450 is drop-in replacement with better performance",
        )
        result = rule.check(self.P_12345, self.P_67890, db=db)
        assert result.passed
        assert "drop-in" in result.reason or "verified" in result.reason.lower()

    def test_with_db_no_edge_still_fails(self, rule):
        """If the graph has no pre-approved edge, spec comparison runs and fails."""
        result = rule.check(self.P_12345, self.P_67890, db=_db_no_compat())
        assert not result.passed


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def rule():
    from src.reasoning.supply_chain_rules import PartCompatibilityRule
    return PartCompatibilityRule()