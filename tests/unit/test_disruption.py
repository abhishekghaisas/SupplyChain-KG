"""
Unit tests for DisruptionAnalyzer, DisruptedPart, and DisruptionReport.

No Neo4j required — the client is fully mocked.

Run with:
    pytest test_disruption.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call
from typing import Any, Dict, List, Optional

from src.bom.disruption import (
    DEFAULT_BOM_STATUSES,
    AffectedBOM,
    CRITICALITY_WEIGHT,
    DisruptedPart,
    DisruptionAnalyzer,
    DisruptionReport,
    RecommendedAction,
    SubstituteInfo,
)


# ── Fixtures / builders ───────────────────────────────────────────────────────

def _supplier(sid="SUP-001", name="Precision Motors Inc", status="ACTIVE"):
    return {"id": sid, "name": name, "status": status}


def _part(pid="P-001", name="Servo Motor", criticality="HIGH"):
    return {"id": pid, "name": name, "criticality": criticality}


def _bom_row(bom_id="BOM-001", part_id="P-001", qty=2.0,
             status="RELEASED", version="1.0"):
    return {"bom_id": bom_id, "bom_name": f"BOM {bom_id}", "bom_version": version,
            "bom_status": status, "part_id": part_id, "quantity": qty}


def _sub(part_id="P-002", name="Servo Motor Alt", vtype="FORM_FIT_FUNCTION"):
    return SubstituteInfo(part_id=part_id, part_name=name,
                          compatibility_type=vtype, validation_status="VERIFIED",
                          constraints={}, notes="Drop-in replacement")


def _make_client(
    supplier=None,
    part=None,
    supplied_parts=None,
    bom_rows=None,
    alternate_count=0,
    substitutes=None,
) -> MagicMock:
    client = MagicMock()

    def _execute_query(query, params=None):
        params = params or {}
        # supplier lookup
        if "Supplier {id:" in query and "RETURN s.id" in query:
            return [supplier] if supplier else []
        # part lookup
        if "Part {id:" in query and "RETURN p.id" in query and "SUPPLIES" not in query:
            return [part] if part else []
        # supplied parts — _fetch_supplied_parts returns part_id/part_name/criticality
        if "SUPPLIES" in query and "RETURN p.id" in query:
            rows = supplied_parts or []
            return [
                {"part_id": r.get("part_id", r.get("id")),
                 "part_name": r.get("part_name", r.get("name", "")),
                 "criticality": r.get("criticality", "HIGH")}
                for r in rows
            ]
        # BOM rows
        if "CONTAINS" in query and "REFERENCES" in query:
            return bom_rows or []
        # alternate count
        if "count(s)" in query:
            return [{"cnt": alternate_count}]
        # substitutes
        if "COMPATIBLE_WITH" in query:
            if substitutes is not None:
                return [{"part_id": s.part_id, "part_name": s.part_name,
                         "compatibility_type": s.compatibility_type,
                         "validation_status": s.validation_status,
                         "constraints_json": "{}", "notes": s.notes}
                        for s in substitutes]
            return []
        return []

    client.execute_query.side_effect = _execute_query
    return client


def _disrupted_part(
    part_id="P-001", criticality="HIGH",
    alt_count=0, substitutes=None, qty=1.0,
) -> DisruptedPart:
    return DisruptedPart(
        part_id=part_id, part_name=f"Part {part_id}",
        criticality=criticality, quantity_in_bom=qty,
        alternate_supplier_count=alt_count,
        substitutes=substitutes or [],
    )


# ── CRITICALITY_WEIGHT ────────────────────────────────────────────────────────

class TestCriticalityWeight:
    def test_critical_is_highest(self):
        assert CRITICALITY_WEIGHT["CRITICAL"] == 1.0

    def test_ordering(self):
        assert (CRITICALITY_WEIGHT["CRITICAL"] > CRITICALITY_WEIGHT["HIGH"]
                > CRITICALITY_WEIGHT["MEDIUM"] > CRITICALITY_WEIGHT["LOW"])


# ── DisruptedPart ─────────────────────────────────────────────────────────────

class TestDisruptedPart:

    # sourcing risk key
    def test_no_alternate_no_sub(self):
        dp = _disrupted_part(alt_count=0, substitutes=[])
        assert dp.sourcing_risk_key == "no_alternate_no_substitute"

    def test_no_alternate_has_sub(self):
        dp = _disrupted_part(alt_count=0, substitutes=[_sub()])
        assert dp.sourcing_risk_key == "no_alternate_has_substitute"

    def test_one_alternate(self):
        dp = _disrupted_part(alt_count=1)
        assert dp.sourcing_risk_key == "has_alternate"

    def test_multi_alternate(self):
        dp = _disrupted_part(alt_count=2)
        assert dp.sourcing_risk_key == "multi_alternate"

    # severity contribution
    def test_critical_no_alternate_is_1(self):
        dp = _disrupted_part(criticality="CRITICAL", alt_count=0)
        assert dp.severity_contribution() == pytest.approx(1.0)

    def test_high_no_alternate_is_0_75(self):
        dp = _disrupted_part(criticality="HIGH", alt_count=0)
        assert dp.severity_contribution() == pytest.approx(0.75)

    def test_alternate_reduces_severity(self):
        base = _disrupted_part(criticality="HIGH", alt_count=0).severity_contribution()
        with_alt = _disrupted_part(criticality="HIGH", alt_count=1).severity_contribution()
        assert with_alt < base

    def test_substitute_reduces_severity_vs_no_option(self):
        no_option = _disrupted_part(criticality="HIGH", alt_count=0, substitutes=[])
        has_sub   = _disrupted_part(criticality="HIGH", alt_count=0, substitutes=[_sub()])
        assert has_sub.severity_contribution() < no_option.severity_contribution()

    # recommended actions
    def test_escalate_when_critical_no_options(self):
        dp = _disrupted_part(criticality="CRITICAL", alt_count=0, substitutes=[])
        assert RecommendedAction.ESCALATE in dp.recommended_actions()

    def test_use_substitute_when_available(self):
        dp = _disrupted_part(alt_count=0, substitutes=[_sub()])
        assert RecommendedAction.USE_SUBSTITUTE in dp.recommended_actions()

    def test_expedite_when_alternate_exists(self):
        dp = _disrupted_part(alt_count=1)
        assert RecommendedAction.EXPEDITE_ALTERNATE in dp.recommended_actions()

    def test_dual_source_when_single_alternate_critical(self):
        dp = _disrupted_part(criticality="CRITICAL", alt_count=1)
        assert RecommendedAction.DUAL_SOURCE in dp.recommended_actions()

    def test_no_escalate_when_substitute_exists(self):
        dp = _disrupted_part(criticality="CRITICAL", alt_count=0, substitutes=[_sub()])
        assert RecommendedAction.ESCALATE not in dp.recommended_actions()

    def test_monitor_for_low_no_options(self):
        dp = _disrupted_part(criticality="LOW", alt_count=0, substitutes=[])
        assert RecommendedAction.MONITOR in dp.recommended_actions()

    def test_no_duplicate_actions(self):
        dp = _disrupted_part(criticality="HIGH", alt_count=1, substitutes=[_sub()])
        actions = dp.recommended_actions()
        assert len(actions) == len(set(actions))

    def test_has_alternate_supplier_property(self):
        assert not _disrupted_part(alt_count=0).has_alternate_supplier
        assert     _disrupted_part(alt_count=1).has_alternate_supplier

    def test_has_substitute_property(self):
        assert not _disrupted_part(substitutes=[]).has_substitute
        assert     _disrupted_part(substitutes=[_sub()]).has_substitute


# ── DisruptionReport ──────────────────────────────────────────────────────────

class TestDisruptionReport:
    def _report(self, affected_boms=None) -> DisruptionReport:
        return DisruptionReport(
            scenario="SUPPLIER", disrupted_id="SUP-001",
            disrupted_name="Precision Motors", bom_statuses=["RELEASED"],
            affected_boms=affected_boms or [], total_parts_affected=1,
        )

    def _bom(self, score, label_expected) -> AffectedBOM:
        return AffectedBOM(
            bom_id="B", bom_name="BOM", bom_version="1.0", bom_status="RELEASED",
            disrupted_parts=[], severity_score=score,
            actions=[RecommendedAction.ESCALATE],
        )

    def test_summary_no_boms(self):
        assert "0 BOM(s)" in self._report().summary

    def test_summary_counts_boms(self):
        bom = AffectedBOM("B","BOM","1.0","RELEASED",[],0.9,[RecommendedAction.ESCALATE])
        r = self._report([bom])
        assert "1 BOM(s)" in r.summary

    def test_critical_boms_filtered(self):
        high = AffectedBOM("B1","BOM","1","RELEASED",[],0.9,[])
        low  = AffectedBOM("B2","BOM","1","RELEASED",[],0.1,[])
        r = self._report([high, low])
        assert len(r.critical_boms) == 1
        assert r.critical_boms[0].bom_id == "B1"

    def test_format_report_is_string(self):
        assert isinstance(self._report().format_report(), str)

    def test_format_report_contains_disrupted_name(self):
        assert "Precision Motors" in self._report().format_report()

    def test_format_report_contains_bom_id(self):
        bom = AffectedBOM("BOM-XYZ","name","1","RELEASED",[],0.5,[RecommendedAction.MONITOR])
        assert "BOM-XYZ" in self._report([bom]).format_report()

    def test_format_report_sorted_by_severity(self):
        high = AffectedBOM("HIGH","high","1","RELEASED",[],0.9,[])
        low  = AffectedBOM("LOW","low","1","RELEASED",[],0.1,[])
        report = self._report([low, high]).format_report()
        assert report.index("HIGH") < report.index("LOW")


# ── AffectedBOM.severity_label ────────────────────────────────────────────────

class TestSeverityLabel:
    def _bom(self, score):
        return AffectedBOM("B","BOM","1","RELEASED",[],score,[])

    def test_critical_label(self):
        assert self._bom(0.9).severity_label == "CRITICAL"

    def test_high_label(self):
        assert self._bom(0.6).severity_label == "HIGH"

    def test_medium_label(self):
        assert self._bom(0.3).severity_label == "MEDIUM"

    def test_low_label(self):
        assert self._bom(0.1).severity_label == "LOW"

    def test_boundary_0_8_is_critical(self):
        assert self._bom(0.8).severity_label == "CRITICAL"

    def test_boundary_0_5_is_high(self):
        assert self._bom(0.5).severity_label == "HIGH"


# ── DisruptionAnalyzer.analyze_supplier_disruption ────────────────────────────

class TestSupplierDisruption:
    def test_raises_when_supplier_not_found(self):
        client = _make_client(supplier=None)
        with pytest.raises(ValueError, match="SUP-001"):
            DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")

    def test_returns_report(self):
        client = _make_client(
            supplier=_supplier(), supplied_parts=[_part()],
            bom_rows=[_bom_row()], alternate_count=1,
        )
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert isinstance(report, DisruptionReport)

    def test_scenario_is_supplier(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=[_bom_row()], alternate_count=1)
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert report.scenario == "SUPPLIER"

    def test_disrupted_name_is_supplier_name(self):
        client = _make_client(supplier=_supplier("SUP-001", "Acme Corp"),
                              supplied_parts=[_part()], bom_rows=[_bom_row()])
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert report.disrupted_name == "Acme Corp"

    def test_no_supplied_parts_returns_empty(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[])
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert report.affected_boms == []
        assert report.total_parts_affected == 0

    def test_affected_bom_present(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=[_bom_row()], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert len(report.affected_boms) == 1

    def test_bom_statuses_passed_to_query(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=[])
        DisruptionAnalyzer(client).analyze_supplier_disruption(
            "SUP-001", bom_statuses=["DRAFT", "REVIEW"]
        )
        # Find the BOM query call and check statuses
        bom_call = next(
            (c for c in client.execute_query.call_args_list
             if "CONTAINS" in c[0][0]),
            None,
        )
        assert bom_call is not None
        assert bom_call[0][1]["bom_statuses"] == ["DRAFT", "REVIEW"]

    def test_default_scope_is_released(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=[])
        DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        bom_call = next(
            c for c in client.execute_query.call_args_list if "CONTAINS" in c[0][0]
        )
        assert bom_call[0][1]["bom_statuses"] == list(DEFAULT_BOM_STATUSES)

    def test_severity_score_present(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=[_bom_row()], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert 0.0 <= report.affected_boms[0].severity_score <= 1.0

    def test_actions_present(self):
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=[_bom_row()], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert report.affected_boms[0].actions

    def test_escalate_when_critical_no_alternate(self):
        client = _make_client(
            supplier=_supplier(),
            supplied_parts=[_part("P-001", criticality="CRITICAL")],
            bom_rows=[_bom_row()], alternate_count=0,
        )
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert RecommendedAction.ESCALATE in report.affected_boms[0].actions

    def test_use_substitute_when_available(self):
        client = _make_client(
            supplier=_supplier(), supplied_parts=[_part()],
            bom_rows=[_bom_row()], alternate_count=0,
            substitutes=[_sub()],
        )
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert RecommendedAction.USE_SUBSTITUTE in report.affected_boms[0].actions

    def test_multiple_boms_affected(self):
        rows = [_bom_row("BOM-001"), _bom_row("BOM-002")]
        client = _make_client(supplier=_supplier(), supplied_parts=[_part()],
                              bom_rows=rows, alternate_count=1)
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert len(report.affected_boms) == 2

    def test_total_parts_affected_count(self):
        parts = [_part("P-001"), _part("P-002")]
        client = _make_client(supplier=_supplier(), supplied_parts=parts,
                              bom_rows=[], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_supplier_disruption("SUP-001")
        assert report.total_parts_affected == 2


# ── DisruptionAnalyzer.analyze_part_disruption ────────────────────────────────

class TestPartDisruption:
    def test_raises_when_part_not_found(self):
        client = _make_client(part=None)
        with pytest.raises(ValueError, match="P-001"):
            DisruptionAnalyzer(client).analyze_part_disruption("P-001")

    def test_returns_report(self):
        client = _make_client(part=_part(), bom_rows=[_bom_row()], alternate_count=1)
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert isinstance(report, DisruptionReport)

    def test_scenario_is_part(self):
        client = _make_client(part=_part(), bom_rows=[_bom_row()], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert report.scenario == "PART"

    def test_disrupted_name_is_part_name(self):
        client = _make_client(part=_part("P-001", "Controller Board"),
                              bom_rows=[_bom_row()], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert report.disrupted_name == "Controller Board"

    def test_total_parts_affected_is_1(self):
        client = _make_client(part=_part(), bom_rows=[], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert report.total_parts_affected == 1

    def test_no_boms_returns_empty(self):
        client = _make_client(part=_part(), bom_rows=[], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert report.affected_boms == []

    def test_affected_bom_present(self):
        client = _make_client(part=_part(), bom_rows=[_bom_row()], alternate_count=0)
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert len(report.affected_boms) == 1

    def test_substitute_surfaced(self):
        client = _make_client(part=_part(), bom_rows=[_bom_row()],
                              alternate_count=0, substitutes=[_sub()])
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        dp = report.affected_boms[0].disrupted_parts[0]
        assert dp.has_substitute
        assert dp.substitutes[0].part_id == "P-002"

    def test_severity_clamped_to_1(self):
        client = _make_client(
            part=_part("P-001", criticality="CRITICAL"),
            bom_rows=[_bom_row()], alternate_count=0,
        )
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert report.affected_boms[0].severity_score <= 1.0

    def test_bom_statuses_respected(self):
        client = _make_client(part=_part(), bom_rows=[])
        DisruptionAnalyzer(client).analyze_part_disruption(
            "P-001", bom_statuses=["DRAFT"]
        )
        bom_call = next(
            c for c in client.execute_query.call_args_list if "CONTAINS" in c[0][0]
        )
        assert bom_call[0][1]["bom_statuses"] == ["DRAFT"]

    def test_critical_part_no_options_escalates(self):
        client = _make_client(
            part=_part("P-001", criticality="CRITICAL"),
            bom_rows=[_bom_row()], alternate_count=0, substitutes=[],
        )
        report = DisruptionAnalyzer(client).analyze_part_disruption("P-001")
        assert RecommendedAction.ESCALATE in report.affected_boms[0].actions


# ── DisruptionAnalyzer._compute_severity (static) ────────────────────────────

class TestComputeSeverity:
    def test_empty_parts_is_zero(self):
        assert DisruptionAnalyzer._compute_severity([]) == 0.0

    def test_single_critical_no_alt_is_1(self):
        dp = _disrupted_part(criticality="CRITICAL", alt_count=0)
        assert DisruptionAnalyzer._compute_severity([dp]) == pytest.approx(1.0)

    def test_max_of_parts_used(self):
        low  = _disrupted_part(criticality="LOW",  alt_count=0)
        high = _disrupted_part(criticality="HIGH", alt_count=0)
        assert DisruptionAnalyzer._compute_severity([low, high]) == pytest.approx(0.75)

    def test_clamped_to_1(self):
        dp = _disrupted_part(criticality="CRITICAL", alt_count=0)
        score = DisruptionAnalyzer._compute_severity([dp, dp])
        assert score <= 1.0


# ── DisruptionAnalyzer._aggregate_actions (static) ───────────────────────────

class TestAggregateActions:
    def test_escalate_has_highest_priority(self):
        dp1 = _disrupted_part(criticality="CRITICAL", alt_count=0)
        dp2 = _disrupted_part(criticality="LOW",      alt_count=2)
        actions = DisruptionAnalyzer._aggregate_actions([dp1, dp2])
        assert actions[0] == RecommendedAction.ESCALATE

    def test_union_of_all_parts(self):
        dp1 = _disrupted_part(criticality="CRITICAL", alt_count=0, substitutes=[])
        dp2 = _disrupted_part(criticality="HIGH",     alt_count=0, substitutes=[_sub()])
        actions = DisruptionAnalyzer._aggregate_actions([dp1, dp2])
        assert RecommendedAction.ESCALATE       in actions
        assert RecommendedAction.USE_SUBSTITUTE in actions

    def test_no_duplicates(self):
        dp1 = _disrupted_part(criticality="HIGH", alt_count=0)
        dp2 = _disrupted_part(criticality="HIGH", alt_count=0)
        actions = DisruptionAnalyzer._aggregate_actions([dp1, dp2])
        assert len(actions) == len(set(actions))

    def test_empty_parts_returns_empty(self):
        assert DisruptionAnalyzer._aggregate_actions([]) == []