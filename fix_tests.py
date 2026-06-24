"""
fix_tests.py — run from project root to fix all CI test failures.

Addresses:
  1. auth.py: add validate_and_consume_refresh_token alias
  2. auth.py: add refresh_expire_days to _TestSettings in test_auth.py
  3. supply_chain_rules.py: add db kwarg to PartCompatibilityRule.check()
  4. supply_chain_rules.py: add outlier_sigma, benchmark_deviation_percent,
     part_id, supplier_id, sigma_distance, trend{} to PriceReasonablenessRule
  5. tests/unit/test_api.py: replace X-API-Key header with Bearer JWT
"""

import re
from pathlib import Path

# ── 1. auth.py — add validate_and_consume_refresh_token alias ────────────────
auth_path = Path("src/api/auth.py")
auth = auth_path.read_text()

ALIAS = '''

def validate_and_consume_refresh_token(jti: str):
    """Module-level alias for test patching — delegates to TokenStore."""
    from src.api.token_store import TokenStore
    return TokenStore().validate_and_consume_refresh_token(jti)

'''

if "validate_and_consume_refresh_token" not in auth:
    auth = auth.replace('\n@router.post("/token"', ALIAS + '\n@router.post("/token"')
    auth_path.write_text(auth)
    print("✓ 1. auth.py: added validate_and_consume_refresh_token alias")
else:
    print("  1. auth.py: alias already present")

# ── 2. test_auth.py — add refresh_expire_days to _TestSettings ───────────────
ta_path = Path("tests/unit/test_auth.py")
ta = ta_path.read_text()

if "refresh_expire_days" not in ta:
    # Find _TestSettings class and add the field
    ta = re.sub(
        r'(class _TestSettings:.*?)(anthropic_api_key\s*=)',
        lambda m: m.group(1) + "    refresh_expire_days        = 7\n    " + m.group(2).lstrip(),
        ta, flags=re.DOTALL, count=1
    )
    ta_path.write_text(ta)
    print("✓ 2. test_auth.py: added refresh_expire_days to _TestSettings")
else:
    print("  2. test_auth.py: refresh_expire_days already present")

# ── 3. supply_chain_rules.py — add db kwarg to PartCompatibilityRule.check() ─
sc_path = Path("src/reasoning/supply_chain_rules.py")
sc = sc_path.read_text()

old_compat_sig = (
    "    def check(\n"
    "        self,\n"
    "        original_part: Dict[str, Any],\n"
    "        substitute_part: Dict[str, Any]\n"
    "    ) -> RuleResult:"
)
new_compat_sig = (
    "    def check(\n"
    "        self,\n"
    "        original_part: Dict[str, Any],\n"
    "        substitute_part: Dict[str, Any],\n"
    "        db=None,\n"
    "    ) -> RuleResult:"
)

if old_compat_sig in sc:
    sc = sc.replace(old_compat_sig, new_compat_sig, 1)
    print("✓ 3. supply_chain_rules.py: added db kwarg to PartCompatibilityRule.check()")
else:
    print("  3. PartCompatibilityRule.check() signature not matched — check manually")

# ── 4. PriceReasonablenessRule — full replacement with all expected params ────
OLD_PRICE_CHECK = re.search(
    r'(    def check\(\n        self,\n        current_price: float,\n'
    r'        historical_prices: list,.*?)'
    r'(?=\nclass |\Z)',
    sc, re.DOTALL
)

NEW_PRICE_CHECK = '''    def check(
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
            self.facts_used.append(f"part_id:{part_id}")
        if supplier_id:
            self.facts_used.append(f"supplier_id:{supplier_id}")
        if historical_prices:
            self.facts_used.append(
                f"historical_data_points:{len(historical_prices)}"
            )

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
            variance = sum((p - mean) ** 2 for p in historical_prices) / len(
                historical_prices
            )
            std_dev = math.sqrt(variance)
            if std_dev > 0:
                sigma_distance = abs(current_price - mean) / std_dev
                threshold = outlier_sigma if outlier_sigma is not None else 2.0
                if sigma_distance > threshold:
                    failures.append(
                        f"Statistical outlier: {sigma_distance:.1f}σ "
                        f"from mean (threshold {threshold}σ)"
                    )
        details["sigma_distance"] = sigma_distance

        # ── Trend analysis ───────────────────────────────────────────────────
        trend_info: dict = {"analyzed": False}
        if trend_window is not None:
            if len(historical_prices) >= trend_window:
                window = historical_prices[-trend_window:]
                trend_avg = sum(window) / len(window)
                trend_deviation = (current_price - trend_avg) / trend_avg * 100
                trend_info = {
                    "analyzed": True,
                    "window": trend_window,
                    "trend_average": trend_avg,
                    "trend_deviation_percent": trend_deviation,
                }
                details["trend_average"] = trend_avg
                details["trend_deviation_percent"] = trend_deviation
                if abs(trend_deviation) > max_deviation_percent:
                    failures.append(
                        f"Trend deviation {trend_deviation:.1f}% "
                        f"exceeds {max_deviation_percent}%"
                    )
            else:
                trend_info = {"analyzed": False, "reason": "insufficient_data"}
        details["trend"] = trend_info

        # ── Competitor benchmark ─────────────────────────────────────────────
        if competitor_prices:
            sorted_c = sorted(competitor_prices)
            median_idx = len(sorted_c) // 2
            competitor_median = sorted_c[median_idx]
            bench_dev = (current_price - competitor_median) / competitor_median * 100
            bench_limit = (
                benchmark_deviation_percent
                if benchmark_deviation_percent is not None
                else max_deviation_percent
            )
            details["benchmark"] = {
                "competitor_median": competitor_median,
                "benchmark_deviation_percent": bench_dev,
                "limit": bench_limit,
            }
            details["competitor_median"] = competitor_median
            details["benchmark_deviation_percent"] = bench_dev
            if bench_dev > bench_limit:
                failures.append(
                    f"Price {bench_dev:.1f}% above competitor median "
                    f"(limit {bench_limit}%)"
                )

        # ── Standard deviation check ─────────────────────────────────────────
        if deviation_percent > max_deviation_percent:
            failures.append(
                f"Price deviation {deviation_percent:.1f}% "
                f"exceeds maximum {max_deviation_percent}%"
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
            reason=(
                f"Price within acceptable range "
                f"(deviation: {deviation_percent:.1f}%)"
            ),
            details=details,
            confidence=0.9,
        )
'''

if OLD_PRICE_CHECK:
    sc = sc[: OLD_PRICE_CHECK.start()] + NEW_PRICE_CHECK + sc[OLD_PRICE_CHECK.end():]
    print("✓ 4. supply_chain_rules.py: PriceReasonablenessRule fully updated")
else:
    print("  4. PriceReasonablenessRule.check() not matched — may need manual update")

# Ensure Optional and math imports
if "from typing import" in sc and "Optional" not in sc:
    sc = sc.replace(
        "from typing import Dict, Any",
        "from typing import Dict, Any, Optional",
    )
elif "Optional" not in sc:
    sc = "from typing import Optional\n" + sc

sc_path.write_text(sc)

# ── 5. test_api.py — replace X-API-Key with Bearer JWT ───────────────────────
api_test_path = Path("tests/unit/test_api.py")
if api_test_path.exists():
    api_test = api_test_path.read_text()
    if 'X-API-Key' in api_test and 'Authorization' not in api_test:
        api_test = api_test.replace(
            'HEADERS = {"X-API-Key": "dev-api-key"}',
            '''import jwt as _jwt
_TEST_TOKEN = _jwt.encode(
    {"sub": "ci-client", "type": "access"},
    "ci-test-jwt-secret-not-for-production",
    algorithm="HS256",
)
HEADERS = {"Authorization": f"Bearer {_TEST_TOKEN}"}'''
        )
        api_test_path.write_text(api_test)
        print("✓ 5. test_api.py: replaced X-API-Key with Bearer JWT")
    else:
        print("  5. test_api.py: already uses Bearer JWT")
else:
    print("  5. test_api.py not found")

print("\n✅ Done. Run: pytest tests/ -m 'not db' -q")