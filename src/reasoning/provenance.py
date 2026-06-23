"""
Provenance tracking for decision traceability.

Tracks the complete reasoning chain: sources → facts → rules → decisions
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum

from loguru import logger


class ProvenanceType(Enum):
    """Types of provenance entries."""
    DATA_SOURCE = "data_source"
    EXTRACTION = "extraction"
    VALIDATION = "validation"
    REASONING = "reasoning"
    DECISION = "decision"


@dataclass
class ProvenanceEntry:
    """Single entry in provenance chain."""
    entry_type: ProvenanceType
    description: str
    timestamp: datetime = field(default_factory=datetime.now)
    actor: str = "system"  # Who/what performed this action
    data: Dict[str, Any] = field(default_factory=dict)
    confidence: Optional[float] = None
    parent_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.entry_type.value,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "data": self.data,
            "confidence": self.confidence,
            "parent_id": self.parent_id
        }


class ProvenanceTracker:
    """
    Track complete provenance of decisions and data.
    
    This enables full explainability: "Why did the system make this decision?"
    Answer: "Here's the complete chain from source data to final decision."
    """
    
    def __init__(self, root_subject: str):
        """
        Initialize provenance tracker.
        
        Args:
            root_subject: What we're tracking (e.g., "Part P-12345 substitution")
        """
        self.root_subject = root_subject
        self.entries: List[ProvenanceEntry] = []
        self.entry_id_counter = 0
        logger.debug(f"Initialized provenance tracker for: {root_subject}")
    
    def add_source(
        self,
        source_name: str,
        source_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Add a data source to provenance.
        
        Args:
            source_name: Name/identifier of source
            source_type: Type (document, database, api, etc.)
            metadata: Additional metadata
            
        Returns:
            Entry ID
        """
        entry = ProvenanceEntry(
            entry_type=ProvenanceType.DATA_SOURCE,
            description=f"Data from {source_name}",
            actor=source_type,
            data=metadata or {}
        )
        self.entries.append(entry)
        
        entry_id = f"source_{self.entry_id_counter}"
        self.entry_id_counter += 1
        return entry_id
    
    def add_extraction(
        self,
        extracted_by: str,
        entities_extracted: List[str],
        confidence: float,
        source_id: Optional[str] = None
    ) -> str:
        """
        Add an extraction step to provenance.
        
        Args:
            extracted_by: System that performed extraction (e.g., "claude-sonnet-4")
            entities_extracted: List of entity types extracted
            confidence: Extraction confidence
            source_id: Parent source entry ID
            
        Returns:
            Entry ID
        """
        entry = ProvenanceEntry(
            entry_type=ProvenanceType.EXTRACTION,
            description=f"Extracted {len(entities_extracted)} entities",
            actor=extracted_by,
            confidence=confidence,
            data={"entities": entities_extracted},
            parent_id=source_id
        )
        self.entries.append(entry)
        
        entry_id = f"extraction_{self.entry_id_counter}"
        self.entry_id_counter += 1
        return entry_id
    
    def add_validation(
        self,
        rule_name: str,
        passed: bool,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None
    ) -> str:
        """
        Add a validation step to provenance.
        
        Args:
            rule_name: Name of rule that was applied
            passed: Whether validation passed
            reason: Reason for result
            details: Additional details
            parent_id: Parent entry ID
            
        Returns:
            Entry ID
        """
        entry = ProvenanceEntry(
            entry_type=ProvenanceType.VALIDATION,
            description=f"Rule '{rule_name}': {'PASS' if passed else 'FAIL'} - {reason}",
            actor=rule_name,
            data={
                "passed": passed,
                "reason": reason,
                **(details or {})
            },
            parent_id=parent_id
        )
        self.entries.append(entry)
        
        entry_id = f"validation_{self.entry_id_counter}"
        self.entry_id_counter += 1
        return entry_id
    
    def add_reasoning(
        self,
        reasoning_type: str,
        conclusion: str,
        facts_used: List[str],
        confidence: float,
        parent_ids: Optional[List[str]] = None
    ) -> str:
        """
        Add a reasoning step to provenance.
        
        Args:
            reasoning_type: Type of reasoning applied
            conclusion: What was concluded
            facts_used: Facts that were used in reasoning
            confidence: Confidence in conclusion
            parent_ids: Parent entry IDs
            
        Returns:
            Entry ID
        """
        entry = ProvenanceEntry(
            entry_type=ProvenanceType.REASONING,
            description=f"{reasoning_type}: {conclusion}",
            actor="reasoning_engine",
            confidence=confidence,
            data={
                "conclusion": conclusion,
                "facts_used": facts_used,
                "parent_ids": parent_ids or []
            }
        )
        self.entries.append(entry)
        
        entry_id = f"reasoning_{self.entry_id_counter}"
        self.entry_id_counter += 1
        return entry_id
    
    def add_decision(
        self,
        decision: str,
        rationale: str,
        confidence: float,
        parent_ids: Optional[List[str]] = None
    ) -> str:
        """
        Add final decision to provenance.
        
        Args:
            decision: The decision made
            rationale: Why this decision was made
            confidence: Confidence in decision
            parent_ids: All entry IDs that contributed to this decision
            
        Returns:
            Entry ID
        """
        entry = ProvenanceEntry(
            entry_type=ProvenanceType.DECISION,
            description=f"Decision: {decision}",
            actor="system",
            confidence=confidence,
            data={
                "decision": decision,
                "rationale": rationale,
                "contributing_entries": parent_ids or []
            }
        )
        self.entries.append(entry)
        
        entry_id = f"decision_{self.entry_id_counter}"
        self.entry_id_counter += 1
        return entry_id
    
    def get_chain(self) -> List[Dict[str, Any]]:
        """Get complete provenance chain as list of dicts."""
        return [entry.to_dict() for entry in self.entries]
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of provenance."""
        return {
            "subject": self.root_subject,
            "total_entries": len(self.entries),
            "entry_types": {
                ptype.value: len([e for e in self.entries if e.entry_type == ptype])
                for ptype in ProvenanceType
            },
            "timeline": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.entry_type.value,
                    "description": e.description
                }
                for e in self.entries
            ]
        }
    
    def format_for_display(self) -> str:
        """Format provenance chain for human-readable display."""
        lines = [f"\nProvenance Chain: {self.root_subject}"]
        lines.append("=" * 70)
        
        for i, entry in enumerate(self.entries, 1):
            icon = {
                ProvenanceType.DATA_SOURCE: "📄",
                ProvenanceType.EXTRACTION: "🤖",
                ProvenanceType.VALIDATION: "✓",
                ProvenanceType.REASONING: "🧠",
                ProvenanceType.DECISION: "⚖️"
            }.get(entry.entry_type, "•")
            
            lines.append(f"\n{i}. {icon} {entry.entry_type.value.upper()}")
            lines.append(f"   {entry.description}")
            lines.append(f"   Actor: {entry.actor}")
            
            if entry.confidence is not None:
                lines.append(f"   Confidence: {entry.confidence:.2%}")
            
            lines.append(f"   Time: {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(lines)


# Example usage
if __name__ == "__main__":
    tracker = ProvenanceTracker("Part P-12345 substitution analysis")
    
    # Track a complete decision chain
    source_id = tracker.add_source(
        "supplier_catalog_2024.pdf",
        "document",
        {"page": 5, "section": "Motors"}
    )
    
    extract_id = tracker.add_extraction(
        "claude-sonnet-4",
        ["Part", "Specifications"],
        confidence=0.95,
        source_id=source_id
    )
    
    val_id = tracker.add_validation(
        "SpecificationMatchRule",
        passed=True,
        reason="All required specs match",
        parent_id=extract_id
    )
    
    reasoning_id = tracker.add_reasoning(
        "Compatibility Analysis",
        "Parts are compatible for substitution",
        facts_used=["spec_match", "certification_valid"],
        confidence=0.92,
        parent_ids=[val_id]
    )
    
    tracker.add_decision(
        "APPROVE substitution of P-12345 with P-67890",
        "All compatibility checks passed with 92% confidence",
        confidence=0.92,
        parent_ids=[reasoning_id]
    )
    
    print(tracker.format_for_display())
