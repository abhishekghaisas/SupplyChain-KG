"""
Vector store — Redis-backed embedding storage with cosine similarity search.

Storage layout (Redis db+3, separate from cache/rate-limit/tokens):
  vec:part:<part_id>      → JSON {id, name, type, vector: [float, ...]}
  vec:supplier:<sup_id>   → JSON {id, name, type, vector: [float, ...]}
  vec:bom:<bom_id>        → JSON {id, name, type, vector: [float, ...]}

Search is brute-force cosine similarity over all keys of the requested
type. For the typical dataset size (hundreds to low thousands of parts)
this is fast enough — full scan of 1000 parts takes < 5 ms in Redis.

For larger datasets (10k+), replace with Redis Stack's vector search
(RediSearch VSS) by switching to HNSW indexing — the interface here is
already shaped to make that migration straightforward.

Public API
──────────
  upsert(entity_id, entity_type, name, vector)
  delete(entity_id, entity_type)
  search(query_vector, entity_type, limit, min_score) → List[SearchResult]
  reindex_all(db_client)   — rebuild all vectors from the graph
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Literal, Optional

from loguru import logger

EntityType = Literal["part", "supplier", "bom"]


@dataclass
class SearchResult:
    entity_id: str
    entity_type: str
    name: str
    score: float  # cosine similarity, 0.0–1.0


def _get_redis():
    import redis as redis_lib
    from src.config import get_settings

    s = get_settings()
    host = s.redis_host or "localhost"
    port = s.redis_port or 6379
    db = (s.redis_db or 0) + 3  # db+3 — separate from other Redis uses
    return redis_lib.Redis(
        host=host, port=port, db=db, decode_responses=True, socket_connect_timeout=1
    )


def _key(entity_id: str, entity_type: EntityType) -> str:
    return f"vec:{entity_type}:{entity_id}"


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two pre-normalised vectors."""
    # Vectors from sentence-transformers are already unit-normalised,
    # so cosine similarity = dot product.
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot))  # clamp floating-point drift


# ── Write operations ──────────────────────────────────────────────────────────


def upsert(
    entity_id: str,
    entity_type: EntityType,
    name: str,
    vector: List[float],
) -> None:
    """Store or update an embedding in Redis."""
    try:
        r = _get_redis()
        payload = json.dumps(
            {
                "id": entity_id,
                "name": name,
                "type": entity_type,
                "vector": vector,
            }
        )
        r.set(_key(entity_id, entity_type), payload)
        logger.debug(f"Upserted vector for {entity_type}:{entity_id}")
    except Exception as exc:
        logger.warning(f"Vector upsert failed for {entity_type}:{entity_id}: {exc}")


def delete(entity_id: str, entity_type: EntityType) -> None:
    """Remove an embedding from Redis."""
    try:
        r = _get_redis()
        r.delete(_key(entity_id, entity_type))
    except Exception as exc:
        logger.warning(f"Vector delete failed: {exc}")


# ── Search ────────────────────────────────────────────────────────────────────


def search(
    query_vector: List[float],
    entity_type: Optional[EntityType] = None,
    limit: int = 10,
    min_score: float = 0.3,
) -> List[SearchResult]:
    """
    Return the top-N most similar entities to the query vector.

    Args:
        query_vector: Embedding of the search query (384-dim unit vector).
        entity_type:  Restrict to 'part', 'supplier', or 'bom'. None = all.
        limit:        Maximum results to return.
        min_score:    Minimum cosine similarity threshold (0.0–1.0).

    Returns:
        List of SearchResult sorted by score descending.
    """
    try:
        r = _get_redis()
        pattern = f"vec:{entity_type}:*" if entity_type else "vec:*"

        results: List[SearchResult] = []
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=200)
            if keys:
                raw_values = r.mget(keys)
                for raw in raw_values:
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                        score = _cosine(query_vector, entry["vector"])
                        if score >= min_score:
                            results.append(
                                SearchResult(
                                    entity_id=entry["id"],
                                    entity_type=entry["type"],
                                    name=entry["name"],
                                    score=round(score, 4),
                                )
                            )
                    except Exception:
                        continue
            if cursor == 0:
                break

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    except Exception as exc:
        logger.error(f"Vector search failed: {exc}")
        return []


# ── Bulk reindex ──────────────────────────────────────────────────────────────


def reindex_all(db_client) -> dict:
    """
    Rebuild all vectors from the graph.

    Queries Neo4j for all parts, suppliers, and BOMs, generates embeddings,
    and stores them in Redis. Safe to call repeatedly — uses upsert.

    Returns counts of indexed entities.
    """
    from src.search.embedder import embed_batch, part_text, supplier_text, bom_text

    counts = {"parts": 0, "suppliers": 0, "boms": 0}

    # ── Parts ─────────────────────────────────────────────────────────────────
    part_rows = db_client.execute_query(
        """
        MATCH (p:Part)
        RETURN p.id AS id, p.name AS name, p.description AS description,
               p.category AS category, p.criticality AS criticality,
               p.specifications_json AS specifications_json
        """
    )
    if part_rows:
        texts = [part_text(p) for p in part_rows]
        vectors = embed_batch(texts)
        for part, vec in zip(part_rows, vectors):
            upsert(part["id"], "part", part.get("name", ""), vec)
            counts["parts"] += 1
        logger.info(f"Indexed {counts['parts']} parts")

    # ── Suppliers ─────────────────────────────────────────────────────────────
    sup_rows = db_client.execute_query(
        """
        MATCH (s:Supplier)
        RETURN s.id AS id, s.name AS name, s.location AS location,
               s.certifications AS certifications
        """
    )
    if sup_rows:
        texts = [supplier_text(s) for s in sup_rows]
        vectors = embed_batch(texts)
        for sup, vec in zip(sup_rows, vectors):
            upsert(sup["id"], "supplier", sup.get("name", ""), vec)
            counts["suppliers"] += 1
        logger.info(f"Indexed {counts['suppliers']} suppliers")

    # ── BOMs ──────────────────────────────────────────────────────────────────
    bom_rows = db_client.execute_query(
        """
        MATCH (b:BOM)
        RETURN b.id AS id, b.name AS name, b.description AS description,
               b.version AS version, b.status AS status
        """
    )
    if bom_rows:
        texts = [bom_text(b) for b in bom_rows]
        vectors = embed_batch(texts)
        for bom, vec in zip(bom_rows, vectors):
            upsert(bom["id"], "bom", bom.get("name", ""), vec)
            counts["boms"] += 1
        logger.info(f"Indexed {counts['boms']} BOMs")

    return counts
