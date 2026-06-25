"""
Specific rules for supply chain reasoning.

These implement business logic for part compatibility, supplier qualification,
lead time feasibility, and other supply chain decisions.
"""

# fmt: off
from typing import Dict, Any, Optional
from datetime import date, timedelta

from src.reasoning.rules_engine import BaseRule, RuleResult, RuleType, RuleSeverity


class PartCompatibilityRule(BaseRule):
    """Check if two parts are compatible for substitution."""

    def __init__(self):
        super().__init__(
            name="PartCompatibilityRule",
            rule_type=RuleType.COMPATIBILITY,
            severity=RuleSeverity.CRITICAL,
        )

    def check(self, original_part: Dict[str, Any], substitute_part: Dict[str, Any],
              db=None) -> RuleResult:
        """
        Check if substitute can replace original part.

        Args:
            original_part: Original part data (with specifications_json)
            substitute_part: Potential substitute part data

        Returns:
            RuleResult
        """
        import json

        self.facts_used = [
            f"original_part:{original_part.get('id')}",
            f"substitute_part:{substitute_part.get('id')}",
        ]

        # ── Path 1: graph pre-approval ────────────────────────────────────────
        if db is not None:
            orig_id = original_part.get("id")
            sub_id = substitute_part.get("id")
            query = (
                "MATCH (o:Part {id: $orig_id})-[r:COMPATIBLE_WITH "
                "{validation_status: 'VERIFIED'}]->(s:Part {id: $sub_id}) "
                "RETURN r.compatibility_type AS compatibility_type, "
                "r.notes AS notes, r.validated_by AS validated_by, "
                "r.validated_date AS validated_date LIMIT 1"
            )
            try:
                rows = db.execute_query(query, {"orig_id": orig_id, "sub_id": sub_id})
                if rows:
                    edge = rows[0]
                    notes = edge.get("notes") or ""
                    self.facts_used.append("graph:COMPATIBLE_WITH:VERIFIED")
                    reason = "Engineering-verified COMPATIBLE_WITH relationship found"
                    if notes:
                        reason = f"{reason}; {notes}"
                    return self._create_result(
                        passed=True,
                        reason=reason,
                        details={
                            "source":             "COMPATIBLE_WITH_relationship",
                            "compatibility_type": edge.get("compatibility_type"),
                            "notes":              notes,
                            "validated_by":       edge.get("validated_by"),
                            "validated_date":     str(edge.get("validated_date", "")),
                        },
                        confidence=1.0,
                    )
            except Exception as exc:
                from loguru import logger
                logger.warning(
                    f"Graph lookup failed for {orig_id}→{sub_id}: {exc}. "
                    "Falling through to spec comparison."
                )
        # ── Path 2: spec comparison (fallback) ────────────────────────────────

        # Parse specifications
        try:
            original_specs = json.loads(original_part.get("specifications_json", "{}"))
            substitute_specs = json.loads(substitute_part.get("specifications_json", "{}"))
        except json.JSONDecodeError:
            return self._create_result(
                passed=False,
                reason="Failed to parse specifications",
                details={"error": "Invalid JSON in specifications"},
                confidence=0.0,
            )

        # Check category match
        if original_part.get("category") != substitute_part.get("category"):
            return self._create_result(
                passed=False,
                reason=f"Category mismatch: {original_part.get('category')} vs {substitute_part.get('category')}",  # noqa: E501
                details={
                    "original_category": original_part.get("category"),
                    "substitute_category": substitute_part.get("category"),
                },
                confidence=1.0,
            )

        # Check key specifications
        mismatches = []
        for key in ["voltage", "power_rating"]:
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
                confidence=1.0,
            )

        # Check certifications
        original_certs = set(original_specs.get("certifications", []))
        substitute_certs = set(substitute_specs.get("certifications", []))

        missing_certs = original_certs - substitute_certs
        if missing_certs:
            return self._create_result(
                passed=False,
                reason=f"Missing certifications: {', '.join(missing_certs)}",
                details={"missing_certifications": list(missing_certs)},
                confidence=1.0,
            )

        # All checks passed
        return self._create_result(
            passed=True,
            reason="All compatibility checks passed",
            details={
                "category_match": True,
                "specifications_compatible": True,
                "certifications_sufficient": True,
            },
            confidence=0.95,
        )


class LeadTimeFeasibilityRule(BaseRule):
    """Check if supplier can meet required delivery date."""

    def __init__(self):
        super().__init__(
            name="LeadTimeFeasibilityRule",
            rule_type=RuleType.CONSTRAINT,
            severity=RuleSeverity.ERROR,
        )

    def check(
        self, supplier_lead_time_days: int, required_date: date, order_date: Optional[date] = None
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
            f"order_date:{order_date}",
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
                    "days_late": abs(days_difference),
                },
                confidence=1.0,
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
                "buffer_days": buffer_days,
            },
            confidence=confidence,
        )


class SupplierQualificationRule(BaseRule):
    """Check if supplier meets qualification requirements."""

    def __init__(self):
        super().__init__(
            name="SupplierQualificationRule",
            rule_type=RuleType.VALIDATION,
            severity=RuleSeverity.ERROR,
        )

    def check(
        self,
        supplier: Dict[str, Any],
        required_certifications: Optional[list] = None,
        min_rating: float = 3.5,
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
        if supplier.get("status") != "ACTIVE":
            return self._create_result(
                passed=False,
                reason=f"Supplier status is {supplier.get('status')}, not ACTIVE",
                details={"status": supplier.get("status")},
                severity=RuleSeverity.CRITICAL,
                confidence=1.0,
            )

        # Check certifications
        if required_certifications:
            supplier_certs = set(supplier.get("certifications", []))
            missing = set(required_certifications) - supplier_certs
            if missing:
                issues.append(f"Missing certifications: {', '.join(missing)}")

        # Check rating
        rating = supplier.get("rating", 0.0)
        if rating < min_rating:
            issues.append(f"Rating {rating} below minimum {min_rating}")

        if issues:
            return self._create_result(
                passed=False, reason="; ".join(issues), details={"issues": issues}, confidence=1.0
            )

        return self._create_result(
            passed=True,
            reason="Supplier meets all qualification requirements",
            details={"status": "ACTIVE", "rating": rating, "certifications_met": True},
            confidence=0.95,
        )


class PriceReasonablenessRule(BaseRule):
    """Check if price is reasonable compared to historical data."""

    def __init__(self):
        super().__init__(
            name="PriceReasonablenessRule",
            rule_type=RuleType.VALIDATION,
            severity=RuleSeverity.WARNING,
        )

    def check(
        self,
        current_price: float,
        historical_prices: list,
        max_deviation_percent: float = 30.0,
        trend_window: Optional[int] = None,
        competitor_prices: Optional[list] = None,
        outlier_sigma: Optional[float] = None,
        benchmark_deviation_percent: Optional[float] = None,
        part_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
    ) -> RuleResult:
        """
        Check if price is within reasonable range.

        Args:
            current_price: Price being evaluated
            historical_prices: List of historical prices
            max_deviation_percent: Maximum acceptable deviation from average
            trend_window: If set, also check price trend over last N periods
            competitor_prices: If set, benchmark against competitor median
            outlier_sigma: If set, flag if price is N standard deviations out
            benchmark_deviation_percent: Override max_deviation for benchmark check
            part_id: Part identifier (recorded in facts_used)
            supplier_id: Supplier identifier (recorded in facts_used)
        """
        import math

        # Record provenance facts
        self.facts_used = [f"current_price:{current_price}"]
        if part_id:
            self.facts_used.append(f"part:{part_id}")
        if supplier_id:
            self.facts_used.append(f"supplier:{supplier_id}")
        if historical_prices:
            self.facts_used.append(f"historical_data_points:{len(historical_prices)}")

        if not historical_prices:
            return self._create_result(
                passed=True,
                reason="No historical data to compare",
                details={"note": "First price for this item", "sigma_distance": None},
                confidence=0.5,
            )

        avg_price = sum(historical_prices) / len(historical_prices)
        deviation_percent = abs((current_price - avg_price) / avg_price) * 100

        details: dict = {
            "current_price": current_price,
            "average_price": avg_price,
            "deviation_percent": deviation_percent,
        }

        failures = []

        # ── Zero / invalid price ─────────────────────────────────────────────
        if current_price <= 0:
            return self._create_result(
                passed=False,
                reason=f"Invalid price: {current_price} (must be > 0)",
                details=details,
                confidence=1.0,
            )

        # ── Statistical outlier (sigma) ──────────────────────────────────────
        sigma_distance = None
        if len(historical_prices) > 1:
            mean = avg_price
            variance = sum((p - mean) ** 2 for p in historical_prices) / len(historical_prices)
            std_dev = math.sqrt(variance)
            if std_dev > 0:
                sigma_distance = abs(current_price - mean) / std_dev
                threshold = outlier_sigma if outlier_sigma is not None else 2.0
                if sigma_distance > threshold:
                    failures.append(
                        f"Statistical outlier: {sigma_distance:.1f}sigma from mean (threshold {threshold}sigma)"  # noqa: E501
                    )
        details["sigma_distance"] = sigma_distance

        # ── Trend analysis ───────────────────────────────────────────────────
        trend_info: dict = {"analyzed": False}
        if trend_window is not None:
            if len(historical_prices) >= trend_window:
                recent = historical_prices[-trend_window:]
                recent_upward = all(
                    recent[i] < recent[i + 1] for i in range(len(recent) - 1)
                )
                trend_info = {
                    "analyzed": True,
                    "window": trend_window,
                    "recent_prices": recent,
                    "recent_upward_trend": recent_upward,
                }
                # Only flag if trend is upward AND new price continues higher
                last_price = historical_prices[-1]
                if recent_upward and current_price > last_price:
                    failures.append(
                        f"Upward price trend detected over last {trend_window} periods; new quote continues upward"  # noqa: E501
                    )
            else:
                trend_info = {"analyzed": False, "reason": "insufficient_data"}
        details["trend"] = trend_info

        # ── Competitor benchmark ─────────────────────────────────────────────
        if competitor_prices:
            sorted_c = sorted(competitor_prices)
            n = len(sorted_c)
            # Proper median: average the two middle values for even-length lists
            if n % 2 == 1:
                competitor_median = sorted_c[n // 2]
            else:
                competitor_median = (sorted_c[n // 2 - 1] + sorted_c[n // 2]) / 2.0
            bench_dev = (current_price - competitor_median) / competitor_median * 100
            bench_limit = (
                benchmark_deviation_percent
                if benchmark_deviation_percent is not None
                else max_deviation_percent
            )
            details["benchmark"] = {
                "competitor_count":          n,
                "competitor_median":         competitor_median,
                "deviation_from_median_pct": bench_dev,
                "limit":                     bench_limit,
            }
            if bench_dev > bench_limit:
                failures.append(
                    f"Price {bench_dev:.1f}% above competitor median (limit {bench_limit}%); competitor benchmark exceeded"  # noqa: E501
                )

        # ── Standard deviation check ─────────────────────────────────────────
        if deviation_percent > max_deviation_percent:
            failures.append(
                f"Price deviation {deviation_percent:.1f}% exceeds maximum {max_deviation_percent}%"
            )

        if failures:
            return self._create_result(
                passed=False,
                reason="; ".join(failures),
                details=details,
                confidence=0.9,
            )

        return self._create_result(
            passed=True,
            reason=f"Price within acceptable range (deviation: {deviation_percent:.1f}%)",
            details=details,
            confidence=0.9,
        )