"""
Specific rules for supply chain reasoning.

These implement business logic for part compatibility, supplier qualification,
lead time feasibility, and other supply chain decisions.
"""

from typing import Dict, Any, Optional
from datetime import date, timedelta

from src.reasoning.rules_engine import BaseRule, RuleResult, RuleType, RuleSeverity
from loguru import logger


class PartCompatibilityRule(BaseRule):
    """
    Check if two parts are compatible for substitution.

    Decision priority:
      1. Verified COMPATIBLE_WITH edge in the graph (db kwarg required) —
         engineering pre-approval overrides spec comparison and passes immediately.
      2. Spec comparison (category, key specifications, certifications) — used
         when no pre-approved relationship exists or db is not provided.
    """

    def __init__(self):
        super().__init__(
            name="PartCompatibilityRule",
            rule_type=RuleType.COMPATIBILITY,
            severity=RuleSeverity.CRITICAL
        )

    def check(
        self,
        original_part: Dict[str, Any],
        substitute_part: Dict[str, Any],
        db=None,
    ) -> RuleResult:
        """
        Check if substitute can replace original part.

        Args:
            original_part:   Original part data (with specifications_json)
            substitute_part: Potential substitute part data
            db:              Optional Neo4jClient. When provided, checks for a
                             VERIFIED COMPATIBLE_WITH edge before running spec
                             comparison. Pass db=db from the router.

        Returns:
            RuleResult
        """
        import json

        original_id = original_part.get('id')
        substitute_id = substitute_part.get('id')

        self.facts_used = [
            f"original_part:{original_id}",
            f"substitute_part:{substitute_id}",
        ]

        # ── Step 1: graph pre-approval check ─────────────────────────────────
        # A VERIFIED COMPATIBLE_WITH relationship is engineering sign-off and
        # overrides spec comparison entirely — if the team has already validated
        # the substitution, the rule should reflect that.
        if db is not None and original_id and substitute_id:
            try:
                rows = db.execute_query(
                    """
                    MATCH (orig:Part {id: $orig_id})-[r:COMPATIBLE_WITH]->(sub:Part {id: $sub_id})
                    WHERE r.validation_status = 'VERIFIED'
                    RETURN r.compatibility_type    AS compatibility_type,
                           r.notes                 AS notes,
                           toString(r.validated_date) AS validated_date,
                           r.validated_by          AS validated_by
                    """,
                    {"orig_id": original_id, "sub_id": substitute_id},
                )
                if rows:
                    rel = rows[0]
                    self.facts_used.append("source:COMPATIBLE_WITH_graph_relationship")
                    return self._create_result(
                        passed=True,
                        reason=(
                            f"Engineering-verified substitution "
                            f"({rel.get('compatibility_type', 'VERIFIED')}): "
                            f"{rel.get('notes') or 'pre-approved in graph'}"
                        ),
                        details={
                            "compatibility_type": rel.get("compatibility_type"),
                            "validated_by":       rel.get("validated_by"),
                            "validated_date":     rel.get("validated_date"),
                            "notes":              rel.get("notes"),
                            "source":             "COMPATIBLE_WITH_relationship",
                        },
                        confidence=0.99,
                    )
            except Exception as exc:
                # Graph lookup failed — log and fall through to spec comparison
                logger.warning(
                    f"COMPATIBLE_WITH lookup failed for {original_id}→{substitute_id}: {exc}"
                )

        # ── Step 2: spec-based comparison (fallback / no db) ─────────────────
        try:
            original_specs = json.loads(original_part.get('specifications_json', '{}'))
            substitute_specs = json.loads(substitute_part.get('specifications_json', '{}'))
        except json.JSONDecodeError:
            return self._create_result(
                passed=False,
                reason="Failed to parse specifications",
                details={"error": "Invalid JSON in specifications"},
                confidence=0.0
            )

        # Check category match
        if original_part.get('category') != substitute_part.get('category'):
            return self._create_result(
                passed=False,
                reason=f"Category mismatch: {original_part.get('category')} vs {substitute_part.get('category')}",  # noqa: E501
                details={
                    "original_category": original_part.get('category'),
                    "substitute_category": substitute_part.get('category')
                },
                confidence=1.0
            )

        # Check key specifications
        mismatches = []
        for key in ['voltage', 'power_rating']:
            if key in original_specs:
                if key not in substitute_specs:
                    mismatches.append(f"{key} missing in substitute")
                elif original_specs[key] != substitute_specs[key]:
                    mismatches.append(f"{key}: {original_specs[key]} → {substitute_specs[key]}")

        if mismatches:
            return self._create_result(
                passed=False,
                reason=f"Specification mismatches: {'; '.join(mismatches)}",
                details={"mismatches": mismatches},
                confidence=1.0
            )

        # Check certifications — substitute must cover all original certs
        original_certs = set(original_specs.get('certifications', []))
        substitute_certs = set(substitute_specs.get('certifications', []))

        missing_certs = original_certs - substitute_certs
        if missing_certs:
            return self._create_result(
                passed=False,
                reason=f"Missing certifications: {', '.join(missing_certs)}",
                details={"missing_certifications": list(missing_certs)},
                confidence=1.0
            )

        # All spec checks passed
        return self._create_result(
            passed=True,
            reason="All compatibility checks passed",
            details={
                "category_match": True,
                "specifications_compatible": True,
                "certifications_sufficient": True
            },
            confidence=0.95
        )


class LeadTimeFeasibilityRule(BaseRule):
    """Check if supplier can meet required delivery date."""

    def __init__(self):
        super().__init__(
            name="LeadTimeFeasibilityRule",
            rule_type=RuleType.CONSTRAINT,
            severity=RuleSeverity.ERROR
        )

    def check(
        self,
        supplier_lead_time_days: int,
        required_date: date,
        order_date: Optional[date] = None
    ) -> RuleResult:
        """
        Check if supplier can deliver by required date.

        Args:
            supplier_lead_time_days: Supplier's typical lead time
            required_date: When parts are needed
            order_date: When order will be placed (default: today)

        Returns:
            RuleResult
        """
        if order_date is None:
            order_date = date.today()

        self.facts_used = [
            f"lead_time:{supplier_lead_time_days}",
            f"required_date:{required_date}",
            f"order_date:{order_date}"
        ]

        earliest_delivery = order_date + timedelta(days=supplier_lead_time_days)
        days_difference = (required_date - earliest_delivery).days

        if earliest_delivery > required_date:
            return self._create_result(
                passed=False,
                reason=f"Cannot deliver on time: earliest delivery {earliest_delivery}, needed by {required_date}",  # noqa: E501
                details={
                    "earliest_delivery": earliest_delivery.isoformat(),
                    "required_date": required_date.isoformat(),
                    "days_late": abs(days_difference)
                },
                confidence=1.0
            )

        # Calculate confidence based on buffer
        buffer_days = days_difference
        if buffer_days >= 7:
            confidence = 0.95
        elif buffer_days >= 3:
            confidence = 0.85
        else:
            confidence = 0.75

        return self._create_result(
            passed=True,
            reason=f"Can deliver on time with {buffer_days} days buffer",
            details={
                "earliest_delivery": earliest_delivery.isoformat(),
                "required_date": required_date.isoformat(),
                "buffer_days": buffer_days
            },
            confidence=confidence
        )


class SupplierQualificationRule(BaseRule):
    """Check if supplier meets qualification requirements."""

    def __init__(self):
        super().__init__(
            name="SupplierQualificationRule",
            rule_type=RuleType.VALIDATION,
            severity=RuleSeverity.ERROR
        )

    def check(
        self,
        supplier: Dict[str, Any],
        required_certifications: Optional[list] = None,
        min_rating: float = 3.5
    ) -> RuleResult:
        """
        Check if supplier is qualified.

        Args:
            supplier: Supplier data
            required_certifications: List of required certs
            min_rating: Minimum acceptable rating

        Returns:
            RuleResult
        """
        self.facts_used = [f"supplier:{supplier.get('id')}"]

        issues = []

        # Check status
        if supplier.get('status') != 'ACTIVE':
            return self._create_result(
                passed=False,
                reason=f"Supplier status is {supplier.get('status')}, not ACTIVE",
                details={"status": supplier.get('status')},
                severity=RuleSeverity.CRITICAL,
                confidence=1.0
            )

        # Check certifications
        if required_certifications:
            supplier_certs = set(supplier.get('certifications', []))
            missing = set(required_certifications) - supplier_certs
            if missing:
                issues.append(f"Missing certifications: {', '.join(missing)}")

        # Check rating
        rating = supplier.get('rating', 0.0)
        if rating < min_rating:
            issues.append(f"Rating {rating} below minimum {min_rating}")

        if issues:
            return self._create_result(
                passed=False,
                reason="; ".join(issues),
                details={"issues": issues},
                confidence=1.0
            )

        return self._create_result(
            passed=True,
            reason="Supplier meets all qualification requirements",
            details={
                "status": "ACTIVE",
                "rating": rating,
                "certifications_met": True
            },
            confidence=0.95
        )


class PriceReasonablenessRule(BaseRule):
    """Check if price is reasonable compared to historical data."""

    def __init__(self):
        super().__init__(
            name="PriceReasonablenessRule",
            rule_type=RuleType.VALIDATION,
            severity=RuleSeverity.WARNING
        )

    def check(
        self,
        current_price: float,
        historical_prices: list,
        max_deviation_percent: float = 30.0
    ) -> RuleResult:
        """
        Check if price is within reasonable range.

        Args:
            current_price: Price being evaluated
            historical_prices: List of historical prices
            max_deviation_percent: Maximum acceptable deviation

        Returns:
            RuleResult
        """
        if not historical_prices:
            return self._create_result(
                passed=True,
                reason="No historical data to compare",
                details={"note": "First price for this item"},
                confidence=0.5
            )

        avg_price = sum(historical_prices) / len(historical_prices)
        deviation_percent = abs((current_price - avg_price) / avg_price) * 100

        if deviation_percent > max_deviation_percent:
            return self._create_result(
                passed=False,
                reason=f"Price deviation {deviation_percent:.1f}% exceeds maximum {max_deviation_percent}%",  # noqa: E501
                details={
                    "current_price": current_price,
                    "average_price": avg_price,
                    "deviation_percent": deviation_percent
                },
                confidence=0.9
            )

        return self._create_result(
            passed=True,
            reason=f"Price within acceptable range (deviation: {deviation_percent:.1f}%)",
            details={
                "current_price": current_price,
                "average_price": avg_price,
                "deviation_percent": deviation_percent
            },
            confidence=0.9
        )
