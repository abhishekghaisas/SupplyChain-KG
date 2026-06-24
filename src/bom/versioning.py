"""
BOM versioning — clone a BOM into a new version and diff two versions.

Graph pattern (unchanged from neo4j_client):
    (BOM)-[:CONTAINS]->(Component)-[:REFERENCES]->(Part)

Public API
----------
BOMVersionManager.clone(source_bom_id, new_bom_id, new_version, ...)
    Deep-copies a BOM: creates a new BOM node and new Component nodes
    pointing at the same Part nodes.  Returns the new BOM id.

BOMVersionManager.diff(bom_id_a, bom_id_b)
    Returns a BOMDiff describing every change between the two versions:
      - added:    parts present in B but not A
      - removed:  parts present in A but not B
      - modified: parts in both where quantity, unit_of_measure, or notes changed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.graph.neo4j_client import Neo4jClient


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class ComponentSnapshot:
    """Flattened view of one BOM line item used for comparison."""

    part_id: str
    part_name: str
    category: str
    criticality: str
    quantity: float
    unit_of_measure: str
    reference_designator: str
    notes: str

    # Fields compared during diff (extend this list to widen the diff scope)
    DIFFABLE_FIELDS: tuple = field(
        default=("quantity", "unit_of_measure", "notes"), init=False, repr=False, compare=False
    )

    def diff_against(self, other: "ComponentSnapshot") -> Dict[str, Dict[str, Any]]:
        """
        Return a dict of changed fields: {field: {"from": old, "to": new}}.
        Returns an empty dict when both snapshots are identical.
        """
        changes: Dict[str, Dict[str, Any]] = {}
        for f in self.DIFFABLE_FIELDS:
            old, new = getattr(self, f), getattr(other, f)
            if old != new:
                changes[f] = {"from": old, "to": new}
        return changes


@dataclass
class ComponentChange:
    """A single modified component with its before/after values."""

    part_id: str
    part_name: str
    changes: Dict[str, Dict[str, Any]]  # {field: {"from": ..., "to": ...}}


@dataclass
class BOMDiff:
    """Complete diff between two BOM versions."""

    bom_id_a: str
    bom_id_b: str
    version_a: str
    version_b: str
    added: List[ComponentSnapshot]  # in B, not in A
    removed: List[ComponentSnapshot]  # in A, not in B
    modified: List[ComponentChange]  # in both, but changed
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    @property
    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        if self.modified:
            parts.append(f"{len(self.modified)} modified")
        return ", ".join(parts) if parts else "no changes"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bom_id_a": self.bom_id_a,
            "bom_id_b": self.bom_id_b,
            "version_a": self.version_a,
            "version_b": self.version_b,
            "summary": self.summary,
            "added": [vars(c) for c in self.added],
            "removed": [vars(c) for c in self.removed],
            "modified": [
                {
                    "part_id": c.part_id,
                    "part_name": c.part_name,
                    "changes": c.changes,
                }
                for c in self.modified
            ],
            "generated_at": self.generated_at.isoformat(),
        }

    def format_report(self) -> str:
        """Human-readable diff report."""
        lines = [
            "",
            "=" * 70,
            f"BOM DIFF  {self.bom_id_a} (v{self.version_a})"
            f"  →  {self.bom_id_b} (v{self.version_b})",
            "=" * 70,
            f"Summary: {self.summary}",
        ]

        if self.added:
            lines += ["", f"ADDED ({len(self.added)})"]
            for c in self.added:
                lines.append(
                    f"  + {c.part_id}  {c.part_name}"
                    f"  qty={c.quantity} {c.unit_of_measure}"
                    f"  [{c.criticality}]"
                )

        if self.removed:
            lines += ["", f"REMOVED ({len(self.removed)})"]
            for c in self.removed:
                lines.append(
                    f"  - {c.part_id}  {c.part_name}"
                    f"  qty={c.quantity} {c.unit_of_measure}"
                    f"  [{c.criticality}]"
                )

        if self.modified:
            lines += ["", f"MODIFIED ({len(self.modified)})"]
            for c in self.modified:
                lines.append(f"  ~ {c.part_id}  {c.part_name}")
                for fname, delta in c.changes.items():
                    lines.append(f"      {fname}: {delta['from']!r} → {delta['to']!r}")

        lines.append("")
        return "\n".join(lines)


# ── Manager ───────────────────────────────────────────────────────────────────


class BOMVersionManager:
    """
    Clone and diff BOM versions against the Neo4j knowledge graph.

    All graph writes are atomic: the clone either fully succeeds or
    raises, leaving the graph unchanged.
    """

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    # ── Clone ─────────────────────────────────────────────────────────────

    def clone(
        self,
        source_bom_id: str,
        new_bom_id: str,
        new_version: str,
        *,
        new_name: Optional[str] = None,
        new_description: Optional[str] = None,
        new_status: str = "DRAFT",
        cloned_by: Optional[str] = None,
    ) -> str:
        """
        Deep-clone a BOM into a new version.

        Creates:
          - A new BOM node (new_bom_id / new_version)
          - A new Component node for every component in the source BOM
          - CONTAINS / REFERENCES relationships mirroring the source
          - A CLONED_FROM relationship: (new BOM)-[:CLONED_FROM]->(source BOM)

        Part nodes are *shared* — not copied — so supplier/spec data stays
        consistent across versions.

        Args:
            source_bom_id: ID of the BOM to copy.
            new_bom_id:    ID for the new BOM (must not already exist).
            new_version:   Version string for the new BOM (e.g. "2.0").
            new_name:      Override the name; defaults to source name.
            new_description: Override description; defaults to source description.
            new_status:    Status for the new BOM (default "DRAFT").
            cloned_by:     Optional actor identifier recorded on the relationship.

        Returns:
            new_bom_id on success.

        Raises:
            ValueError: source BOM not found, or new_bom_id already exists.
        """
        # --- pre-flight checks -------------------------------------------
        source = self._client.get_bom(source_bom_id)
        if source is None:
            raise ValueError(f"Source BOM not found: {source_bom_id!r}")

        if self._client.get_bom(new_bom_id) is not None:
            raise ValueError(f"BOM already exists: {new_bom_id!r}")

        # --- resolve metadata -------------------------------------------
        resolved_name = new_name or source["name"]
        resolved_desc = new_description or source.get("description", "")

        logger.info(
            f"Cloning BOM {source_bom_id!r} (v{source['version']}) "
            f"→ {new_bom_id!r} (v{new_version})"
        )

        # --- single atomic write ----------------------------------------
        # We do everything in one Cypher statement so partial writes are
        # impossible.  UNWIND iterates over the source components and
        # creates a fresh Component node for each one.
        query = """
        // 1. Find source BOM and all its components + referenced parts
        MATCH (src:BOM {id: $source_bom_id})
        MATCH (src)-[:CONTAINS]->(sc:Component)-[:REFERENCES]->(p:Part)

        // 2. Collect component data before creating anything
        WITH src,
             collect({
                 part_id:              p.id,
                 quantity:             sc.quantity,
                 reference_designator: sc.reference_designator,
                 unit_of_measure:      sc.unit_of_measure,
                 notes:                sc.notes
             }) AS components

        // 3. Create the new BOM node
        CREATE (nb:BOM {
            id:          $new_bom_id,
            name:        $new_name,
            description: $new_desc,
            version:     $new_version,
            status:      $new_status,
            created_at:  datetime()
        })

        // 4. Record provenance
        CREATE (nb)-[:CLONED_FROM {
            cloned_at: datetime(),
            cloned_by: $cloned_by
        }]->(src)

        // 5. Recreate each component
        WITH nb, components
        UNWIND components AS comp
        MATCH (p:Part {id: comp.part_id})
        CREATE (nc:Component {
            id:                   $new_bom_id + '-' + comp.part_id,
            quantity:             comp.quantity,
            reference_designator: comp.reference_designator,
            unit_of_measure:      comp.unit_of_measure,
            notes:                comp.notes,
            created_at:           datetime()
        })
        CREATE (nb)-[:CONTAINS]->(nc)
        CREATE (nc)-[:REFERENCES]->(p)

        RETURN count(nc) AS components_cloned
        """

        rows = self._client.execute_query(
            query,
            {
                "source_bom_id": source_bom_id,
                "new_bom_id": new_bom_id,
                "new_name": resolved_name,
                "new_desc": resolved_desc,
                "new_version": new_version,
                "new_status": new_status,
                "cloned_by": cloned_by or "system",
            },
        )

        n = rows[0]["components_cloned"] if rows else 0
        logger.info(f"Cloned {n} components into {new_bom_id!r}")
        return new_bom_id

    # ── Diff ──────────────────────────────────────────────────────────────

    def diff(self, bom_id_a: str, bom_id_b: str) -> BOMDiff:
        """
        Diff two BOM versions.

        Compares every component by part_id.  For parts present in both
        versions, compares quantity, unit_of_measure, and notes.

        Args:
            bom_id_a: "before" BOM (typically the older version).
            bom_id_b: "after"  BOM (typically the newer version).

        Returns:
            BOMDiff with added, removed, and modified buckets.

        Raises:
            ValueError: either BOM not found.
        """
        bom_a = self._client.get_bom(bom_id_a)
        bom_b = self._client.get_bom(bom_id_b)

        if bom_a is None:
            raise ValueError(f"BOM not found: {bom_id_a!r}")
        if bom_b is None:
            raise ValueError(f"BOM not found: {bom_id_b!r}")

        logger.info(
            f"Diffing {bom_id_a!r} (v{bom_a['version']}) " f"vs {bom_id_b!r} (v{bom_b['version']})"
        )

        snap_a = self._fetch_snapshots(bom_id_a)
        snap_b = self._fetch_snapshots(bom_id_b)

        added, removed, modified = self._compute_diff(snap_a, snap_b)

        return BOMDiff(
            bom_id_a=bom_id_a,
            bom_id_b=bom_id_b,
            version_a=bom_a["version"],
            version_b=bom_b["version"],
            added=added,
            removed=removed,
            modified=modified,
        )

    # ── Version lineage ───────────────────────────────────────────────────

    def get_version_lineage(self, bom_id: str) -> List[Dict[str, Any]]:
        """
        Walk the CLONED_FROM chain and return the full ancestry of a BOM,
        oldest first.

        Returns a list of dicts with keys: id, version, status, cloned_at,
        cloned_by.  The root (original) BOM has cloned_at=None.
        """
        query = """
        MATCH path = (b:BOM {id: $bom_id})-[:CLONED_FROM*0..]->(ancestor:BOM)
        UNWIND relationships(path) AS rel
        WITH ancestor,
             rel.cloned_at AS cloned_at,
             rel.cloned_by AS cloned_by
        RETURN ancestor.id      AS id,
               ancestor.version AS version,
               ancestor.status  AS status,
               toString(cloned_at) AS cloned_at,
               cloned_by
        ORDER BY cloned_at
        """
        rows = self._client.execute_query(query, {"bom_id": bom_id})

        # Also include the BOM itself as the head of the chain
        head = self._client.get_bom(bom_id)
        if head:
            result = [
                {
                    "id": head["id"],
                    "version": head["version"],
                    "status": head["status"],
                    "cloned_at": None,
                    "cloned_by": None,
                }
            ]
            # ancestors come after; deduplicate by id
            seen = {head["id"]}
            for row in reversed(rows):  # oldest first
                if row["id"] not in seen:
                    result.insert(0, row)
                    seen.add(row["id"])
            return result

        return rows

    # ── Private helpers ───────────────────────────────────────────────────

    def _fetch_snapshots(self, bom_id: str) -> Dict[str, ComponentSnapshot]:
        """Return {part_id: ComponentSnapshot} for every component in the BOM."""
        rows = self._client.get_bom_components(bom_id)
        snapshots: Dict[str, ComponentSnapshot] = {}
        for row in rows:
            pid = row["part_id"]
            if pid in snapshots:
                # Defensive: multiple components referencing same part in one BOM
                logger.warning(
                    f"BOM {bom_id!r} has duplicate part {pid!r}; "
                    "only the first occurrence is used in diff"
                )
                continue
            snapshots[pid] = ComponentSnapshot(
                part_id=pid,
                part_name=row.get("part_name", ""),
                category=row.get("category", ""),
                criticality=row.get("criticality", ""),
                quantity=float(row.get("quantity") or 0),
                unit_of_measure=row.get("unit_of_measure", "EA"),
                reference_designator=row.get("reference_designator", ""),
                notes=row.get("notes", ""),
            )
        return snapshots

    @staticmethod
    def _compute_diff(
        snap_a: Dict[str, ComponentSnapshot],
        snap_b: Dict[str, ComponentSnapshot],
    ) -> tuple[List[ComponentSnapshot], List[ComponentSnapshot], List[ComponentChange]]:
        """
        Core diff logic — pure function, no I/O.

        Returns (added, removed, modified).
        """
        keys_a = set(snap_a)
        keys_b = set(snap_b)

        added = [snap_b[k] for k in sorted(keys_b - keys_a)]
        removed = [snap_a[k] for k in sorted(keys_a - keys_b)]

        modified: List[ComponentChange] = []
        for k in sorted(keys_a & keys_b):
            changes = snap_a[k].diff_against(snap_b[k])
            if changes:
                modified.append(
                    ComponentChange(
                        part_id=k,
                        part_name=snap_b[k].part_name,
                        changes=changes,
                    )
                )

        return added, removed, modified
