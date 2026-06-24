"""
Rules engine for symbolic reasoning over knowledge graph.

This implements the "symbolic" component of the neuro-symbolic architecture,
applying explicit logic rules to validate and reason over extracted data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime

from loguru import logger


class RuleSeverity(Enum):
    """Severity level of rule violations."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RuleType(Enum):
    """Types of rules."""
    COMPATIBILITY = "compatibility"
    CONSTRAINT = "constraint"
    VALIDATION = "validation"
    BUSINESS_LOGIC = "business_logic"
    QUALITY = "quality"


@dataclass
class RuleResult:
    """Result of applying a rule."""
    passed: bool
    rule_name: str
    rule_type: RuleType
    reason: str
    severity: RuleSeverity
    details: Dict[str, Any] = field(default_factory=dict)
    facts_used: List[str] = field(default_factory=list)
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"{status} [{self.severity.value.upper()}] {self.rule_name}: {self.reason}"


@dataclass
class ReasoningResult:
    """Complete reasoning result with multiple rules applied."""
    subject: str  # What we're reasoning about
    passed: bool  # Overall pass/fail
    rules_applied: List[RuleResult]
    summary: str
    confidence: float
    provenance: Dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> List[RuleResult]:
        """Get all failed rules."""
        return [r for r in self.rules_applied if not r.passed]

    @property
    def critical_failures(self) -> List[RuleResult]:
        """Get critical failures."""
        return [r for r in self.failures if r.severity == RuleSeverity.CRITICAL]

    @property
    def warnings(self) -> List[RuleResult]:
        """Get warnings."""
        return [r for r in self.rules_applied if not r.passed and r.severity == RuleSeverity.WARNING]

    def __str__(self) -> str:
        status = "✓ PASSED" if self.passed else "✗ FAILED"
        return f"{status}: {self.subject} - {self.summary}"


class BaseRule(ABC):
    """Base class for all rules."""

    def __init__(self, name: str, rule_type: RuleType, severity: RuleSeverity = RuleSeverity.ERROR):
        self.name = name
        self.rule_type = rule_type
        self.severity = severity
        self.facts_used: List[str] = []

    @abstractmethod
    def check(self, *args, **kwargs) -> RuleResult:
        """Check if the rule passes. Must be implemented by subclasses."""
        pass

    def _create_result(
        self,
        passed: bool,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0
    ) -> RuleResult:
        """Helper to create a RuleResult."""
        return RuleResult(
            passed=passed,
            rule_name=self.name,
            rule_type=self.rule_type,
            reason=reason,
            severity=self.severity,
            details=details or {},
            facts_used=self.facts_used.copy(),
            confidence=confidence
        )


class RulesEngine:
    """
    Main rules engine that orchestrates rule checking.

    This is the symbolic reasoning component that validates data extracted
    by the neural component (Claude) and applies business logic.
    """

    def __init__(self):
        self.rules: Dict[str, BaseRule] = {}
        self.rule_groups: Dict[str, List[str]] = {}
        logger.info("Initialized RulesEngine")

    def register_rule(self, rule: BaseRule, groups: Optional[List[str]] = None) -> None:
        """
        Register a rule with the engine.

        Args:
            rule: Rule to register
            groups: Optional list of groups this rule belongs to
        """
        self.rules[rule.name] = rule

        if groups:
            for group in groups:
                if group not in self.rule_groups:
                    self.rule_groups[group] = []
                self.rule_groups[group].append(rule.name)

        logger.debug(f"Registered rule: {rule.name} (type: {rule.rule_type.value})")

    def apply_rule(self, rule_name: str, *args, **kwargs) -> RuleResult:
        """
        Apply a specific rule.

        Args:
            rule_name: Name of the rule to apply
            *args, **kwargs: Arguments to pass to the rule

        Returns:
            RuleResult
        """
        if rule_name not in self.rules:
            raise ValueError(f"Rule not found: {rule_name}")

        rule = self.rules[rule_name]
        logger.debug(f"Applying rule: {rule_name}")

        try:
            result = rule.check(*args, **kwargs)
            logger.debug(f"Rule {rule_name}: {'PASS' if result.passed else 'FAIL'}")
            return result
        except Exception as e:
            logger.error(f"Rule {rule_name} raised exception: {e}")
            return RuleResult(
                passed=False,
                rule_name=rule_name,
                rule_type=rule.rule_type,
                reason=f"Rule execution failed: {str(e)}",
                severity=RuleSeverity.ERROR,
                details={"error": str(e)}
            )

    def apply_group(self, group_name: str, *args, **kwargs) -> List[RuleResult]:
        """
        Apply all rules in a group.

        Args:
            group_name: Name of the rule group
            *args, **kwargs: Arguments to pass to each rule

        Returns:
            List of RuleResults
        """
        if group_name not in self.rule_groups:
            raise ValueError(f"Rule group not found: {group_name}")

        results = []
        for rule_name in self.rule_groups[group_name]:
            result = self.apply_rule(rule_name, *args, **kwargs)
            results.append(result)

        return results

    def evaluate(
        self,
        subject: str,
        rules: List[str],
        stop_on_critical: bool = True,
        *args,
        **kwargs
    ) -> ReasoningResult:
        """
        Evaluate multiple rules and return comprehensive result.

        Args:
            subject: What we're evaluating (for reporting)
            rules: List of rule names to apply
            stop_on_critical: Stop evaluation on critical failure
            *args, **kwargs: Arguments to pass to rules

        Returns:
            ReasoningResult
        """
        results = []

        for rule_name in rules:
            result = self.apply_rule(rule_name, *args, **kwargs)
            results.append(result)

            # Stop on critical failure if requested
            if stop_on_critical and not result.passed and result.severity == RuleSeverity.CRITICAL:
                logger.warning(f"Critical failure in rule {rule_name}, stopping evaluation")
                break

        # Determine overall pass/fail
        critical_failures = [
            r for r in results if not r.passed and r.severity == RuleSeverity.CRITICAL]
        error_failures = [r for r in results if not r.passed and r.severity == RuleSeverity.ERROR]

        overall_passed = len(critical_failures) == 0 and len(error_failures) == 0

        # Calculate overall confidence (average of all rules)
        avg_confidence = sum(r.confidence for r in results) / len(results) if results else 0.0

        # Create summary
        if overall_passed:
            summary = f"All {len(results)} rules passed"
        else:
            summary = f"{len(critical_failures)} critical, {len(error_failures)} errors"

        return ReasoningResult(
            subject=subject,
            passed=overall_passed,
            rules_applied=results,
            summary=summary,
            confidence=avg_confidence,
            provenance={
                "rules_evaluated": [r.rule_name for r in results],
                "evaluation_time": datetime.now().isoformat(),
                "stop_on_critical": stop_on_critical
            }
        )

    def get_rule_info(self) -> Dict[str, Any]:
        """Get information about registered rules."""
        return {
            "total_rules": len(self.rules),
            "rule_types": {
                rt.value: len([r for r in self.rules.values() if r.rule_type == rt])
                for rt in RuleType
            },
            "groups": {
                group: len(rules) for group, rules in self.rule_groups.items()
            }
        }


# Example usage and testing
if __name__ == "__main__":
    # Example: Simple rule
    class ExampleRule(BaseRule):
        def check(self, value: int) -> RuleResult:
            passed = value > 0
            return self._create_result(
                passed=passed,
                reason=f"Value is {'positive' if passed else 'not positive'}",
                details={"value": value}
            )

    engine = RulesEngine()
    engine.register_rule(
        ExampleRule("positive_value", RuleType.VALIDATION, RuleSeverity.ERROR)
    )

    result = engine.apply_rule("positive_value", value=10)
    print(result)
