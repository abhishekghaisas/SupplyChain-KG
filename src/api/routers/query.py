"""
Natural language graph query router.

  POST /query
    Body: {"question": "Which parts have only one active supplier?"}
    Returns: Cypher, raw results, plain-English answer, token usage.

The endpoint is intentionally read-only — the NLQueryEngine validates
that no write operations are present in the generated Cypher.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.dependencies import get_db, verify_token
from src.graph.neo4j_client import Neo4jClient

router = APIRouter(prefix="/query", tags=["Natural Language Query"])


class NLQueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=5,
        description="Plain English question about the supply chain graph",
        example="Which parts have only one active supplier?",
    )


@router.post("", dependencies=[Depends(verify_token)])
def natural_language_query(
    body: NLQueryRequest,
    db: Neo4jClient = Depends(get_db),
):
    """
    Ask a natural language question about the supply chain graph.

    Claude converts the question to Cypher, executes it against Neo4j,
    and interprets the results in plain English.

    The Cypher is always returned so you can verify exactly what ran.
    Only read queries are permitted — write operations are rejected.

    Example questions:
    - "Which parts have only one active supplier?"
    - "Show me all CRITICAL parts without a verified substitute"
    - "Which suppliers have a quality rating above 4.5?"
    - "What BOMs contain parts from German suppliers?"
    - "Which parts have a lead time over 30 days?"
    """
    from src.ai.grounded import NLQueryEngine

    try:
        engine = NLQueryEngine(db)
        result = engine.query(body.question)
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Query failed: {exc}")
