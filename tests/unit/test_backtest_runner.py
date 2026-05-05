"""
tests/unit/test_backtest_runner.py
------------------------------------
Unit tests for scripts/backtest_runner.py — run_parameter_sweep.

Tests
-----
1. run_parameter_sweep with 2 trailing_pcts → DataFrame with exactly 2 rows.
2. Returned DataFrame contains all required metric columns.
3. trailing_stop_pct column matches the input list in order.
4. _print_sweep_table emits a non-empty string to stdout.

Design
------
_run_single is patched via unittest.mock.patch.object on the imported
module to avoid any real backtest I/O.  The fake_run_single stub returns
zero-trade metrics so the DataFrame shape/column assertions are independent
of actual market data.

Import strategy
---------------
scripts/ is made a package by scripts/__init__.py.  The test imports
run_parameter_sweep and _run_single directly from scripts.backtest_runner.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# ── Ensure project root is on sys.path (editable install covers packages;
#    scripts/ needs the extra push when pytest runs from a subprocess).
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.backtest_runner as _runner  # noqa: E402

run_parameter_sweep = _runner.run_parameter_sweep
_run_single         = _runner._run_single

# ---------------------------------------------------------------------------
# Required columns in the output DataFrame
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "trailing_stop_pct",
    "cagr",
    "sharpe",
    "max_drawdown",
    "win_rate",
    "total_trades",
}

# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

def _make_metrics(**overrides) -> dict:
    """Return a complete metrics dict with sensible defaults."""
    base = {
        "cagr":              0.12,
        "total_return_pct":  14.0,
        "sharpe_ratio":      0.85,
        "max_drawdown_pct":  8.50,
        "win_rate":          0.55,
        "avg_r_multiple":    1.30,
        "profit_factor":     1.80,
        "expectancy":        0.40,
        "total_trades":      20,
        "avg_hold_days":     11.0,
        "best_trade_pct":    18.0,
        "worst_trade_pct":  -7.0,
    }
    base.update(overrides)
    return base


def _fake_run_single(*args, **kwargs):
    """Stub _run_single: returns a minimal BacktestResult + metrics."""
    from backtest.engine import BacktestResult
    result = BacktestResult(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        trades=[],
        universe_size=2,
        config_snapshot={},
    )
    return result, _make_metrics(), []


# ---------------------------------------------------------------------------
# Test 1 — correct number of rows
# ---------------------------------------------------------------------------

class TestRunParameterSweepRows:

    def test_two_trailing_pcts_returns_two_rows(self):
        """run_parameter_sweep([0.05, 0.07]) must return a DataFrame with 2 rows."""
        trailing_pcts = [0.05, 0.07]
        with patch.object(_runner, "_run_single", side_effect=_fake_run_single):
            df = run_parameter_sweep(
                base_config={"backtest": {}, "paper_trading": {"initial_capital": 100_000}},
                start=date(2024, 1, 2),
                end=date(2024, 1, 31),
                universe=["SYM1", "SYM2"],
                trailing_pcts=trailing_pcts,
            )
        assert len(df) == 2, f"Expected 2 rows, got {len(df)}"

    def test_four_trailing_pcts_returns_four_rows(self):
        """Default sweep list [0.05, 0.07, 0.10, 0.15] returns 4 rows."""
        with patch.object(_runner, "_run_single", side_effect=_fake_run_single):
            df = run_parameter_sweep(
                base_config={"backtest": {}, "paper_trading": {"initial_capital": 100_000}},
                start=date(2024, 1, 2),
                end=date(2024, 1, 31),
                universe=["SYM1"],
                trailing_pcts=None,   # triggers default list
            )
        assert len(df) == 4, f"Expected 4 rows (default), got {len(df)}"

    def test_single_trailing_pct_returns_one_row(self):
        """A single-element list produces exactly one row."""
        with patch.object(_runner, "_run_single", side_effect=_fake_run_single):
            df = run_parameter_sweep(
                base_config={"backtest": {}, "paper_trading": {"initial_capital": 100_000}},
                start=date(2024, 1, 2),
                end=date(2024, 1, 31),
                universe=["SYM1"],
                trailing_pcts=[0.10],
            )
        assert len(df) == 1


# ---------------------------------------------------------------------------
# Test 2 — required columns present
# ---------------------------------------------------------------------------

class TestRunParameterSweepColumns:

    def test_all_required_columns_present(self):
        """Output DataFrame must contain every column in REQUIRED_COLUMNS."""
        with patch.object(_runner, "_run_single", side_effect=_fake_run_single):
            df = run_parameter_sweep(
                base_config={"backtest": {}, "paper_trading": {"initial_capital": 100_000}},
                start=date(2024, 1, 2),
                end=date(2024, 1, 31),
                universe=["SYM1", "SYM2"],
                trailing_pcts=[0.05, 0.10],
            )
        missing = REQUIRED_COLUMNS - set(df.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_no_unexpected_nulls_in_metric_columns(self):
        """Metric columns must not contain NaN values."""
        with patch.object(_runner, "_run_single", side_effect=_fake_run_single):
            df = run_parameter_sweep(
                base_config={"backtest": {}, "paper_trading": {"initial_capital": 100_000}},
                start=date(2024, 1, 2),
                end=date(2024, 1, 31),
                universe=["SYM1"],
                trailing_pcts=[0.07, 0.10],
            )
        for col in REQUIRED_COLUMNS:
            assert df[col].notna().all(), f"Column '{col}' contains NaN"

    def test_trailing_stop_pct_column_matches_input_order(self):
        """trailing_stop_pct values must match the input list, in order."""
        pcts = [0.05, 0.07, 0.10]
        with patch.object(_runner, "_run_single", side_effect=_fake_run_single):
            df = run_parameter_sweep(
                base_config={"backtest": {}, "paper_trading": {"initial_capital": 100_000}},
                start=date(2024, 1, 2),
                end=date(2024, 1, 31),
                universe=["SYM1"],
                trailing_pcts=pcts,
            )
        assert list(df["trailing_stop_pct"]) == pytest.approx(pcts), (
            f"trailing_stop_pct order mismatch: {list(df['trailing_stop_pct'])}"
        )


# ---------------------------------------------------------------------------
# Test 3 — console output
# ---------------------------------------------------------------------------

class TestPrintSweepTable:

    def test_print_sweep_table_produces_output(self, capsys):
        """_print_sweep_table must write a non-empty table to stdout."""
        df = pd.DataFrame({
            "trailing_stop_pct": [0.05, 0.07],
            "cagr":              [0.10, 0.12],
            "sharpe":            [0.80, 0.90],
            "max_drawdown":      [8.00, 9.00],
            "win_rate":          [0.50, 0.55],
            "total_trades":      [10,   15],
        })
        _runner._print_sweep_table(df)
        captured = capsys.readouterr()
        assert len(captured.out) > 0, "Expected non-empty table output"
        assert "5.0%" in captured.out, "Expected '5.0%' trailing pct in output"
        assert "7.0%" in captured.out, "Expected '7.0%' trailing pct in output"


# ---------------------------------------------------------------------------
# Test 4 — Critical regression: trailing stop never drops below VCP floor
#            (unit-level, using simulate_trade directly — mirrors spec item 3)
# ---------------------------------------------------------------------------

class TestCriticalRegressionTrailingFloor:
    """
    Unit-level guard for the VCP-floor invariant.

    Mirrors the critical integration test in test_backtest_e2e.py but lives
    here so a single ``pytest tests/unit/`` run surfaces the regression
    immediately without spinning up the full integration suite.
    """

    _CONFIG = {
        "backtest": {
            "trailing_stop_pct": 0.07,
            "target_pct":        0.30,   # high — trailing fires before target
            "max_hold_days":     60,
        },
        "paper_trading": {"initial_capital": 100_000, "risk_per_trade_pct": 2.0},
        "data": {"features_dir": "data/features"},
    }

    @staticmethod
    def _make_ohlcv(closes: list[float], start: date = date(2024, 1, 2)) -> pd.DataFrame:
        n   = len(closes)
        idx = pd.date_range(start=start, periods=n, freq="B")
        return pd.DataFrame({
            "open":   closes, "high": [c * 1.01 for c in closes],
            "low":    [c * 0.99 for c in closes], "close": closes,
            "volume": [1_000_000] * n,
        }, index=idx)

    def test_trailing_stop_never_drops_below_vcp_floor(self):
        """
        Critical regression (spec unit test 3).

        Entry 100 | VCP stop 88 | trailing 7 %
        Prices: rise to 130, then fall to 119 (triggers trailing stop).
        Assert: trailing_stop_used ≥ 88 at exit AND peak is as expected.
        """
        from backtest.engine import simulate_trade

        closes       = [100, 107, 115, 122, 128, 130, 128, 125, 122, 119]
        entry_price  = 100.0
        stop_loss    = 88.0
        trailing_pct = 0.07

        trade = simulate_trade(
            entry_date=date(2024, 1, 2),
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            ohlcv_df=self._make_ohlcv(closes),
            config=self._CONFIG,
            trailing_stop_pct=trailing_pct,
        )

        assert trade.trailing_stop_used >= stop_loss, (
            f"VCP floor violated: trailing_stop_used={trade.trailing_stop_used:.4f} < {stop_loss}"
        )
        assert trade.peak_price == pytest.approx(130.0)
        expected_ts = 130.0 * (1.0 - trailing_pct)   # 120.9
        assert trade.trailing_stop_used == pytest.approx(expected_ts, rel=1e-3)
        assert trade.exit_reason == "trailing_stop"

    def test_floor_governs_when_pct_trails_below_stop_loss(self):
        """When natural trailing would go below 88, the floor (88) must win."""
        from backtest.engine import simulate_trade

        # peak stays at 93 → natural ts = 93*0.93 = 86.49 < floor 88
        trade = simulate_trade(
            entry_date=date(2024, 1, 2),
            entry_price=100.0,
            stop_loss_price=88.0,
            ohlcv_df=self._make_ohlcv([100, 93, 91, 90]),
            config=self._CONFIG,
            trailing_stop_pct=0.07,
        )
        assert trade.trailing_stop_used >= 88.0, (
            f"Floor violated: trailing_stop_used={trade.trailing_stop_used:.4f}"
        )


# ---------------------------------------------------------------------------
# Test 5 — No lookahead bias (unit-level — spec item 4)
# ---------------------------------------------------------------------------

class TestNoLookaheadBiasUnit:
    """
    Unit-level verification that simulate_trade only processes the rows it
    is given.  Backtest 'enters' on 2020-01-02 with a January-only slice;
    March 2020 sentinel rows are NOT included → exit price must be < 200.
    """

    _CONFIG = {
        "backtest": {
            "trailing_stop_pct": 0.07,
            "target_pct":        0.30,
            "max_hold_days":     30,
        },
        "paper_trading": {"initial_capital": 100_000, "risk_per_trade_pct": 2.0},
        "data": {"features_dir": "data/features"},
    }

    def test_no_lookahead_bias(self):
        """
        Spec unit test 4: backtest on 2020-01-01 does not use 2020-03-01 data.

        Construct 21 January trading-day bars (close 100 … ~104).
        Run simulate_trade on the January slice only.
        Verify exit_price and peak_price are nowhere near the March sentinel
        value of 9 999, confirming zero lookahead.
        """
        from backtest.engine import simulate_trade

        jan_closes = [100.0 + i * 0.2 for i in range(21)]   # 100 … ~104
        jan_idx    = pd.bdate_range(start="2020-01-02", periods=21)
        jan_df     = pd.DataFrame({
            "open": jan_closes, "high": [c * 1.01 for c in jan_closes],
            "low":  [c * 0.99 for c in jan_closes], "close": jan_closes,
            "volume": [1_000_000] * 21,
        }, index=jan_idx)

        trade = simulate_trade(
            entry_date=date(2020, 1, 2),
            entry_price=100.0,
            stop_loss_price=88.0,
            ohlcv_df=jan_df,            # ← January ONLY; March not included
            config=self._CONFIG,
            trailing_stop_pct=0.07,
        )

        SENTINEL = 9_999.0
        assert trade.exit_price  < SENTINEL - 1, (
            f"exit_price={trade.exit_price} suggests future (Mar) data was used"
        )
        assert trade.peak_price  < SENTINEL - 1, (
            f"peak_price={trade.peak_price} suggests future (Mar) data was used"
        )
        assert trade.exit_date.year  == 2020
        assert trade.exit_date.month == 1, (
            f"exit_date={trade.exit_date} went past January — lookahead bias suspected"
        )

    def test_sentinel_visible_only_when_included(self):
        """
        Control: when March sentinel rows ARE appended, peak_price rises to
        9 999.  This proves the January-only test above is a genuine boundary.
        """
        from backtest.engine import simulate_trade

        jan_closes = [100.0 + i * 0.2 for i in range(21)]
        jan_idx    = pd.bdate_range(start="2020-01-02", periods=21)
        jan_df     = pd.DataFrame({
            "open": jan_closes, "high": [c * 1.01 for c in jan_closes],
            "low":  [c * 0.99 for c in jan_closes], "close": jan_closes,
            "volume": [1_000_000] * 21,
        }, index=jan_idx)

        mar_idx = pd.bdate_range(start="2020-03-02", periods=10)
        mar_df  = pd.DataFrame({
            "open": [9_999.0] * 10, "high": [9_999.0] * 10,
            "low":  [9_999.0] * 10, "close": [9_999.0] * 10,
            "volume": [1_000_000] * 10,
        }, index=mar_idx)

        full_df = pd.concat([jan_df, mar_df])

        trade = simulate_trade(
            entry_date=date(2020, 1, 2),
            entry_price=100.0,
            stop_loss_price=88.0,
            ohlcv_df=full_df,           # ← includes March sentinel
            config=self._CONFIG,
            trailing_stop_pct=0.07,
        )

        assert trade.peak_price == pytest.approx(9_999.0), (
            "Full slice must show peak=9999 — confirming engine reads all rows when given"
        )
