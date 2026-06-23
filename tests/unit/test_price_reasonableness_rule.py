"""
Unit tests for PriceReasonablenessRule.

Run with:
    pytest test_price_reasonableness_rule.py -v

Requirements:
    pip install pytest loguru
"""

import pytest
from datetime import date

# ---------------------------------------------------------------------------
# If your project is installed as a package, use the real imports:
#
#   from src.reasoning.supply_chain_rules import PriceReasonablenessRule
#   from src.reasoning.rules_engine import RuleSeverity, RuleType
#
# Otherwise adjust the path so Python can find the modules, e.g.:
#
#   import sys, pathlib
#   sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
#
# ---------------------------------------------------------------------------
from src.reasoning.supply_chain_rules import PriceReasonablenessRule
from src.reasoning.rules_engine import RuleSeverity, RuleType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rule() -> PriceReasonablenessRule:
    return PriceReasonablenessRule()


# Spread data so that a price near the mean stays clearly within 2σ
SPREAD_HISTORY = [90.0, 95.0, 100.0, 105.0, 110.0]   # mean=100, σ≈7.9
TIGHT_HISTORY  = [100.0] * 8 + [102.0, 98.0]          # mean=100, σ≈0.63 (outlier-sensitive)


# ===========================================================================
# 1. Rule identity / metadata
# ===========================================================================

class TestRuleMetadata:
    def test_name(self):
        assert make_rule().name == "PriceReasonablenessRule"

    def test_rule_type(self):
        assert make_rule().rule_type == RuleType.VALIDATION

    def test_severity_is_warning(self):
        """Failures should be warnings so overall reasoning can still pass."""
        assert make_rule().severity == RuleSeverity.WARNING


# ===========================================================================
# 2. Input validation
# ===========================================================================

class TestInputValidation:
    def test_zero_price_fails(self):
        r = make_rule().check(0.0, SPREAD_HISTORY)
        assert not r.passed
        assert "invalid" in r.reason.lower()

    def test_negative_price_fails(self):
        r = make_rule().check(-50.0, SPREAD_HISTORY)
        assert not r.passed

    def test_valid_price_does_not_raise(self):
        r = make_rule().check(100.0, SPREAD_HISTORY)
        assert r is not None


# ===========================================================================
# 3. No historical data
# ===========================================================================

class TestNoHistory:
    def test_passes_with_no_history(self):
        r = make_rule().check(999.0, [])
        assert r.passed

    def test_passes_with_none_history(self):
        r = make_rule().check(999.0, None)
        assert r.passed

    def test_confidence_is_low_with_no_history(self):
        r = make_rule().check(100.0, [])
        assert r.confidence == 0.5

    def test_details_note_baseline(self):
        r = make_rule().check(100.0, [])
        assert "note" in r.details or "baseline" in r.reason.lower() or "no historical" in r.reason.lower()


# ===========================================================================
# 4. Average deviation check
# ===========================================================================

class TestAverageDeviation:
    def test_within_default_threshold_passes(self):
        # 101 is 1% above mean of SPREAD_HISTORY → well within 30%
        r = make_rule().check(101.0, SPREAD_HISTORY)
        assert r.passed

    def test_exceeds_default_threshold_fails(self):
        # 200 is 100% above mean of 100 → exceeds 30%
        r = make_rule().check(200.0, SPREAD_HISTORY)
        assert not r.passed

    def test_below_threshold_fails(self):
        # 50 is 50% below mean → also fails
        r = make_rule().check(50.0, SPREAD_HISTORY)
        assert not r.passed

    def test_custom_threshold_respected(self):
        # Use wide spread (σ≈28) so sigma check doesn't fire; test only the pct threshold.
        # mean=100, 115 is 15% above — passes at 20% threshold, fails at 10%.
        wide_hist = [60.0, 80.0, 100.0, 120.0, 140.0]  # mean=100, σ≈31.6
        r_pass = make_rule().check(115.0, wide_hist, max_deviation_percent=20.0)
        r_fail = make_rule().check(115.0, wide_hist, max_deviation_percent=10.0)
        assert r_pass.passed, f"Expected pass: {r_pass.reason}"
        assert not r_fail.passed

    def test_exact_threshold_boundary(self):
        # With wide spread (σ≈31.6) 130 is 1.0σ — well within 2σ.
        # deviation_percent == 30.0 is NOT > 30.0, so the rule should pass.
        wide_hist = [60.0, 80.0, 100.0, 120.0, 140.0]
        r = make_rule().check(130.0, wide_hist, max_deviation_percent=30.0)
        assert r.passed, f"Expected pass at exact boundary: {r.reason}"

    def test_deviation_percent_in_details(self):
        r = make_rule().check(115.0, SPREAD_HISTORY)
        assert "deviation_percent" in r.details
        assert r.details["deviation_percent"] == pytest.approx(15.0, abs=0.1)


# ===========================================================================
# 5. Statistical outlier detection
# ===========================================================================

class TestStatisticalOutlier:
    def test_tight_cluster_outlier_detected(self):
        # TIGHT_HISTORY has σ≈0.63; price 160 is ~95σ away
        r = make_rule().check(160.0, TIGHT_HISTORY, max_deviation_percent=100)
        assert not r.passed
        assert "outlier" in r.reason.lower() or "σ" in r.reason

    def test_within_sigma_passes(self):
        # 101 is ~0.13σ from mean of SPREAD_HISTORY → within default 2σ
        r = make_rule().check(101.0, SPREAD_HISTORY)
        assert r.passed

    def test_custom_sigma_threshold(self):
        # Use tight data; 101 should fail at 0.5σ but pass at 5.0σ
        r_fail = make_rule().check(101.0, TIGHT_HISTORY,
                                   max_deviation_percent=100, outlier_sigma=0.5)
        r_pass = make_rule().check(101.0, TIGHT_HISTORY,
                                   max_deviation_percent=100, outlier_sigma=5.0)
        assert not r_fail.passed
        assert r_pass.passed

    def test_sigma_distance_in_details(self):
        r = make_rule().check(160.0, TIGHT_HISTORY, max_deviation_percent=100)
        assert "sigma_distance" in r.details
        assert r.details["sigma_distance"] is not None
        assert r.details["sigma_distance"] > 2.0

    def test_single_data_point_skips_sigma(self):
        # With only one data point std dev is undefined; should not crash
        r = make_rule().check(150.0, [100.0], max_deviation_percent=100)
        assert r.details["sigma_distance"] is None


# ===========================================================================
# 6. Trend analysis
# ===========================================================================

class TestTrendAnalysis:
    def test_upward_trend_continuation_fails(self):
        # Monotone increase over 3 recent points, new quote continues upward
        r = make_rule().check(145.0, [100.0, 110.0, 120.0, 130.0], trend_window=3)
        assert not r.passed
        assert "trend" in r.reason.lower()

    def test_flat_history_no_trend_issue(self):
        # No trend in flat data
        r = make_rule().check(101.0, SPREAD_HISTORY, trend_window=3)
        assert r.passed  # no trend issue (deviation also fine)

    def test_declining_trend_no_false_positive(self):
        # Prices falling; new quote also falls — no trend warning expected.
        # Use wide spread so 85 stays within 2σ of mean (~95).
        r = make_rule().check(85.0, [60.0, 80.0, 100.0, 120.0, 110.0, 105.0, 100.0, 95.0],
                              trend_window=3)
        assert r.passed, f"Expected pass for declining trend: {r.reason}"

    def test_insufficient_data_for_trend_skipped(self):
        # Only 2 data points; trend_window=3 → trend skipped, should not error
        r = make_rule().check(101.0, [99.0, 101.0], trend_window=3)
        assert r.details["trend"]["analyzed"] is False

    def test_trend_info_in_details(self):
        r = make_rule().check(145.0, [100.0, 110.0, 120.0, 130.0], trend_window=3)
        assert "trend" in r.details
        assert r.details["trend"]["recent_upward_trend"] is True

    def test_custom_trend_window(self):
        # Only last 2 points are monotone increasing; window=2 should catch it
        r = make_rule().check(145.0, [80.0, 60.0, 120.0, 130.0], trend_window=2)
        assert not r.passed

    def test_new_price_slightly_below_last_no_trend_flag(self):
        # Upward trend in history, but new quote is below the last price
        r = make_rule().check(125.0, [100.0, 110.0, 120.0, 130.0], trend_window=3)
        # price is BELOW last (130), so trend flag should not fire
        assert "trend" not in r.reason.lower() or r.passed


# ===========================================================================
# 7. Competitor benchmark
# ===========================================================================

class TestCompetitorBenchmark:
    def test_above_benchmark_fails(self):
        # This supplier quotes 180; peers are around 100 → 80% above median
        r = make_rule().check(
            180.0, SPREAD_HISTORY,
            competitor_prices=[98.0, 100.0, 105.0],
            benchmark_deviation_percent=20.0,
        )
        assert not r.passed
        assert "competitor" in r.reason.lower()

    def test_within_benchmark_passes(self):
        r = make_rule().check(
            105.0, SPREAD_HISTORY,
            competitor_prices=[98.0, 100.0, 105.0],
            benchmark_deviation_percent=20.0,
        )
        assert r.passed

    def test_below_competitor_median_passes(self):
        # Being cheaper than peers should never fail the benchmark
        r = make_rule().check(
            90.0, SPREAD_HISTORY,
            competitor_prices=[98.0, 100.0, 105.0],
        )
        assert r.passed

    def test_benchmark_details_populated(self):
        r = make_rule().check(
            180.0, SPREAD_HISTORY,
            competitor_prices=[98.0, 100.0, 105.0],
            benchmark_deviation_percent=20.0,
        )
        b = r.details["benchmark"]
        assert b["competitor_count"] == 3
        assert b["competitor_median"] == pytest.approx(100.0, abs=0.1)
        assert b["deviation_from_median_pct"] == pytest.approx(80.0, abs=0.5)

    def test_no_competitor_prices_no_benchmark(self):
        r = make_rule().check(101.0, SPREAD_HISTORY, competitor_prices=[])
        assert r.passed
        assert "benchmark" not in r.details

    def test_custom_benchmark_threshold(self):
        # Use wide, non-monotone history so sigma/trend checks don't interfere.
        # 115 is 15% above competitor median 100.
        neutral_hist = [60.0, 80.0, 100.0, 120.0, 140.0]
        r_pass = make_rule().check(115.0, neutral_hist,
                                   competitor_prices=[100.0],
                                   benchmark_deviation_percent=20.0)
        r_fail = make_rule().check(115.0, neutral_hist,
                                   competitor_prices=[100.0],
                                   benchmark_deviation_percent=10.0)
        assert r_pass.passed, f"Expected pass: {r_pass.reason}"
        assert not r_fail.passed


# ===========================================================================
# 8. Provenance / facts_used
# ===========================================================================

class TestProvenance:
    def test_current_price_in_facts(self):
        r = make_rule().check(100.0, SPREAD_HISTORY)
        assert any("current_price" in f for f in r.facts_used)

    def test_part_id_recorded(self):
        r = make_rule().check(100.0, SPREAD_HISTORY, part_id="P-12345")
        assert "part:P-12345" in r.facts_used

    def test_supplier_id_recorded(self):
        r = make_rule().check(100.0, SPREAD_HISTORY, supplier_id="SUP-001")
        assert "supplier:SUP-001" in r.facts_used

    def test_historical_count_recorded(self):
        r = make_rule().check(100.0, SPREAD_HISTORY)
        assert any("historical_data_points:5" in f for f in r.facts_used)

    def test_facts_independent_across_calls(self):
        """facts_used from one call must not bleed into the next."""
        rule = make_rule()
        rule.check(100.0, SPREAD_HISTORY, part_id="P-AAA")
        r2 = rule.check(100.0, SPREAD_HISTORY, part_id="P-BBB")
        assert "part:P-AAA" not in r2.facts_used
        assert "part:P-BBB" in r2.facts_used


# ===========================================================================
# 9. Confidence scoring
# ===========================================================================

class TestConfidence:
    def test_no_history_confidence_is_0_5(self):
        assert make_rule().check(100.0, []).confidence == 0.5

    def test_passing_confidence_scales_with_data_volume(self):
        small_hist = [90.0, 110.0]
        large_hist = [90.0 + i for i in range(20)]   # 20 data points
        r_small = make_rule().check(100.0, small_hist)
        r_large = make_rule().check(statistics_mean(large_hist), large_hist)
        assert r_large.confidence >= r_small.confidence

    def test_multiple_failures_reduce_confidence(self):
        # Single failure vs multiple simultaneous failures
        r_one = make_rule().check(140.0, SPREAD_HISTORY)   # one issue (deviation)
        r_many = make_rule().check(300.0, TIGHT_HISTORY,
                                   competitor_prices=[95.0, 100.0, 105.0])
        assert r_many.confidence <= r_one.confidence

    def test_passing_confidence_capped_at_0_95(self):
        # Even with huge history, confidence should not exceed 0.95
        big_hist = [100.0] * 100
        r = make_rule().check(100.0, big_hist)
        assert r.confidence <= 0.95


# ===========================================================================
# 10. Multiple simultaneous failures
# ===========================================================================

class TestMultipleFailures:
    def test_all_issues_reported_in_one_result(self):
        """Rule should collect all failures, not stop at the first."""
        r = make_rule().check(
            300.0,
            TIGHT_HISTORY,                            # triggers outlier + deviation
            competitor_prices=[95.0, 100.0, 105.0],  # triggers benchmark
        )
        assert not r.passed
        issue_count = r.reason.count(";") + 1
        assert issue_count >= 2

    def test_details_contains_all_sub_check_data(self):
        r = make_rule().check(
            300.0, TIGHT_HISTORY,
            competitor_prices=[95.0, 100.0, 105.0],
        )
        assert "deviation_percent" in r.details
        assert "benchmark" in r.details


# ===========================================================================
# 11. Real-world supply chain scenarios (data from sample JSON)
# ===========================================================================

class TestRealWorldScenarios:
    """Scenarios grounded in the project's sample data."""

    # P-12345 Servo Motor SM-400: SUP-001 @ $285.50, SUP-002 @ $245.00
    def test_servo_motor_new_quote_reasonable(self):
        historical = [285.50, 285.50, 290.00, 280.00]   # SUP-001 history
        r = make_rule().check(287.00, historical, part_id="P-12345", supplier_id="SUP-001")
        assert r.passed

    def test_servo_motor_price_spike_detected(self):
        historical = [285.50, 287.00, 286.00]
        r = make_rule().check(
            420.00, historical,
            part_id="P-12345",
            competitor_prices=[285.50, 245.00],   # two current suppliers
            benchmark_deviation_percent=20.0,
        )
        assert not r.passed

    # P-33333 Controller Board CB-2000: $750 — single-source (SUP-003)
    def test_controller_board_single_source_no_benchmark(self):
        r = make_rule().check(
            750.00, [745.00, 755.00, 750.00],
            part_id="P-33333", supplier_id="SUP-003",
        )
        assert r.passed

    def test_controller_board_first_ever_quote(self):
        # No history yet for a new part
        r = make_rule().check(750.00, [], part_id="P-33333")
        assert r.passed
        assert r.confidence == 0.5

    # P-11111 Mounting Bracket MB-100: SUP-003 @ $45, SUP-004 @ $42
    def test_mounting_bracket_cheaper_supplier_passes(self):
        # SUP-004 quotes $42 vs SUP-003's historical $45 range.
        # Use wider history so the cheaper price doesn't trip the sigma check.
        wide_hist = [35.0, 40.0, 45.0, 50.0, 55.0]  # mean=45, σ≈7.9
        r = make_rule().check(
            42.00, wide_hist,
            part_id="P-11111", supplier_id="SUP-004",
            competitor_prices=[45.00],
        )
        assert r.passed, f"Expected pass for cheaper supplier: {r.reason}"


# ---------------------------------------------------------------------------
# stdlib helper (avoids importing numpy/statistics just for the test)
# ---------------------------------------------------------------------------

def statistics_mean(values):
    return sum(values) / len(values)