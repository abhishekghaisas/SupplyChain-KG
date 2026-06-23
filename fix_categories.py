"""
One-time script to normalise category values in the graph.
Run with: python fix_categories.py (from project root)
"""
import sys
sys.path.insert(0, '.')
from src.graph.neo4j_client import Neo4jClient
from src.ingestion.entity_extractor import _normalise_category

with Neo4jClient() as db:
    rows = db.execute_query(
        "MATCH (p:Part) RETURN DISTINCT p.category AS cat, count(*) AS n ORDER BY n DESC"
    )
    print("Current categories:")
    for r in rows:
        normalised = _normalise_category(r['cat'] or '')
        changed = '→ ' + normalised if normalised != (r['cat'] or '').lower() else '  (unchanged)'
        print(f"  {r['n']:4}  {r['cat']!r:40} {changed}")

    print("\nApplying normalisation…")
    all_parts = db.execute_query(
        "MATCH (p:Part) RETURN p.id AS id, p.category AS category"
    )
    updated = 0
    for part in all_parts:
        normalised = _normalise_category(part['category'] or '')
        if normalised != (part['category'] or ''):
            db.execute_write(
                "MATCH (p:Part {id: $id}) SET p.category = $cat",
                {"id": part['id'], "cat": normalised}
            )
            updated += 1

    print(f"Updated {updated} of {len(all_parts)} parts")

    print("\nFinal categories:")
    rows = db.execute_query(
        "MATCH (p:Part) RETURN DISTINCT p.category AS cat, count(*) AS n ORDER BY n DESC"
    )
    for r in rows:
        print(f"  {r['n']:4}  {r['cat']!r}")