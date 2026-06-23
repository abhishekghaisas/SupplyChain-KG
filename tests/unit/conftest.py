"""
Shared pytest fixtures available to all test modules.
"""

import sys
from pathlib import Path

import pytest

# Ensure project root is on the path (belt-and-suspenders alongside pyproject.toml)
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session")
def db_client():
    """
    Return a connected Neo4jClient for the test session.
    Skips automatically if Neo4j is unreachable so no-DB runs stay clean.
    """
    from src.graph.neo4j_client import Neo4jClient
    from neo4j.exceptions import ServiceUnavailable

    c = Neo4jClient()
    try:
        c.connect()
    except (ServiceUnavailable, Exception):
        pytest.skip("Neo4j not available — skipping DB test")
    yield c
    c.close()