"""
Supply Chain Knowledge Graph
A neuro-symbolic reasoning system for supply chain intelligence.
"""

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

from .graph.neo4j_client import Neo4jClient
from .ingestion.entity_extractor import ClaudeEntityExtractor, extract_entities_from_text

__all__ = [
    "Neo4jClient",
    "ClaudeEntityExtractor",
    "extract_entities_from_text",
]
