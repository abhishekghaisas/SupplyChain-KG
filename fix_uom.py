"""
One-time script to normalise unit_of_measure values in the graph.
Run with: python fix_uom.py

Normalises: 'each' → 'EA', 'piece' → 'EA', 'pieces' → 'EA', etc.
"""
import sys
sys.path.insert(0, '.')

from src.graph.neo4j_client import Neo4jClient

UOM_MAP = {
    "each": "EA", "piece": "EA", "pieces": "EA",
    "pc": "EA", "pcs": "EA", "unit": "EA", "units": "EA",
    "item": "EA", "items": "EA",
}

with Neo4jClient() as db:
    # Check what values exist
    rows = db.execute_query(
        "MATCH (p:Part) RETURN DISTINCT p.unit_of_measure AS uom, count(*) AS n ORDER BY n DESC"
    )
    print("Current UOM distribution:")
    for r in rows:
        print(f"  {r['uom']!r:20} → {r['n']} parts")

    # Fix non-standard values
    fixed = 0
    for raw, canonical in UOM_MAP.items():
        result = db.execute_query(
            "MATCH (p:Part) WHERE toLower(p.unit_of_measure) = $raw "
            "SET p.unit_of_measure = $canonical RETURN count(p) AS n",
            {"raw": raw, "canonical": canonical}
        )
        n = result[0]["n"] if result else 0
        if n:
            print(f"  Fixed {n} parts: {raw!r} → {canonical!r}")
            fixed += n

    print(f"\nTotal fixed: {fixed} parts")

    # Verify
    rows = db.execute_query(
        "MATCH (p:Part) RETURN DISTINCT p.unit_of_measure AS uom, count(*) AS n ORDER BY n DESC"
    )
    print("\nUOM distribution after fix:")
    for r in rows:
        print(f"  {r['uom']!r:20} → {r['n']} parts")