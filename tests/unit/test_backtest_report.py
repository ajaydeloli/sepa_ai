"""
tests/unit/test_backtest_report.py
-----------------------------------
Unit tests for backtest/report.py and scripts/backtest_runner.py (smoke).

Coverage
--------
1.  generate_report with 10 trades → HTML and CSV files created
2.  HTML contains regime breakdown table (th text check)
3.  plot_equity_curve returns valid base64 string
4.  CSV has correct header + one row per trade
5.  generate_report with 0 trades → "No trades" HTML generated, no crash
6.  Smoke test: backtest_runner.py --help exits 0 without error
"""

from __future__ import annotations

import base64
import csv
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtest.engine import BacktestResult, BacktestTrade
from backtest.report import generate_report, plot_equity_curve

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNNER       = _PROJECT_ROOT / "scripts" / "backtest_runner.py"


def _make_trade(
    symbol: str = "TCS",
    entry_date: date = date(2022, 1, 10),
    exit_date: date = date(2022, 2, 5),
    entry_price: float = 100.0,
    exit_price: float = 115.0,
    stop_loss_price: float = 85.0,
    pnl: float = 1_500.0,
    pnl_pct: float = 15.0,
    r_multiple: float = 1.0,
    regime: str = "Bull",
    setup_quality: str = "A+",
    stop_type: str = "trailing",
    exit_reason: str = "trailing_stop",
) -> BacktestTrade:
    return BacktestTrade(
        symbol=symbol,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss_price=stop_loss_price,
        peak_price=exit_price,
        trailing_stop_used=stop_loss_price * 1.05,
        stop_type=stop_type,
        quantity=10,
        pnl=pnl,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
        exit_reason=exit_reason,
        regime=regime,
        setup_quality=setup_quality,
        sepa_score=82,
    )


def _make_result(trades: list[BacktestTrade]) -> BacktestResult:
    return BacktestResult(
        start_date=date(2022, 1, 1),
        end_date=date(2022, 12, 31),
        trades=trades,
        universe_size=200,
        config_snapshot={"backtest": {"trailing_stop_pct": 0.07}},
    )


def _make_metrics(trades: list[BacktestTrade]) -> dict:
    from backtest.metrics import compute_metrics
    return compute_metrics(trades, [], 100_000.0)


def _make_equity_curve(n: int = 20) -> list[dict]:
    """Build n equity snapshots starting from 2022-01-01.

    Uses timedelta offsets (one calendar day per step) so the date never
    overflows a month boundary, regardless of how large n is.
    """
    base  = 100_000.0
    start = date(2022, 1, 1)
    return [
        {
            "date": start + timedelta(days=i),
            "portfolio_value": base + i * 500.0,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Ten sample trades for the main tests
# ---------------------------------------------------------------------------

_SYMBOLS = [
    "TCS", "INFY", "WIPRO", "HDFC", "RELIANCE",
    "BAJFINANCE", "TITAN", "MARUTI", "HCLTECH", "ICICIBANK",
]

_TRADES_10: list[BacktestTrade] = [
    _make_trade(
        symbol=sym,
        pnl=1_000.0 if i % 3 != 0 else -500.0,
        pnl_pct=10.0 if i % 3 != 0 else -5.0,
        r_multiple=1.5 if i % 3 != 0 else -0.5,
        regime="Bull" if i < 5 else ("Bear" if i < 8 else "Sideways"),
        setup_quality="A+" if i < 4 else ("A" if i < 7 else "B"),
        exit_date=date(2022, (i % 11) + 1, 15),
    )
    for i, sym in enumerate(_SYMBOLS)
]


# ---------------------------------------------------------------------------
# Test 1 — generate_report with 10 trades → HTML and CSV created
# ---------------------------------------------------------------------------

def test_generate_report_creates_html_and_csv(tmp_path):
    """Both HTML and CSV files must be written to output_dir."""
    result  = _make_result(_TRADES_10)
    metrics = _make_metrics(_TRADES_10)
    ec      = _make_equity_curve(30)

    html_path, csv_path = generate_report(
        result=result,
        metrics=metrics,
        output_dir=str(tmp_path),
        equity_curve=ec,
    )

    assert Path(html_path).exists(), "HTML report file was not created"
    assert Path(csv_path).exists(),  "CSV file was not created"

    html = Path(html_path).read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "Backtest Report" in html

    # All 10 symbols should appear in the full trades table
    for sym in _SYMBOLS:
        assert sym in html, f"Symbol {sym} missing from HTML"


# ---------------------------------------------------------------------------
# Test 2 — HTML contains regime breakdown table
# ---------------------------------------------------------------------------

def test_html_contains_regime_breakdown_table(tmp_path):
    """The regime breakdown section must contain its column headers."""
    result  = _make_result(_TRADES_10)
    metrics = _make_metrics(_TRADES_10)

    html_path, _ = generate_report(
        result=result,
        metrics=metrics,
        output_dir=str(tmp_path),
    )

    html = Path(html_path).read_text(encoding="utf-8")

    # The section heading
    assert "Regime Breakdown" in html
    # Column headers in the table
    assert "Regime" in html
    assert "Win Rate" in html
    assert "Avg R-Multiple" in html
    # At least one regime label should appear
    assert any(r in html for r in ("Bull", "Bear", "Sideways")), (
        "No regime label found in HTML"
    )


# ---------------------------------------------------------------------------
# Test 3 — plot_equity_curve returns valid base64 string
# ---------------------------------------------------------------------------

def test_plot_equity_curve_returns_valid_base64():
    """plot_equity_curve must return a non-empty, valid base64 PNG string."""
    ec  = _make_equity_curve(40)   # 40 days — safe with timedelta offsets
    b64 = plot_equity_curve(ec)

    assert isinstance(b64, str), "Return value should be a str"
    assert len(b64) > 100,      "base64 string is unexpectedly short"

    # Must decode without error and begin with PNG magic bytes
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", (
        "Decoded bytes do not look like a PNG file"
    )


def test_plot_equity_curve_empty_returns_empty_string():
    """Empty equity_curve must return an empty string, not raise."""
    result = plot_equity_curve([])
    assert result == "", f"Expected '', got {result!r}"


# ---------------------------------------------------------------------------
# Test 4 — CSV has one row per trade + header row
# ---------------------------------------------------------------------------

def test_csv_has_correct_row_count(tmp_path):
    """CSV must have a header row plus exactly one data row per trade."""
    result  = _make_result(_TRADES_10)
    metrics = _make_metrics(_TRADES_10)

    _, csv_path = generate_report(
        result=result,
        metrics=metrics,
        output_dir=str(tmp_path),
    )

    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 10, f"Expected 10 data rows, got {len(rows)}"

    # Header must contain expected field names
    assert "symbol"     in rows[0], "'symbol' column missing from CSV"
    assert "pnl"        in rows[0], "'pnl' column missing from CSV"
    assert "pnl_pct"    in rows[0], "'pnl_pct' column missing from CSV"
    assert "r_multiple" in rows[0], "'r_multiple' column missing from CSV"
    assert "regime"     in rows[0], "'regime' column missing from CSV"


def test_csv_field_values_match_trades(tmp_path):
    """CSV field values should round-trip correctly from BacktestTrade."""
    trades  = [_make_trade(symbol="RELIANCE", pnl=2_000.0, pnl_pct=20.0)]
    result  = _make_result(trades)
    metrics = _make_metrics(trades)

    _, csv_path = generate_report(
        result=result, metrics=metrics, output_dir=str(tmp_path)
    )

    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["symbol"] == "RELIANCE"
    assert float(rows[0]["pnl"])     == pytest.approx(2_000.0)
    assert float(rows[0]["pnl_pct"]) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Test 5 — generate_report with 0 trades → no crash, "No trades" HTML
# ---------------------------------------------------------------------------

def test_generate_report_zero_trades_no_crash(tmp_path):
    """Zero-trade result must produce valid HTML with a 'no trades' notice."""
    result  = _make_result([])
    metrics = _make_metrics([])

    html_path, csv_path = generate_report(
        result=result,
        metrics=metrics,
        output_dir=str(tmp_path),
    )

    assert Path(html_path).exists(), "HTML file was not created for 0-trade result"
    assert Path(csv_path).exists(),  "CSV file was not created for 0-trade result"

    html = Path(html_path).read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    # Should mention "No trades" somewhere in the report
    assert "No trades" in html or "No trades were generated" in html

    # CSV must have only the header row (no data rows)
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 0, f"Expected 0 data rows for empty trades, got {len(rows)}"


# ---------------------------------------------------------------------------
# Test 6 — CLI smoke test: backtest_runner.py --help exits 0
# ---------------------------------------------------------------------------

def test_backtest_runner_help_exits_zero():
    """backtest_runner.py --help must exit 0 without crashing."""
    proc = subprocess.run(
        [sys.executable, str(_RUNNER), "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"--help exited with code {proc.returncode}.\n"
        f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )
    # Basic sanity: help text mentions the key flags
    combined = proc.stdout + proc.stderr
    assert "--start" in combined, "'--start' not found in --help output"
    assert "--end"   in combined, "'--end' not found in --help output"


# ---------------------------------------------------------------------------
# Test 7 — Equity curve chart is embedded in HTML when supplied
# ---------------------------------------------------------------------------

def test_equity_curve_embedded_in_html(tmp_path):
    """When equity_curve is supplied the HTML must contain a base64 <img>."""
    result  = _make_result(_TRADES_10)
    metrics = _make_metrics(_TRADES_10)
    ec      = _make_equity_curve(25)

    html_path, _ = generate_report(
        result=result,
        metrics=metrics,
        output_dir=str(tmp_path),
        equity_curve=ec,
    )

    html = Path(html_path).read_text(encoding="utf-8")
    assert 'src="data:image/png;base64,' in html, (
        "Equity curve base64 <img> tag not found in HTML"
    )


# ---------------------------------------------------------------------------
# Test 8 — Stop comparison section appears when both metrics are provided
# ---------------------------------------------------------------------------

def test_stop_comparison_section_present_when_both_metrics_supplied(tmp_path):
    """HTML must include the comparison table when trailing + fixed metrics given."""
    result  = _make_result(_TRADES_10)
    metrics = _make_metrics(_TRADES_10)

    html_path, _ = generate_report(
        result=result,
        metrics=metrics,
        output_dir=str(tmp_path),
        trailing_metrics=metrics,   # reuse same dict for test simplicity
        fixed_metrics=metrics,
    )

    html = Path(html_path).read_text(encoding="utf-8")
    assert "Trailing vs Fixed Stop Comparison" in html, (
        "Comparison section heading not found in HTML"
    )
    assert "Trailing Stop" in html
    assert "Fixed Stop"    in html
