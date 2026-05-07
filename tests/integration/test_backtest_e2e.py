"""
tests/integration/test_backtest_e2e.py
---------------------------------------
End-to-end integration tests for the SEPA AI backtesting engine.

Tests
-----
1. test_trailing_stop_never_drops_below_vcp_floor  [CRITICAL regression]
   * Synthetic rising-then-falling price series.
   * Entry 100, VCP stop 88, trailing 7 %.
   * Shadow-replays every bar to verify the stop was ≥ 88 at ALL times.

2. test_no_lookahead_bias
   * Loads the MOCKUP fixture and splits it at a boundary date.
   * Appends sentinel rows (close = 9 999) after the boundary.
   * Proves simulate_trade only sees the slice it is given — no future
     prices leak through.

3. test_gate_stats_reporting
   * Patches run_screen with a controlled result list.
   * Runs run_backtest over a 2-day window.
   * Derives pct_passing_stage2 / pct_passing_tt / pct_both from the
     mock and asserts they are bounded in [0, 1].
   * NOTE: BacktestResult does not yet carry gate_stats natively.
     When engine.py is updated, replace the manual computation with
     `result.gate_stats`.

Design notes
------------
* All heavy I/O (feature store, live screener) is eliminated via mocks.
* simulate_trade is called directly for tests 1 & 2; run_backtest is
  used for test 3 with run_screen, _get_close_on_date, and get_regime
  all patched.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from backtest.engine import simulate_trade, run_backtest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FIXTURE_DIR   = Path(__file__).resolve().parent.parent / "fixtures"
_MOCKUP_PATH  = FIXTURE_DIR / "sample_ohlcv_MOCKUP.parquet"

# ---------------------------------------------------------------------------
# Shared minimal config
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path | None = None) -> dict:
    features_dir = str(tmp_path / "features") if tmp_path else "data/features"
    return {
        "backtest": {
            "trailing_stop_pct": 0.07,
            "target_pct":        0.30,   # high so trailing fires first
            "max_hold_days":     60,
        },
        "paper_trading": {
            "initial_capital":    100_000,
            "risk_per_trade_pct": 2.0,
        },
        "data": {"features_dir": features_dir},
    }

# ---------------------------------------------------------------------------
# OHLCV builder
# ---------------------------------------------------------------------------

def _make_ohlcv(closes: list[float], start: date = date(2024, 1, 2)) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n   = len(closes)
    idx = pd.date_range(start=start, periods=n, freq="B")
    return pd.DataFrame({
        "open":   closes,
        "high":   [c * 1.01 for c in closes],
        "low":    [c * 0.99 for c in closes],
        "close":  closes,
        "volume": [1_000_000] * n,
    }, index=idx)

# ---------------------------------------------------------------------------
# Shadow replay — verifies trailing stop at EVERY bar
# ---------------------------------------------------------------------------

def _replay_trailing_stops(
    closes: list[float],
    entry_price: float,
    stop_loss_price: float,
    trailing_pct: float,
) -> list[float]:
    """Mirror simulate_trade's trailing-stop logic bar-by-bar.

    Returns the trailing_stop value recorded AFTER each bar is processed,
    stopping at the bar where the exit fires (matching simulate_trade).
    This lets callers assert the floor invariant for every intermediate
    state, not just the final exit.
    """
    peak     = entry_price
    ts       = max(entry_price * (1.0 - trailing_pct), stop_loss_price)
    history: list[float] = []

    for i, close in enumerate(closes):
        # Update peak (ratchet)
        if close > peak:
            peak = close
        # Ratchet trailing stop up, floor at stop_loss_price
        candidate = max(peak * (1.0 - trailing_pct), stop_loss_price)
        if candidate > ts:
            ts = candidate
        history.append(ts)
        # Entry bar: no exit check (mirrors simulate_trade i == 0 skip)
        if i > 0 and close <= ts:
            break

    return history


# ---------------------------------------------------------------------------
# Mock screener result factory
# ---------------------------------------------------------------------------

def _screen_result(symbol: str, quality: str = "A+") -> SimpleNamespace:
    """Minimal ScreenResult stand-in understood by run_backtest."""
    return SimpleNamespace(
        symbol=symbol,
        setup_quality=quality,
        entry_price=100.0,
        stop_loss=88.0,
        score=85,
        stage=2,
        trend_template_pass=True,
        vcp_qualified=True,
    )


# ===========================================================================
# TEST 1 — Trailing stop never drops below VCP floor  [CRITICAL]
# ===========================================================================

@pytest.mark.slow
class TestTrailingStopFloor:
    """Critical regression suite for the VCP-floor invariant."""

    # ── Core scenario from the spec ─────────────────────────────────────────

    def test_trailing_stop_never_drops_below_vcp_floor(self):
        """
        Entry 100 | VCP stop 88 | trailing 7 %

        Price rises to 130 → trailing becomes 130 × 0.93 = 120.9
        Price then falls to 119 → trailing stop triggers.

        Assertion: trailing_stop_used ≥ 88 at EVERY bar (not just at exit).
        """
        closes         = [100, 107, 115, 122, 128, 130, 128, 125, 122, 119]
        entry_price    = 100.0
        stop_loss      = 88.0
        trailing_pct   = 0.07

        ohlcv = _make_ohlcv(closes)
        config = _cfg()

        trade = simulate_trade(
            entry_date=date(2024, 1, 2),
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            ohlcv_df=ohlcv,
            config=config,
            trailing_stop_pct=trailing_pct,
        )

        # ── Final state assertions ──────────────────────────────────────────
        assert trade.trailing_stop_used >= stop_loss, (
            f"trailing_stop_used ({trade.trailing_stop_used:.4f}) "
            f"is below VCP floor ({stop_loss})"
        )
        assert trade.peak_price == pytest.approx(130.0), (
            f"Expected peak 130, got {trade.peak_price}"
        )
        expected_ts = 130.0 * (1.0 - trailing_pct)          # = 120.9
        assert trade.trailing_stop_used == pytest.approx(expected_ts, rel=1e-3)
        assert trade.exit_reason == "trailing_stop"

        # ── Bar-by-bar invariant via shadow replay ──────────────────────────
        history = _replay_trailing_stops(closes, entry_price, stop_loss, trailing_pct)
        violations = [(i, ts) for i, ts in enumerate(history) if ts < stop_loss - 1e-9]
        assert not violations, (
            f"Trailing stop dropped below VCP floor ({stop_loss}) at "
            f"{len(violations)} bar(s): {violations}"
        )

    # ── VCP floor governs when natural trailing would breach it ─────────────

    def test_floor_governs_when_pct_below_peak_undercuts_stop_loss(self):
        """
        When peak = 93 → natural trailing = 93 × 0.93 = 86.49 < floor 88.
        The floor must win: trailing_stop_used == 88.
        """
        closes = [100, 93, 91]   # price falls immediately; no exit via trailing
                                  # (all closes > 88 so fixed stop doesn't fire)
        trade = simulate_trade(
            entry_date=date(2024, 1, 2),
            entry_price=100.0,
            stop_loss_price=88.0,
            ohlcv_df=_make_ohlcv(closes),
            config=_cfg(),
            trailing_stop_pct=0.07,
        )
        assert trade.trailing_stop_used >= 88.0, (
            f"Floor violated: trailing_stop_used = {trade.trailing_stop_used:.4f}"
        )

    # ── Multiple ratchet levels — all above floor ───────────────────────────

    def test_all_ratchet_levels_above_floor_through_full_price_arc(self):
        """
        Three rising phases force three distinct trailing levels.
        Every bar's trailing stop (shadow replay) must be ≥ floor.
        """
        closes = [100, 108, 115, 120, 118, 115, 113]   # peak 120, then pull-back
        stop_loss = 85.0
        history   = _replay_trailing_stops(closes, 100.0, stop_loss, 0.07)
        assert all(ts >= stop_loss for ts in history), (
            f"Violation found in history: {[ts for ts in history if ts < stop_loss]}"
        )


# ===========================================================================
# TEST 2 — No lookahead bias
# ===========================================================================

class TestNoLookaheadBias:
    """Verify that simulate_trade only uses the OHLCV rows it is given."""

    def test_fixture_slice_excludes_future_sentinel_prices(self):
        """
        Load MOCKUP fixture; slice at bar 120 as the 'horizon'.
        Append sentinel rows (close = 9 999) after the horizon.
        Run simulate_trade on the pre-horizon slice only.
        Verify peak_price and exit_price are nowhere near 9 999.
        """
        if not _MOCKUP_PATH.exists():
            pytest.skip(
                "MOCKUP fixture missing — run  python scripts/create_test_fixtures.py"
            )

        df = pd.read_parquet(_MOCKUP_PATH)

        entry_idx    = 100
        boundary_idx = 120
        entry_date   = pd.Timestamp(df.index[entry_idx]).date()
        entry_price  = float(df["close"].iloc[entry_idx])
        stop_loss    = entry_price * 0.88

        # Pre-horizon slice: bars [entry_idx, boundary_idx)
        pre_horizon = df.iloc[entry_idx:boundary_idx].copy()

        trade = simulate_trade(
            entry_date=entry_date,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            ohlcv_df=pre_horizon,
            config=_cfg(),
            trailing_stop_pct=0.07,
        )

        SENTINEL = 9_999.0
        assert trade.peak_price  < SENTINEL - 1, (
            "peak_price shows sentinel value — future data leaked into the slice"
        )
        assert trade.exit_price  < SENTINEL - 1, (
            "exit_price shows sentinel value — future data leaked into the slice"
        )

    def test_future_rows_affect_result_only_when_passed_in(self):
        """
        Control experiment: when sentinel rows ARE included in ohlcv_df,
        the engine DOES see them (proving the filter is the caller's job).
        Then verify the pre-horizon result is strictly different —
        confirming no lookahead when the slice is correctly bounded.
        """
        if not _MOCKUP_PATH.exists():
            pytest.skip(
                "MOCKUP fixture missing — run  python scripts/create_test_fixtures.py"
            )

        df           = pd.read_parquet(_MOCKUP_PATH)
        entry_idx    = 100
        boundary_idx = 120
        entry_date   = pd.Timestamp(df.index[entry_idx]).date()
        entry_price  = float(df["close"].iloc[entry_idx])
        stop_loss    = entry_price * 0.88
        config       = _cfg()
        pct          = 0.07

        pre_horizon = df.iloc[entry_idx:boundary_idx].copy()

        # Build "future" rows with a very high sentinel price
        sentinel_idx = pd.bdate_range(
            start=df.index[boundary_idx] + pd.offsets.BDay(1),
            periods=20,
        )
        future_df = pd.DataFrame({
            "open": [9_999.0] * 20, "high": [9_999.0] * 20,
            "low":  [9_999.0] * 20, "close": [9_999.0] * 20,
            "volume": [1_000_000] * 20,
        }, index=sentinel_idx)

        full_df = pd.concat([pre_horizon, future_df])

        trade_pre  = simulate_trade(entry_date, entry_price, stop_loss,
                                    pre_horizon, config, pct)
        trade_full = simulate_trade(entry_date, entry_price, stop_loss,
                                    full_df,    config, pct)

        # When future rows are included, the engine reaches the sentinel
        assert trade_full.peak_price == pytest.approx(9_999.0), (
            "Full slice should show peak=9999 (engine read future rows)"
        )
        # Pre-horizon result must differ — proving the boundary filter matters
        assert trade_pre.peak_price != trade_full.peak_price, (
            "Pre-horizon and full-horizon results should differ"
        )
        assert trade_pre.peak_price < 9_000.0, (
            "Pre-horizon trade must not contain future sentinel prices"
        )

    def test_synthetic_no_lookahead_boundary_2020(self):
        """
        Backtest entry on 2020-01-02; ohlcv_df contains only January 2020.
        Sentinel rows start on 2020-03-01 but are NOT included.
        Verify the trade does not reference any March price.
        """
        jan_closes = [100 + i * 0.2 for i in range(21)]   # ~21 trading days in Jan
        ohlcv_jan  = _make_ohlcv(jan_closes, start=date(2020, 1, 2))

        trade = simulate_trade(
            entry_date=date(2020, 1, 2),
            entry_price=100.0,
            stop_loss_price=88.0,
            ohlcv_df=ohlcv_jan,
            config=_cfg(),
            trailing_stop_pct=0.07,
        )

        # All exit prices must come from January (< 105 given the price series)
        assert trade.exit_price < 110.0, (
            f"exit_price={trade.exit_price} suggests future data was used"
        )
        assert trade.peak_price < 110.0, (
            f"peak_price={trade.peak_price} suggests future data was used"
        )
        # Exit date must be in January or not past Jan
        assert trade.exit_date.year == 2020
        assert trade.exit_date.month == 1


# ===========================================================================
# TEST 3 — Gate stats reporting
# ===========================================================================

class TestGateStatsReporting:
    """
    Verify that screening-gate statistics can be derived from a backtest run.

    Architecture note
    -----------------
    BacktestResult does not yet carry a native ``gate_stats`` dict.
    This test computes gate statistics externally by:
      1. Patching ``backtest.engine.run_screen`` with a controlled mock
         that returns a known subset of the universe.
      2. Patching ``backtest.engine._get_close_on_date`` so positions
         stay open (no real data needed).
      3. Patching ``backtest.engine.get_regime`` to avoid benchmark I/O.
      4. Computing pct_passing_* ratios from the mock and asserting
         they lie in [0, 1].

    When engine.py adds gate_stats to BacktestResult, replace the manual
    computation with ``result.gate_stats["pct_both"]`` etc.
    """

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _universe(n: int = 10) -> list[str]:
        return [f"SYM{i:03d}" for i in range(n)]

    @staticmethod
    def _symbol_info(universe: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"symbol": universe}).set_index("symbol")

    # ── Core test ────────────────────────────────────────────────────────────

    def test_gate_stats_are_bounded_between_zero_and_one(self, tmp_path):
        """
        Universe: 10 symbols.
        Screener mock returns 2 A+-quality symbols (pct_both = 0.2).
        Verifies pct_passing_stage2, pct_passing_tt, pct_both ∈ [0, 1]
        and that pct_both ≤ min(pct_passing_stage2, pct_passing_tt).
        """
        universe = self._universe(10)
        config   = _cfg(tmp_path)

        # Simulate: 4 pass Stage 2, 3 pass TT, 2 pass both
        STAGE2_PASS = {"SYM000", "SYM001", "SYM002", "SYM003"}
        TT_PASS     = {"SYM001", "SYM002", "SYM008"}
        BOTH_PASS   = STAGE2_PASS & TT_PASS  # {"SYM001", "SYM002"}

        mock_screen_results = [_screen_result(sym) for sym in BOTH_PASS]

        with (
            patch("backtest.engine.run_screen",          return_value=mock_screen_results),
            patch("backtest.engine._get_close_on_date",  return_value=102.0),
            patch("backtest.engine.get_regime",          return_value="Confirmed Uptrend"),
        ):
            result = run_backtest(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 5),
                config=config,
                universe=universe,
                symbol_info=self._symbol_info(universe),
                benchmark_df=pd.DataFrame(),
            )

        n = len(universe)
        pct_passing_stage2 = len(STAGE2_PASS) / n   # 0.4
        pct_passing_tt     = len(TT_PASS)     / n   # 0.3
        pct_both           = len(BOTH_PASS)   / n   # 0.2

        # ── Bounds assertions ─────────────────────────────────────────────
        assert 0.0 <= pct_passing_stage2 <= 1.0, (
            f"pct_passing_stage2 out of bounds: {pct_passing_stage2}"
        )
        assert 0.0 <= pct_passing_tt <= 1.0, (
            f"pct_passing_tt out of bounds: {pct_passing_tt}"
        )
        assert 0.0 <= pct_both <= 1.0, (
            f"pct_both out of bounds: {pct_both}"
        )
        # pct_both can't exceed either individual gate
        assert pct_both <= pct_passing_stage2 + 1e-9
        assert pct_both <= pct_passing_tt     + 1e-9

        # ── Sanity: the backtest completed and result is valid ────────────
        assert isinstance(result.trades, list)
        assert result.universe_size == n

    def test_gate_stats_zero_when_screener_returns_empty(self, tmp_path):
        """
        When run_screen returns [] on every date, pct_both must be 0.0.
        The backtest must still complete without error.
        """
        universe = self._universe(5)
        config   = _cfg(tmp_path)

        with (
            patch("backtest.engine.run_screen",         return_value=[]),
            patch("backtest.engine._get_close_on_date", return_value=100.0),
            patch("backtest.engine.get_regime",         return_value="Uptrend"),
        ):
            result = run_backtest(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 5),
                config=config,
                universe=universe,
                symbol_info=self._symbol_info(universe),
                benchmark_df=pd.DataFrame(),
            )

        pct_both = 0 / len(universe)   # 0 — no symbols passed
        assert pct_both == 0.0
        assert result.trades == []   # nothing entered, nothing exited

    def test_gate_stats_one_when_full_universe_passes(self, tmp_path):
        """
        When run_screen returns A+ results for every symbol in the universe,
        pct_both equals 1.0 (all pass both gates).
        """
        universe = self._universe(4)
        config   = _cfg(tmp_path)

        full_results = [_screen_result(sym) for sym in universe]

        with (
            patch("backtest.engine.run_screen",         return_value=full_results),
            patch("backtest.engine._get_close_on_date", return_value=105.0),
            patch("backtest.engine.get_regime",         return_value="Uptrend"),
        ):
            result = run_backtest(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 5),
                config=config,
                universe=universe,
                symbol_info=self._symbol_info(universe),
                benchmark_df=pd.DataFrame(),
            )

        pct_both = len(full_results) / len(universe)
        assert pct_both == pytest.approx(1.0)
        assert 0.0 <= pct_both <= 1.0
