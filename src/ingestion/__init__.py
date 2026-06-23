"""Data ingestion and extraction module."""

from .entity_extractor import (
    ClaudeEntityExtractor,
    extract_entities_from_text,
    ExtractedPart,
    ExtractedSupplier,
    ExtractedSupplyRelationship,
    SupplyChainDocument,
    ExtractionResult,
    EntityType,
)

__all__ = [
    "ClaudeEntityExtractor",
    "extract_entities_from_text",
    "ExtractedPart",
    "ExtractedSupplier",
    "ExtractedSupplyRelationship",
    "SupplyChainDocument",
    "ExtractionResult",
    "EntityType",
]
