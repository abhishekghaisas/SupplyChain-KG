"""
Substitute suggester — grounded AI inference of compatible parts.

Workflow
────────
1. Fetch the source part's full spec data from Neo4j
2. Find top-N semantically similar parts via vector search
3. For each candidate, fetch its spec data
4. Claude compares specs and returns structured reasoning:
     - which specs match
     - which specs differ and whether the difference is material
     - an overall compatibility verdict and confidence score
5. Write COMPATIBLE_WITH relationships for approved candidates with
   validation_status = "INFERRED" and the full reasoning stored as JSON

The engineer then reviews inferred substitutes and either:
  - VERIFY: promotes to validation_status = "VERIFIED"
  - REJECT: sets validation_status = "REJECTED"

This means the BOM review can say "1 inferred substitute — requires validation"
rather than "no substitute on record", which is far more actionable.

Confidence scoring
──────────────────
  0.9+  Identical specs, same category, only minor differences
  0.7-0.9  Compatible with one or two acceptable differences
  0.5-0.7  Potentially compatible but significant differences exist
  <0.5    Not recommended — too many spec mismatches

Public API
──────────
  SubstituteSuggester.suggest(part_id, max_candidates=5) -> List[SubstituteSuggestion]
  SubstituteSuggester.persist(part_id, suggestions)      -> int (count written)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from loguru import logger

from src.config import get_settings


@dataclass
class SpecComparison:
    """Comparison of a single specification field between two parts."""

    spec_name: str
    source_value: Any
    candidate_value: Any
    match: bool  # True if values are compatible
    material: bool  # True if a mismatch would matter in practice
    note: str  # Plain-English explanation


@dataclass
class SubstituteSuggestion:
    """A candidate substitute part with full reasoning."""

    source_part_id: str
    source_part_name: str
    candidate_part_id: str
    candidate_part_name: str
    semantic_score: float  # vector similarity (0-1)
    confidence: float  # Claude's compatibility confidence (0-1)
    verdict: str  # COMPATIBLE | LIKELY_COMPATIBLE | INCOMPATIBLE
    summary: str  # one-sentence explanation
    spec_comparisons: List[SpecComparison]
    matching_specs: List[str]  # spec names that match
    differing_specs: List[str]  # spec names that differ materially
    reasoning: str  # full Claude reasoning text
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_part_id": self.source_part_id,
            "source_part_name": self.source_part_name,
            "candidate_part_id": self.candidate_part_id,
            "candidate_part_name": self.candidate_part_name,
            "semantic_score": self.semantic_score,
            "confidence": self.confidence,
            "verdict": self.verdict,
            "summary": self.summary,
            "matching_specs": self.matching_specs,
            "differing_specs": self.differing_specs,
            "reasoning": self.reasoning,
            "spec_comparisons": [
                {
                    "spec": sc.spec_name,
                    "source": sc.source_value,
                    "candidate": sc.candidate_value,
                    "match": sc.match,
                    "material": sc.material,
                    "note": sc.note,
                }
                for sc in self.spec_comparisons
            ],
            "generated_at": self.generated_at,
        }


def rerank_substitute_suggestions(
    suggestions: List["SubstituteSuggestion"],
    source_category: str,
) -> List["SubstituteSuggestion"]:
    """
    Rerank substitute suggestions by composite score.

    Score = confidence × semantic_score × category_bonus - material_diff_penalty

    This ensures:
    - Claude's compatibility verdict is the primary signal
    - Semantic similarity acts as a tiebreaker
    - Material spec mismatches are penalised
    - INCOMPATIBLE verdicts are excluded entirely
    """
    VERDICT_WEIGHT = {"COMPATIBLE": 1.0, "LIKELY_COMPATIBLE": 0.8, "INCOMPATIBLE": 0.0}
    MATERIAL_DIFF_PENALTY = 0.15  # per material spec mismatch

    def _score(s: "SubstituteSuggestion") -> float:
        verdict_weight = VERDICT_WEIGHT.get(s.verdict, 0.5)
        category_bonus = 1.0  # same category enforced upstream
        material_diffs = sum(1 for sc in s.spec_comparisons if not sc.match and sc.material)
        penalty = material_diffs * MATERIAL_DIFF_PENALTY

        return s.confidence * s.semantic_score * verdict_weight * category_bonus - penalty

    ranked = [s for s in suggestions if s.verdict != "INCOMPATIBLE"]
    ranked.sort(key=_score, reverse=True)
    return ranked


class SubstituteSuggester:
    """
    Grounded AI substitute suggester.
    Uses vector search to find candidates then Claude to evaluate
    spec compatibility with explicit reasoning for each comparison.
    """

    _SYSTEM_PROMPT = """You are a supply chain engineer evaluating whether one part can substitute for another.  # noqa: E501

Your job is to compare two parts' specifications and give a structured compatibility assessment.

RULES:
1. Base your assessment ONLY on the specification data provided — never use training knowledge
   about specific part numbers or manufacturers.
2. Focus on functional compatibility: would the substitute work in the same application?
3. Minor differences (e.g. slightly higher rating, same form factor) are often acceptable.
   Material differences (e.g. different voltage, incompatible interface) are blockers.
4. Be specific: cite actual spec values when explaining matches and mismatches.
5. If a spec is present in one part but absent in the other, flag it as an unknown gap,
   not a mismatch — the data may simply not be recorded.

Return your assessment as valid JSON with exactly this structure:
{
  "verdict": "COMPATIBLE" | "LIKELY_COMPATIBLE" | "INCOMPATIBLE",
  "confidence": <float 0.0-1.0>,
  "summary": "<one sentence explaining the verdict>",
  "spec_comparisons": [
    {
      "spec": "<spec name>",
      "source_value": "<value from source part>",
      "candidate_value": "<value from candidate part>",
      "match": <true|false>,
      "material": <true if a mismatch here would block substitution>,
      "note": "<brief explanation>"
    }
  ],
  "matching_specs": ["<list of spec names that are compatible>"],
  "differing_specs": ["<list of spec names with material differences>"],
  "reasoning": "<2-3 sentences of overall reasoning>"
}

Return ONLY the JSON. No preamble, no markdown fences."""

    def __init__(self, db, model: Optional[str] = None):
        settings = get_settings()
        self.db = db
        self.model = model or settings.claude_model
        self.client = Anthropic(api_key=settings.anthropic_api_key)

    def _fetch_part(self, part_id: str) -> Optional[Dict]:
        """Fetch full part data including parsed specs."""
        rows = self.db.execute_query(
            """
            MATCH (p:Part {id: $id})
            RETURN p.id AS id, p.name AS name, p.category AS category,
                   p.criticality AS criticality, p.description AS description,
                   p.specifications_json AS specifications_json,
                   p.unit_of_measure AS unit_of_measure
            """,
            {"id": part_id},
        )
        if not rows:
            return None
        part = dict(rows[0])
        # Parse specs JSON
        try:
            part["specifications"] = json.loads(part.get("specifications_json") or "{}")
        except Exception:
            part["specifications"] = {}
        return part

    def _find_candidates(
        self,
        part: Dict,
        max_candidates: int = 5,
    ) -> List[Dict]:
        """
        Find candidate substitute parts via vector similarity search.

        Filters to:
        - Same category as source part
        - Not the source part itself
        - Semantic similarity >= 0.4
        """
        from src.search.embedder import embed, part_text
        from src.search.vector_store import search

        text = part_text(part)
        query_vec = embed(text)
        results = search(
            query_vec,
            entity_type="part",
            limit=max_candidates + 1,  # +1 to account for self-match
            min_score=0.35,
        )

        candidates = []
        for r in results:
            if r.entity_id == part["id"]:
                continue  # skip self
            candidate = self._fetch_part(r.entity_id)
            if candidate and candidate.get("category") == part.get("category"):
                candidate["_semantic_score"] = r.score
                candidates.append(candidate)
            if len(candidates) >= max_candidates:
                break

        logger.info(
            f"Found {len(candidates)} candidates for {part['id']} "
            f"(category={part.get('category')!r})"
        )
        return candidates

    def _compare_parts(
        self,
        source: Dict,
        candidate: Dict,
    ) -> SubstituteSuggestion:
        """
        Ask Claude to compare two parts' specs and return structured reasoning.
        """
        source_data = {
            "id": source["id"],
            "name": source["name"],
            "category": source.get("category"),
            "criticality": source.get("criticality"),
            "description": source.get("description"),
            "specifications": source.get("specifications", {}),
        }
        candidate_data = {
            "id": candidate["id"],
            "name": candidate["name"],
            "category": candidate.get("category"),
            "criticality": candidate.get("criticality"),
            "description": candidate.get("description"),
            "specifications": candidate.get("specifications", {}),
        }

        user_prompt = f"""Evaluate whether the CANDIDATE part can substitute for the SOURCE part.

SOURCE PART (the one we need to replace):
{json.dumps(source_data, indent=2, default=str)}

CANDIDATE PART (potential substitute):
{json.dumps(candidate_data, indent=2, default=str)}

Compare their specifications and return your structured assessment as JSON."""

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=[
                    {
                        "type": "text",
                        "text": self._SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = message.content[0].text.strip()

            # Strip markdown fences if present
            if "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            result = json.loads(raw)

            # Build SpecComparison objects
            spec_comparisons = [
                SpecComparison(
                    spec_name=sc["spec"],
                    source_value=sc.get("source_value"),
                    candidate_value=sc.get("candidate_value"),
                    match=sc.get("match", False),
                    material=sc.get("material", False),
                    note=sc.get("note", ""),
                )
                for sc in result.get("spec_comparisons", [])
            ]

            return SubstituteSuggestion(
                source_part_id=source["id"],
                source_part_name=source["name"],
                candidate_part_id=candidate["id"],
                candidate_part_name=candidate["name"],
                semantic_score=candidate.get("_semantic_score", 0.0),
                confidence=float(result.get("confidence", 0.5)),
                verdict=result.get("verdict", "LIKELY_COMPATIBLE"),
                summary=result.get("summary", ""),
                spec_comparisons=spec_comparisons,
                matching_specs=result.get("matching_specs", []),
                differing_specs=result.get("differing_specs", []),
                reasoning=result.get("reasoning", ""),
            )

        except Exception as exc:
            logger.error(f"Spec comparison failed for {candidate['id']}: {exc}")
            return SubstituteSuggestion(
                source_part_id=source["id"],
                source_part_name=source["name"],
                candidate_part_id=candidate["id"],
                candidate_part_name=candidate["name"],
                semantic_score=candidate.get("_semantic_score", 0.0),
                confidence=0.0,
                verdict="INCOMPATIBLE",
                summary=f"Analysis failed: {exc}",
                spec_comparisons=[],
                matching_specs=[],
                differing_specs=[],
                reasoning="Analysis could not be completed.",
            )

    def suggest(
        self,
        part_id: str,
        max_candidates: int = 5,
    ) -> List[SubstituteSuggestion]:
        """
        Find and evaluate substitute candidates for a part.

        Args:
            part_id:        The part to find substitutes for.
            max_candidates: Maximum number of candidates to evaluate.

        Returns:
            List of SubstituteSuggestion sorted by confidence descending.
            Only includes COMPATIBLE and LIKELY_COMPATIBLE verdicts.
        """
        source = self._fetch_part(part_id)
        if not source:
            raise ValueError(f"Part {part_id!r} not found")

        candidates = self._find_candidates(source, max_candidates)
        if not candidates:
            logger.info(f"No candidates found for {part_id}")
            return []

        suggestions = []
        for candidate in candidates:
            logger.info(
                f"Comparing {part_id} with {candidate['id']} "
                f"(semantic={candidate.get('_semantic_score', 0):.2f})"
            )
            suggestion = self._compare_parts(source, candidate)
            if suggestion.verdict != "INCOMPATIBLE":
                suggestions.append(suggestion)

        # Rerank by composite score: confidence × semantic × verdict - material penalties
        suggestions = rerank_substitute_suggestions(suggestions, source.get("category", ""))
        logger.info(
            f"Substitute analysis for {part_id}: "
            f"{len(suggestions)} compatible candidates from {len(candidates)} evaluated"
        )
        return suggestions

    def persist(
        self,
        part_id: str,
        suggestions: List[SubstituteSuggestion],
        min_confidence: float = 0.5,
    ) -> int:
        """
        Write inferred COMPATIBLE_WITH relationships to the graph.

        Only persists suggestions with confidence >= min_confidence.
        Uses MERGE so re-running is safe (idempotent).

        Returns count of relationships written.
        """
        written = 0
        for s in suggestions:
            if s.confidence < min_confidence:
                continue

            reasoning_json = json.dumps(
                {
                    "summary": s.summary,
                    "reasoning": s.reasoning,
                    "matching_specs": s.matching_specs,
                    "differing_specs": s.differing_specs,
                    "semantic_score": s.semantic_score,
                    "spec_comparisons": [
                        {
                            "spec": sc.spec_name,
                            "match": sc.match,
                            "material": sc.material,
                            "note": sc.note,
                        }
                        for sc in s.spec_comparisons
                    ],
                }
            )

            self.db.execute_write(
                """
                MATCH (source:Part {id: $source_id})
                MATCH (candidate:Part {id: $candidate_id})
                MERGE (source)-[r:COMPATIBLE_WITH]->(candidate)
                SET r.compatibility_type  = 'INFERRED',
                    r.validation_status   = 'INFERRED',
                    r.confidence          = $confidence,
                    r.reasoning_json      = $reasoning_json,
                    r.inferred_by         = 'claude-ai',
                    r.inferred_at         = datetime(),
                    r.notes               = $summary
                """,
                {
                    "source_id": part_id,
                    "candidate_id": s.candidate_part_id,
                    "confidence": s.confidence,
                    "reasoning_json": reasoning_json,
                    "summary": s.summary,
                },
            )
            written += 1
            logger.info(
                f"Wrote INFERRED substitute: {part_id} → {s.candidate_part_id} "
                f"(confidence={s.confidence:.2f})"
            )

        return written
