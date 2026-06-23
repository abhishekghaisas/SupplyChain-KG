"""
Unit tests for BOMVersionManager (clone + diff).

The Neo4j client is fully mocked — no database required.

Run with:
    pytest test_bom_versioning.py -v
"""

from __future__ import annotations

import pytest
from copy import deepcopy
from unittest.mock import MagicMock, patch, call
from typing import Any, Dict, List, Optional

from src.bom.versioning import (
    BOMVersionManager,
    BOMDiff,
    ComponentSnapshot,
    ComponentChange,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_bom(bom_id: str, version: str, status: str = "RELEASED") -> Dict[str, Any]:
    return {"id": bom_id, "name": f"BOM {bom_id}", "description": "desc",
            "version": version, "status": status}


def _make_component(part_id: str, qty: float = 1.0, uom: str = "EA",
                    ref: str = "", notes: str = "", **kwargs) -> Dict[str, Any]:
    return {
        "component_id": f"COMP-BOM-{part_id}",
        "part_id": part_id,
        "part_name": kwargs.get("part_name", f"Part {part_id}"),
        "category": kwargs.get("category", "electronic"),
        "criticality": kwargs.get("criticality", "HIGH"),
        "quantity": qty,
        "unit_of_measure": uom,
        "reference_designator": ref,
        "notes": notes,
    }


def _make_client(
    source_bom: Optional[Dict] = None,
    new_bom_exists: bool = False,
    components: Optional[List[Dict]] = None,
) -> MagicMock:
    """Return a mock Neo4jClient pre-configured for common scenarios."""
    client = MagicMock()

    def _get_bom(bom_id):
        if bom_id == (source_bom or {}).get("id"):
            return source_bom
        if new_bom_exists and bom_id == "BOM-NEW":
            return _make_bom("BOM-NEW", "2.0")
        return None

    client.get_bom.side_effect = _get_bom
    client.get_bom_components.return_value = components or []
    # clone uses execute_query; return a plausible row
    client.execute_query.return_value = [{"components_cloned": len(components or [])}]
    return client


# ── ComponentSnapshot.diff_against ───────────────────────────────────────────

class TestComponentSnapshotDiff:
    def _snap(self, **kwargs) -> ComponentSnapshot:
        defaults = dict(part_id="P-001", part_name="Part", category="electronic",
                        criticality="HIGH", quantity=1.0, unit_of_measure="EA",
                        reference_designator="R1", notes="")
        defaults.update(kwargs)
        return ComponentSnapshot(**defaults)

    def test_identical_returns_empty(self):
        s = self._snap()
        assert s.diff_against(s) == {}

    def test_quantity_change_detected(self):
        a = self._snap(quantity=1.0)
        b = self._snap(quantity=2.0)
        diff = a.diff_against(b)
        assert "quantity" in diff
        assert diff["quantity"] == {"from": 1.0, "to": 2.0}

    def test_uom_change_detected(self):
        a = self._snap(unit_of_measure="EA")
        b = self._snap(unit_of_measure="KG")
        diff = a.diff_against(b)
        assert "unit_of_measure" in diff
        assert diff["unit_of_measure"] == {"from": "EA", "to": "KG"}

    def test_notes_change_detected(self):
        a = self._snap(notes="")
        b = self._snap(notes="Updated note")
        diff = a.diff_against(b)
        assert "notes" in diff

    def test_multiple_changes_all_reported(self):
        a = self._snap(quantity=1.0, unit_of_measure="EA", notes="")
        b = self._snap(quantity=3.0, unit_of_measure="KG", notes="changed")
        diff = a.diff_against(b)
        assert len(diff) == 3

    def test_non_diffable_fields_ignored(self):
        # category and criticality are not in DIFFABLE_FIELDS
        a = self._snap(category="electronic", criticality="HIGH")
        b = self._snap(category="mechanical", criticality="LOW")
        assert a.diff_against(b) == {}


# ── BOMDiff properties ────────────────────────────────────────────────────────

class TestBOMDiff:
    def _diff(self, added=None, removed=None, modified=None) -> BOMDiff:
        return BOMDiff(
            bom_id_a="BOM-A", bom_id_b="BOM-B",
            version_a="1.0", version_b="2.0",
            added=added or [], removed=removed or [], modified=modified or [],
        )

    def _snap(self, part_id="P-001") -> ComponentSnapshot:
        return ComponentSnapshot(part_id=part_id, part_name="Part", category="electronic",
                                 criticality="HIGH", quantity=1.0, unit_of_measure="EA",
                                 reference_designator="", notes="")

    def _change(self) -> ComponentChange:
        return ComponentChange(part_id="P-001", part_name="Part",
                               changes={"quantity": {"from": 1.0, "to": 2.0}})

    def test_has_changes_false_when_empty(self):
        assert not self._diff().has_changes

    def test_has_changes_true_with_added(self):
        assert self._diff(added=[self._snap()]).has_changes

    def test_has_changes_true_with_removed(self):
        assert self._diff(removed=[self._snap()]).has_changes

    def test_has_changes_true_with_modified(self):
        assert self._diff(modified=[self._change()]).has_changes

    def test_summary_no_changes(self):
        assert self._diff().summary == "no changes"

    def test_summary_all_three(self):
        d = self._diff(added=[self._snap("P-A")], removed=[self._snap("P-R")],
                       modified=[self._change()])
        assert "1 added" in d.summary
        assert "1 removed" in d.summary
        assert "1 modified" in d.summary

    def test_to_dict_keys(self):
        d = self._diff()
        result = d.to_dict()
        assert set(result.keys()) >= {"bom_id_a", "bom_id_b", "version_a", "version_b",
                                      "summary", "added", "removed", "modified"}

    def test_format_report_contains_ids(self):
        report = self._diff().format_report()
        assert "BOM-A" in report
        assert "BOM-B" in report

    def test_format_report_shows_added_part(self):
        d = self._diff(added=[self._snap("P-NEW")])
        report = d.format_report()
        assert "P-NEW" in report
        assert "+" in report

    def test_format_report_shows_removed_part(self):
        d = self._diff(removed=[self._snap("P-OLD")])
        report = d.format_report()
        assert "P-OLD" in report
        assert "-" in report

    def test_format_report_shows_modified_fields(self):
        d = self._diff(modified=[self._change()])
        report = d.format_report()
        assert "quantity" in report
        assert "1.0" in report
        assert "2.0" in report


# ── BOMVersionManager.clone ───────────────────────────────────────────────────

class TestClone:
    SOURCE = _make_bom("BOM-V1", "1.0", "RELEASED")
    COMPONENTS = [
        _make_component("P-12345", qty=2.0, ref="U1"),
        _make_component("P-67890", qty=1.0, ref="U2"),
    ]

    def test_returns_new_bom_id(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        mgr = BOMVersionManager(client)
        result = mgr.clone("BOM-V1", "BOM-V2", "2.0")
        assert result == "BOM-V2"

    def test_raises_when_source_not_found(self):
        client = _make_client(source_bom=None)
        mgr = BOMVersionManager(client)
        with pytest.raises(ValueError, match="BOM-V1"):
            mgr.clone("BOM-V1", "BOM-V2", "2.0")

    def test_raises_when_new_id_already_exists(self):
        client = _make_client(source_bom=self.SOURCE, new_bom_exists=True)
        # Patch get_bom to return something for BOM-NEW too
        client.get_bom.side_effect = lambda bid: (
            self.SOURCE if bid == "BOM-V1" else _make_bom(bid, "2.0")
        )
        mgr = BOMVersionManager(client)
        with pytest.raises(ValueError, match="BOM-NEW"):
            mgr.clone("BOM-V1", "BOM-NEW", "2.0")

    def test_execute_query_called_with_source_id(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0")
        args = client.execute_query.call_args
        assert args[0][1]["source_bom_id"] == "BOM-V1"

    def test_execute_query_called_with_new_id(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0")
        args = client.execute_query.call_args
        assert args[0][1]["new_bom_id"] == "BOM-V2"

    def test_execute_query_called_with_new_version(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0")
        args = client.execute_query.call_args
        assert args[0][1]["new_version"] == "2.0"

    def test_name_override(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0", new_name="Custom Name")
        args = client.execute_query.call_args
        assert args[0][1]["new_name"] == "Custom Name"

    def test_name_falls_back_to_source(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0")
        args = client.execute_query.call_args
        assert args[0][1]["new_name"] == self.SOURCE["name"]

    def test_default_status_is_draft(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0")
        args = client.execute_query.call_args
        assert args[0][1]["new_status"] == "DRAFT"

    def test_custom_status(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0", new_status="REVIEW")
        args = client.execute_query.call_args
        assert args[0][1]["new_status"] == "REVIEW"

    def test_cloned_by_passed_through(self):
        client = _make_client(source_bom=self.SOURCE, components=self.COMPONENTS)
        BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0", cloned_by="alice")
        args = client.execute_query.call_args
        assert args[0][1]["cloned_by"] == "alice"

    def test_empty_bom_cloned_without_error(self):
        client = _make_client(source_bom=self.SOURCE, components=[])
        result = BOMVersionManager(client).clone("BOM-V1", "BOM-V2", "2.0")
        assert result == "BOM-V2"


# ── BOMVersionManager.diff ────────────────────────────────────────────────────

class TestDiff:
    BOM_A = _make_bom("BOM-A", "1.0")
    BOM_B = _make_bom("BOM-B", "2.0")

    COMPS_A = [
        _make_component("P-001", qty=2.0, uom="EA"),
        _make_component("P-002", qty=1.0, uom="EA"),
        _make_component("P-003", qty=5.0, uom="EA"),   # will be removed in B
    ]
    COMPS_B = [
        _make_component("P-001", qty=4.0, uom="EA"),   # qty changed
        _make_component("P-002", qty=1.0, uom="KG"),   # uom changed
        _make_component("P-004", qty=1.0, uom="EA"),   # new part
    ]

    def _make_client(self):
        client = MagicMock()
        client.get_bom.side_effect = lambda bid: (
            self.BOM_A if bid == "BOM-A" else
            self.BOM_B if bid == "BOM-B" else None
        )
        client.get_bom_components.side_effect = lambda bid: (
            deepcopy(self.COMPS_A) if bid == "BOM-A" else deepcopy(self.COMPS_B)
        )
        return client

    def test_returns_bomdiff(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        assert isinstance(result, BOMDiff)

    def test_bom_ids_in_result(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        assert result.bom_id_a == "BOM-A"
        assert result.bom_id_b == "BOM-B"

    def test_versions_in_result(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        assert result.version_a == "1.0"
        assert result.version_b == "2.0"

    def test_added_part_detected(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        added_ids = [c.part_id for c in result.added]
        assert "P-004" in added_ids

    def test_removed_part_detected(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        removed_ids = [c.part_id for c in result.removed]
        assert "P-003" in removed_ids

    def test_quantity_change_detected(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        modified_ids = {c.part_id: c for c in result.modified}
        assert "P-001" in modified_ids
        assert "quantity" in modified_ids["P-001"].changes
        assert modified_ids["P-001"].changes["quantity"] == {"from": 2.0, "to": 4.0}

    def test_uom_change_detected(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        modified_ids = {c.part_id: c for c in result.modified}
        assert "P-002" in modified_ids
        assert "unit_of_measure" in modified_ids["P-002"].changes

    def test_unchanged_parts_not_in_modified(self):
        # P-002 has a UOM change, P-001 has qty change; neither should be in added/removed
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        added_ids = {c.part_id for c in result.added}
        removed_ids = {c.part_id for c in result.removed}
        assert "P-001" not in added_ids and "P-001" not in removed_ids
        assert "P-002" not in added_ids and "P-002" not in removed_ids

    def test_raises_when_bom_a_not_found(self):
        client = MagicMock()
        client.get_bom.side_effect = lambda bid: (
            self.BOM_B if bid == "BOM-B" else None
        )
        with pytest.raises(ValueError, match="BOM-MISSING"):
            BOMVersionManager(client).diff("BOM-MISSING", "BOM-B")

    def test_raises_when_bom_b_not_found(self):
        client = MagicMock()
        client.get_bom.side_effect = lambda bid: (
            self.BOM_A if bid == "BOM-A" else None
        )
        with pytest.raises(ValueError, match="BOM-MISSING"):
            BOMVersionManager(client).diff("BOM-A", "BOM-MISSING")

    def test_identical_boms_no_changes(self):
        client = MagicMock()
        client.get_bom.side_effect = lambda bid: _make_bom(bid, "1.0")
        client.get_bom_components.return_value = deepcopy(self.COMPS_A)
        result = BOMVersionManager(client).diff("BOM-A", "BOM-B")
        assert not result.has_changes

    def test_empty_to_non_empty_all_added(self):
        client = MagicMock()
        client.get_bom.side_effect = lambda bid: _make_bom(bid, "1.0")
        client.get_bom_components.side_effect = lambda bid: (
            [] if bid == "BOM-A" else deepcopy(self.COMPS_B)
        )
        result = BOMVersionManager(client).diff("BOM-A", "BOM-B")
        assert len(result.added) == len(self.COMPS_B)
        assert not result.removed
        assert not result.modified

    def test_non_empty_to_empty_all_removed(self):
        client = MagicMock()
        client.get_bom.side_effect = lambda bid: _make_bom(bid, "1.0")
        client.get_bom_components.side_effect = lambda bid: (
            deepcopy(self.COMPS_A) if bid == "BOM-A" else []
        )
        result = BOMVersionManager(client).diff("BOM-A", "BOM-B")
        assert len(result.removed) == len(self.COMPS_A)
        assert not result.added
        assert not result.modified

    def test_has_changes_true(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        assert result.has_changes

    def test_summary_non_empty(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        assert result.summary != "no changes"

    def test_format_report_string(self):
        result = BOMVersionManager(self._make_client()).diff("BOM-A", "BOM-B")
        report = result.format_report()
        assert isinstance(report, str)
        assert len(report) > 0


# ── _compute_diff (static, pure function) ────────────────────────────────────

class TestComputeDiff:
    def _snap(self, part_id, qty=1.0, uom="EA", notes="") -> ComponentSnapshot:
        return ComponentSnapshot(part_id=part_id, part_name=f"Part {part_id}",
                                 category="electronic", criticality="HIGH",
                                 quantity=qty, unit_of_measure=uom,
                                 reference_designator="", notes=notes)

    def test_empty_both(self):
        added, removed, modified = BOMVersionManager._compute_diff({}, {})
        assert added == removed == modified == []

    def test_all_added(self):
        b = {"P-1": self._snap("P-1"), "P-2": self._snap("P-2")}
        added, removed, modified = BOMVersionManager._compute_diff({}, b)
        assert len(added) == 2
        assert not removed and not modified

    def test_all_removed(self):
        a = {"P-1": self._snap("P-1"), "P-2": self._snap("P-2")}
        added, removed, modified = BOMVersionManager._compute_diff(a, {})
        assert len(removed) == 2
        assert not added and not modified

    def test_no_change(self):
        snap = self._snap("P-1")
        a = {"P-1": snap}
        b = {"P-1": self._snap("P-1")}
        added, removed, modified = BOMVersionManager._compute_diff(a, b)
        assert not added and not removed and not modified

    def test_modification(self):
        a = {"P-1": self._snap("P-1", qty=1.0)}
        b = {"P-1": self._snap("P-1", qty=3.0)}
        added, removed, modified = BOMVersionManager._compute_diff(a, b)
        assert not added and not removed
        assert len(modified) == 1
        assert modified[0].changes["quantity"] == {"from": 1.0, "to": 3.0}

    def test_results_sorted_by_part_id(self):
        a = {k: self._snap(k) for k in ["P-3", "P-1", "P-2"]}
        added, removed, modified = BOMVersionManager._compute_diff(a, {})
        assert [c.part_id for c in removed] == ["P-1", "P-2", "P-3"]