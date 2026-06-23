"""
Similarity search router.

Endpoints
─────────
  GET /search?q=servo+motor&type=parts&limit=10&min_score=0.3
      Full-text semantic search over parts, suppliers, and BOMs using
      sentence-transformer embeddings stored in Redis.

  POST /search/reindex
      Rebuild all embedding vectors from the graph. Call after bulk imports
      or if search results seem stale. Takes ~1-2s for typical dataset sizes.

  GET /search/stats
      Cache hit rate and vector store counts.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import get_db, verify_token
from src.api.cache import get_stats as cache_stats
from src.graph.neo4j_client import Neo4jClient

router = APIRouter(prefix="/search", tags=["Search"])


# ── Response schemas (inline — simple enough not to need schemas.py) ──────────

from pydantic import BaseModel


class SearchResultItem(BaseModel):
    entity_id:   str
    entity_type: str
    name:        str
    score:       float


class SearchResponse(BaseModel):
    query:        str
    entity_type:  Optional[str]
    results:      List[SearchResultItem]
    total:        int
    from_cache:   bool = False


class ReindexResponse(BaseModel):
    parts:     int
    suppliers: int
    boms:      int
    message:   str


class StatsResponse(BaseModel):
    cache:        dict
    vector_counts: dict


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SearchResponse,
            dependencies=[Depends(verify_token)])
def semantic_search(
    q:         str           = Query(..., min_length=2, description="Search query"),
    type:      Optional[str] = Query(None, description="part | supplier | bom (omit for all)"),
    limit:     int           = Query(default=10, ge=1, le=50),
    min_score: float         = Query(default=0.3, ge=0.0, le=1.0),
):
    """
    Semantic similarity search over parts, suppliers, and BOMs.

    Uses sentence-transformer embeddings — no need for exact keyword matches.
    "servo motor 400W" will find "Servo Motor SM-400" even without exact wording.

    Scores range from 0.0 (unrelated) to 1.0 (identical meaning).
    The default threshold of 0.3 filters noise while keeping relevant results.
    """
    from src.search.embedder import embed
    from src.search.vector_store import search

    from src.ai.grounded import rerank_search_results

    entity_type = type if type in ("part", "supplier", "bom") else None
    query_vec   = embed(q)
    raw_results = search(query_vec, entity_type=entity_type,
                         limit=limit * 2,   # fetch extra for reranking headroom
                         min_score=min_score)

    # Rerank by composite score (semantic + criticality + entity type)
    result_dicts = [
        {"entity_id": r.entity_id, "entity_type": r.entity_type,
         "name": r.name, "score": r.score, "data": {}}
        for r in raw_results
    ]
    reranked = rerank_search_results(result_dicts, q, boost_entity_type=entity_type)[:limit]

    return SearchResponse(
        query=q,
        entity_type=entity_type,
        results=[
            SearchResultItem(
                entity_id=r["entity_id"],
                entity_type=r["entity_type"],
                name=r["name"],
                score=r["score"],
            )
            for r in reranked
        ],
        total=len(reranked),
    )


@router.post("/reindex", response_model=ReindexResponse,
             dependencies=[Depends(verify_token)])
def reindex(db: Neo4jClient = Depends(get_db)):
    """
    Rebuild all embedding vectors from the graph.

    Run this after bulk imports or when search results seem stale.
    Safe to call repeatedly — uses upsert, not replace.
    """
    from src.search.vector_store import reindex_all

    counts = reindex_all(db)
    total  = sum(counts.values())
    return ReindexResponse(
        parts=counts["parts"],
        suppliers=counts["suppliers"],
        boms=counts["boms"],
        message=f"Indexed {total} entities ({counts['parts']} parts, "
                f"{counts['suppliers']} suppliers, {counts['boms']} BOMs)",
    )


@router.get("/stats", response_model=StatsResponse,
            dependencies=[Depends(verify_token)])
def search_stats(db: Neo4jClient = Depends(get_db)):
    """
    Cache hit rate and vector store entity counts.

    Use this to demonstrate cost savings:
      - cache.hit_rate shows % of requests served from Redis (no Neo4j cost)
      - vector_counts shows how many entities are searchable
    """
    from src.search.vector_store import _get_redis

    # Count vectors by type
    vector_counts = {"parts": 0, "suppliers": 0, "boms": 0}
    try:
        r = _get_redis()
        for entity_type in vector_counts:
            cursor, keys = r.scan(0, match=f"vec:{entity_type}:*", count=1000)
            count = len(keys)
            # Paginate if needed
            while cursor != 0:
                cursor, more_keys = r.scan(cursor, match=f"vec:{entity_type}:*", count=1000)
                count += len(more_keys)
            vector_counts[entity_type] = count
    except Exception:
        pass

    return StatsResponse(
        cache=cache_stats(),
        vector_counts=vector_counts,
    )