"""
Entity extraction using Anthropic Claude with structured output.
"""

import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

from anthropic import Anthropic
from langchain_anthropic import ChatAnthropic
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import get_settings


# ── Unit of measure normalisation ─────────────────────────────────────────────

_UOM_MAP = {
    "each": "EA", "piece": "EA", "pieces": "EA", "pc": "EA", "pcs": "EA",
    "unit": "EA", "units": "EA", "ea": "EA", "item": "EA", "items": "EA",
    "1": "EA", "meter": "M", "meters": "M", "metre": "M", "metres": "M",
    "foot": "FT", "feet": "FT", "ft": "FT", "inch": "IN", "inches": "IN",
    "millimeter": "MM", "mm": "MM", "kilogram": "KG", "kilograms": "KG",
    "kg": "KG", "gram": "G", "grams": "G", "pound": "LB", "lbs": "LB",
    "box": "BOX", "boxes": "BOX", "reel": "REEL", "roll": "REEL",
    "lot": "LOT", "pair": "PR", "pairs": "PR",
}


def _normalise_uom(raw: str) -> str:
    if not raw:
        return "EA"
    return _UOM_MAP.get(raw.lower().strip(), raw.upper().strip())


# ── Category normalisation ────────────────────────────────────────────────────
CANONICAL_CATEGORIES = [
    "electronic", "electrical", "electromechanical",
    "mechanical", "hydraulic", "pneumatic",
    "software", "raw_material", "other",
]

_CATEGORY_MAP = {
    "electronic": "electronic", "electronics": "electronic",
    "board": "electronic", "boards": "electronic",
    "module": "electronic", "modules": "electronic",
    "ic": "electronic", "integrated circuit": "electronic",
    "semiconductor": "electronic",
    "motor driver": "electronic", "motor drivers": "electronic",
    "motor driver boards": "electronic",
    "motor driver boards / modules": "electronic",
    "driver": "electronic", "drivers": "electronic",
    "controller": "electronic", "controllers": "electronic",
    "microcontroller": "electronic",
    "sensor": "electronic", "sensors": "electronic",
    "relay": "electronic", "relays": "electronic",
    "switch": "electronic", "switches": "electronic",
    "converter": "electronic", "converters": "electronic",
    "soft starter": "electronic", "stepper driver": "electronic",
    "brushless drive": "electronic", "drive": "electronic",
    "amplifier": "electronic", "transistor": "electronic",
    "diode": "electronic", "capacitor": "electronic",
    "resistor": "electronic", "inductor": "electronic",
    "display": "electronic", "connector": "electronic",
    "electrical": "electrical",
    "cable": "electrical", "cables": "electrical",
    "wire": "electrical", "wires": "electrical",
    "power cable": "electrical", "fuse": "electrical",
    "transformer": "electrical", "circuit breaker": "electrical",
    "electromechanical": "electromechanical",
    "motor": "electromechanical", "motors": "electromechanical",
    "servo": "electromechanical", "servo motor": "electromechanical",
    "actuator": "electromechanical", "solenoid": "electromechanical",
    "encoder": "electromechanical",
    "mechanical": "mechanical",
    "bracket": "mechanical", "brackets": "mechanical",
    "mounting bracket": "mechanical",
    "bearing": "mechanical", "gear": "mechanical",
    "shaft": "mechanical", "coupling": "mechanical",
    "fastener": "mechanical", "housing": "mechanical",
    "pump": "mechanical", "valve": "mechanical",
    "hydraulic": "hydraulic", "hydraulics": "hydraulic",
    "hydraulic pump": "hydraulic", "hydraulic valve": "hydraulic",
    "pneumatic": "pneumatic", "pneumatics": "pneumatic",
    "pneumatic valve": "pneumatic", "pneumatic cylinder": "pneumatic",
    "software": "software", "firmware": "software",
    "raw material": "raw_material", "raw materials": "raw_material",
}


def _normalise_category(raw: str) -> str:
    """Normalise a category string to a canonical value."""
    if not raw:
        return "other"
    key = raw.lower().strip()
    if key in _CATEGORY_MAP:
        return _CATEGORY_MAP[key]
    for word in key.split():
        if word in _CATEGORY_MAP:
            return _CATEGORY_MAP[word]
    return "other"


class EntityType(Enum):
    """Types of entities to extract."""
    PART = "part"
    SUPPLIER = "supplier"
    SPECIFICATION = "specification"
    PRICE = "price"
    LEAD_TIME = "lead_time"
    CERTIFICATION = "certification"


@dataclass
class ExtractionResult:
    """Result of entity extraction."""
    entities: List[Dict[str, Any]]
    confidence: float
    source: str
    extraction_method: str
    raw_response: Optional[str] = None


# Pydantic models for structured extraction
class ExtractedPart(BaseModel):
    """Structured part information."""
    part_id: str = Field(description="Part number or ID (e.g., P-12345)")
    name: str = Field(description="Part name or description")
    category: Optional[str] = Field(
        default=None, description="Category: electronic, mechanical, electrical, etc.")
    specifications: Dict[str, Any] = Field(
        default_factory=dict, description="Technical specifications")
    unit_of_measure: Optional[str] = Field(default="EA", description="Unit of measure")

    class Config:
        json_schema_extra = {
            "example": {
                "part_id": "P-12345",
                "name": "Servo Motor SM-400",
                "category": "electronic",
                "specifications": {
                    "power_rating": "400W",
                    "voltage": "24V DC"
                },
                "unit_of_measure": "EA"
            }
        }


class ExtractedSupplier(BaseModel):
    """Structured supplier information."""
    supplier_id: Optional[str] = Field(default=None, description="Supplier ID if mentioned")
    name: str = Field(description="Supplier company name")
    location: Optional[str] = Field(default=None, description="Country or region")
    certifications: List[str] = Field(
        default_factory=list, description="Quality certifications (ISO9001, etc.)")
    contact_info: Dict[str, str] = Field(default_factory=dict, description="Contact information")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Precision Motors Inc",
                "location": "Germany",
                "certifications": ["ISO9001", "IATF16949"],
                "contact_info": {
                    "email": "sales@precisionmotors.de"
                }
            }
        }


class ExtractedSupplyRelationship(BaseModel):
    """Structured supply relationship."""
    supplier_name: str = Field(description="Supplier name")
    part_id: str = Field(description="Part number")
    lead_time_days: Optional[int] = Field(default=None, description="Lead time in days")
    price: Optional[float] = Field(default=None, description="Unit price")
    currency: Optional[str] = Field(default="USD", description="Currency code")
    min_order_quantity: Optional[int] = Field(default=None, description="Minimum order quantity")

    class Config:
        json_schema_extra = {
            "example": {
                "supplier_name": "Precision Motors Inc",
                "part_id": "P-12345",
                "lead_time_days": 21,
                "price": 285.50,
                "currency": "USD"
            }
        }


class SupplyChainDocument(BaseModel):
    """Complete extraction from a supply chain document."""
    parts: List[ExtractedPart] = Field(default_factory=list)
    suppliers: List[ExtractedSupplier] = Field(default_factory=list)
    relationships: List[ExtractedSupplyRelationship] = Field(default_factory=list)
    document_type: str = Field(
        description="Type of document: catalog, bom, price_list, purchase_order, etc.")
    confidence_notes: Optional[str] = Field(
        default=None, description="Any notes about extraction confidence")


class ClaudeEntityExtractor:
    """Extract structured entities from supply chain documents using Claude."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """
        Initialize Claude entity extractor.

        Args:
            api_key: Anthropic API key (uses config if not provided)
            model: Claude model to use (uses config if not provided)
        """
        settings = get_settings()
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.claude_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens

        # Initialize Anthropic client
        self.client = Anthropic(api_key=self.api_key)

        # Initialize LangChain client for structured output
        self.llm = ChatAnthropic(
            anthropic_api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )

        logger.info(f"Initialized ClaudeEntityExtractor with model: {self.model}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def extract_from_text(
        self,
        text: str,
        document_type: str = "unknown",
        source: str = "unknown"
    ) -> ExtractionResult:
        """
        Extract entities from text using Claude with structured output.

        Args:
            text: Document text to extract from
            document_type: Type of document (catalog, bom, price_list, etc.)
            source: Source document identifier

        Returns:
            ExtractionResult with extracted entities
        """
        logger.info(f"Extracting entities from {document_type} document")

        # Use Pydantic parser for structured output
        parser = PydanticOutputParser(pydantic_object=SupplyChainDocument)

        # Create prompt
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an expert at extracting structured information from supply chain documents.  # noqa: E501
Extract parts, suppliers, and their relationships with high accuracy.

Pay special attention to:
- Part numbers and specifications
- Supplier names and contact info
- Lead times and pricing
- Certifications and quality standards

{format_instructions}"""),
            ("human", """Document Type: {document_type}

Document Text:
{text}

Extract all supply chain entities from this document.""")
        ])

        # Build chain
        chain = prompt | self.llm | parser

        try:
            # Execute extraction
            result: SupplyChainDocument = chain.invoke({
                "text": text,
                "document_type": document_type,
                "format_instructions": parser.get_format_instructions()
            })

            # Calculate confidence (simple heuristic - can be improved)
            confidence = self._calculate_confidence(result, text)

            # Convert to dict format
            entities = {
                "parts": [part.model_dump() for part in result.parts],
                "suppliers": [supplier.model_dump() for supplier in result.suppliers],
                "relationships": [rel.model_dump() for rel in result.relationships]
            }

            logger.success(
                f"Extracted {len(result.parts)} parts, "
                f"{len(result.suppliers)} suppliers, "
                f"{len(result.relationships)} relationships"
            )

            return ExtractionResult(
                entities=[entities],
                confidence=confidence,
                source=source,
                extraction_method=f"claude_{self.model}"
            )

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            raise

    def extract_parts_only(
        self,
        text: str,
        source: str = "unknown"
    ) -> List[ExtractedPart]:
        """
        Extract only parts from text.

        Args:
            text: Document text
            source: Source identifier

        Returns:
            List of extracted parts
        """
        parser = PydanticOutputParser(pydantic_object=ExtractedPart)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """Extract part information from this text.
Focus on part numbers, names, categories, and specifications.

{format_instructions}"""),
            ("human", "{text}")
        ])

        chain = prompt | self.llm | parser

        try:
            # For multiple parts, we'll need to chunk or use a different approach
            # This is simplified - production would handle multiple parts better
            result = chain.invoke({
                "text": text,
                "format_instructions": parser.get_format_instructions()
            })

            return [result] if isinstance(result, ExtractedPart) else result

        except Exception as e:
            logger.error(f"Part extraction failed: {e}")
            return []

    def extract_with_direct_api(
        self,
        text: str,
        document_type: str = "unknown"
    ) -> ExtractionResult:
        """
        Extract entities using direct Anthropic API for more control.

        Handles large documents by chunking at ~6000 characters and merging
        results. Retries once with a conciseness hint on JSON parse failure.

        Args:
            text: Document text
            document_type: Document type

        Returns:
            ExtractionResult
        """
        # Use a higher token limit for extraction — the JSON response for a
        # large catalog can easily exceed the 4096 config default.
        EXTRACTION_MAX_TOKENS = 8192
        CHUNK_SIZE = 6000   # characters; keeps response well within token budget

        system_prompt = """You are an expert at extracting structured information from supply chain documents.  # noqa: E501

Extract the following information and return as valid JSON:
{
  "parts": [
    {
      "part_id": "string",
      "name": "string",
      "category": "string",
      "specifications": {},
      "unit_of_measure": "string"
    }
  ],
  "suppliers": [
    {
      "name": "string",
      "location": "string",
      "certifications": [],
      "contact_info": {}
    }
  ],
  "relationships": [
    {
      "supplier_name": "string",
      "part_id": "string",
      "lead_time_days": 0,
      "price": 0.0,
      "currency": "USD"
    }
  ]
}

Rules:
- Return ONLY valid JSON — no markdown fences, no preamble, no trailing text.
- Keep specification values concise (one line each).
- If a field is absent in the document, omit it rather than guessing."""

        def _call_claude(chunk_text: str, hint: str = "") -> str:
            """Call Claude and return the raw response text."""
            user_content = f"Document Type: {document_type}\n\nDocument Text:\n{chunk_text}"
            if hint:
                user_content += f"\n\n{hint}"
            message = self.client.messages.create(
                model=self.model,
                max_tokens=EXTRACTION_MAX_TOKENS,
                temperature=self.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            return message.content[0].text

        def _clean_and_parse(raw: str) -> dict:
            """Strip markdown fences and parse JSON. Raises json.JSONDecodeError on failure."""
            text = raw.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text)

        def _extract_chunk(chunk_text: str) -> dict:
            """Extract from one chunk with one retry on parse failure."""
            raw = _call_claude(chunk_text)
            try:
                return _clean_and_parse(raw)
            except json.JSONDecodeError as first_err:
                logger.warning(
                    f"JSON parse failed on first attempt ({first_err}), retrying with conciseness hint"  # noqa: E501
                )
                retry_hint = (
                    "IMPORTANT: Your previous response contained invalid JSON. "
                    "Return ONLY a compact JSON object. "
                    "Shorten specification values to at most 5 words each. "
                    "Do not include any text outside the JSON object."
                )
                raw2 = _call_claude(chunk_text, hint=retry_hint)
                try:
                    return _clean_and_parse(raw2)
                except json.JSONDecodeError as second_err:
                    logger.error(f"JSON parse failed on retry: {second_err}")
                    raise

        def _merge(a: dict, b: dict) -> dict:
            """Merge two entity dicts, deduplicating parts by part_id."""
            seen_parts = {p["part_id"] for p in a.get("parts", []) if p.get("part_id")}
            merged_parts = list(a.get("parts", []))
            for p in b.get("parts", []):
                if p.get("part_id") and p["part_id"] not in seen_parts:
                    merged_parts.append(p)
                    seen_parts.add(p["part_id"])

            seen_suppliers = {s["name"] for s in a.get("suppliers", []) if s.get("name")}
            merged_suppliers = list(a.get("suppliers", []))
            for s in b.get("suppliers", []):
                if s.get("name") and s["name"] not in seen_suppliers:
                    merged_suppliers.append(s)
                    seen_suppliers.add(s["name"])

            merged_rels = list(a.get("relationships", [])) + b.get("relationships", [])

            return {
                "parts":         merged_parts,
                "suppliers":     merged_suppliers,
                "relationships": merged_rels,
            }

        try:
            # Split into chunks if the text is large
            if len(text) <= CHUNK_SIZE:
                chunks = [text]
            else:
                # Split on double-newlines (product boundaries) where possible
                paragraphs = text.split("\n\n")
                chunks, current = [], ""
                for para in paragraphs:
                    if len(current) + len(para) + 2 > CHUNK_SIZE and current:
                        chunks.append(current.strip())
                        current = para
                    else:
                        current += ("\n\n" if current else "") + para
                if current.strip():
                    chunks.append(current.strip())

            logger.info(
                f"Extracting from {len(text)} chars split into {len(chunks)} chunk(s)"
            )

            entities: dict = {"parts": [], "suppliers": [], "relationships": []}
            for i, chunk in enumerate(chunks):
                logger.info(f"Processing chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)")
                chunk_entities = _extract_chunk(chunk)
                entities = _merge(entities, chunk_entities)

            # Final deduplication pass — catches duplicates within a single chunk
            # (Claude occasionally extracts the same part from a header and a table)
            seen_parts: set = set()
            deduped_parts = []
            for p in entities.get("parts", []):
                pid = (p.get("part_id") or "").strip()
                if pid and pid not in seen_parts:
                    seen_parts.add(pid)
                    deduped_parts.append(p)
                elif not pid:
                    deduped_parts.append(p)  # keep parts without ID; persist will handle them

            seen_suppliers: set = set()
            deduped_suppliers = []
            for s in entities.get("suppliers", []):
                # Normalise name for comparison: lowercase, collapse whitespace
                name_key = " ".join((s.get("name") or "").lower().split())
                if name_key and name_key not in seen_suppliers:
                    seen_suppliers.add(name_key)
                    deduped_suppliers.append(s)

            seen_rels: set = set()
            deduped_rels = []
            for r in entities.get("relationships", []):
                key = (
                    (r.get("supplier_name") or "").lower().strip(),
                    (r.get("part_id") or "").strip(),
                )
                if key not in seen_rels:
                    seen_rels.add(key)
                    deduped_rels.append(r)

            entities = {
                "parts":         deduped_parts,
                "suppliers":     deduped_suppliers,
                "relationships": deduped_rels,
            }

            duplicates_removed = (
                len(entities.get("parts", [])) - len(deduped_parts) +
                len(entities.get("suppliers", [])) - len(deduped_suppliers) +
                len(entities.get("relationships", [])) - len(deduped_rels)
            )
            if duplicates_removed > 0:
                logger.info(f"Removed {duplicates_removed} duplicate entities after extraction")

            # Calculate confidence
            confidence = 0.85
            if entities.get("parts"):
                confidence += 0.05
            if entities.get("suppliers"):
                confidence += 0.05
            if entities.get("relationships"):
                confidence += 0.05

            logger.success(
                f"Extracted {len(entities['parts'])} parts, "
                f"{len(entities['suppliers'])} suppliers, "
                f"{len(entities['relationships'])} relationships "
                f"from {len(chunks)} chunk(s)"
            )

            # Normalise UOM and category across all extracted parts
            for part in entities.get("parts", []):
                part["unit_of_measure"] = _normalise_uom(
                    part.get("unit_of_measure", "EA")
                )
                part["category"] = _normalise_category(
                    part.get("category", "")
                )

            return ExtractionResult(
                entities=[entities],
                confidence=min(confidence, 1.0),
                source=document_type,
                extraction_method=f"claude_{self.model}_direct",
            )

        except Exception as e:
            logger.error(f"Direct API extraction failed: {e}")
            raise

    def _calculate_confidence(
        self,
        result: SupplyChainDocument,
        original_text: str
    ) -> float:
        """
        Calculate extraction confidence score.

        Args:
            result: Extraction result
            original_text: Original document text

        Returns:
            Confidence score (0.0-1.0)
        """
        confidence = 0.5  # Base confidence

        # More entities = higher confidence (up to a point)
        entity_count = len(result.parts) + len(result.suppliers) + len(result.relationships)
        confidence += min(entity_count * 0.05, 0.3)

        # Complete fields = higher confidence
        for part in result.parts:
            if part.specifications:
                confidence += 0.02
            if part.category:
                confidence += 0.01

        for supplier in result.suppliers:
            if supplier.certifications:
                confidence += 0.02
            if supplier.location:
                confidence += 0.01

        for rel in result.relationships:
            if rel.lead_time_days:
                confidence += 0.01
            if rel.price:
                confidence += 0.01

        return min(confidence, 1.0)

    def batch_extract(
        self,
        texts: List[str],
        document_types: Optional[List[str]] = None
    ) -> List[ExtractionResult]:
        """
        Extract entities from multiple documents.

        Args:
            texts: List of document texts
            document_types: List of document types (optional)

        Returns:
            List of extraction results
        """
        if document_types is None:
            document_types = ["unknown"] * len(texts)

        results = []
        for i, (text, doc_type) in enumerate(zip(texts, document_types)):
            logger.info(f"Processing document {i+1}/{len(texts)}")
            try:
                result = self.extract_from_text(text, doc_type, source=f"doc_{i+1}")
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to extract from document {i+1}: {e}")
                continue

        return results


# Convenience function
def extract_entities_from_text(
    text: str,
    document_type: str = "unknown",
    source: str = "unknown"
) -> ExtractionResult:
    """
    Convenience function to extract entities using Claude.

    Args:
        text: Document text
        document_type: Type of document
        source: Source identifier

    Returns:
        ExtractionResult
    """
    extractor = ClaudeEntityExtractor()
    return extractor.extract_from_text(text, document_type, source)
