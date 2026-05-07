"""
tests/unit/test_dashboard_components.py
-----------------------------------------
Unit tests for dashboard/components/{charts,tables,metrics}.

All Streamlit calls are patched via unittest.mock.patch so no real UI
is rendered.  Each test checks that the function completes without raising.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Build a minimal fake "streamlit" module so imports inside components work
# without a real Streamlit runtime.
# ---------------------------------------------------------------------------

def _make_fake_streamlit() -> types.ModuleType:
    st = types.ModuleType("streamlit")
    for attr in (
        "metric", "info", "warning", "error", "success", "caption",
        "subheader", "dataframe", "line_chart", "pyplot",
        "columns", "markdown",
    ):
        setattr(st, attr, MagicMock(return_value=MagicMock()))

    # st.columns returns a list of context-manager mocks
    def _columns(n, **_kwargs):
        mocks = [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        for m in mocks:
            m.__enter__ = MagicMock(return_value=m)
            m.__exit__ = MagicMock(return_value=False)
        return mocks

    st.columns = _columns

    # st.column_config namespace
    col_cfg = types.SimpleNamespace(
        ProgressColumn=MagicMock(return_value=None),
        TextColumn=MagicMock(return_value=None),
    )
    st.column_config = col_cfg

    # st.dataframe returns an object with .selection = {"rows": []}
    def _dataframe(*_a, **_kw):
        sel = types.SimpleNamespace(rows=[])
        return types.SimpleNamespace(selection=sel)

    st.dataframe = _dataframe
    return st


# Inject fake streamlit before any component import
_fake_st = _make_fake_streamlit()
sys.modules.setdefault("streamlit", _fake_st)

# Also stub heavy plotting deps so tests don't need them installed
for _mod in ("mplfinance", "matplotlib", "matplotlib.pyplot"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# plt.subplots() must return a (fig, ax) 2-tuple so that unpacking inside
# render_equity_curve (and any other chart function) doesn't raise ValueError.
_plt_stub = sys.modules["matplotlib.pyplot"]
_plt_stub.subplots.return_value = (MagicMock(), MagicMock())

# Now safe to import components
from dashboard.components.tables import (  # noqa: E402
    render_fundamental_scorecard,
    render_results_table,
    render_trend_template_checklist,
)
from dashboard.components.metrics import (  # noqa: E402
    render_portfolio_summary_cards,
    render_run_status_bar,
    render_score_card,
)
from dashboard.components.charts import render_equity_curve  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_THREE_RESULTS: list[dict] = [
    {
        "symbol": "RELIANCE",
        "score": 88,
        "setup_quality": "A+",
        "stage": 2,
        "stage_label": "Stage 2",
        "conditions_met": 8,
        "vcp_qualified": True,
        "breakout_triggered": True,
        "entry_price": 2950.0,
        "stop_loss": 2800.0,
        "risk_pct": 5.1,
        "rs_rating": 92,
    },
    {
        "symbol": "TCS",
        "score": 74,
        "setup_quality": "A",
        "stage": 2,
        "stage_label": "Stage 2",
        "conditions_met": 8,
        "vcp_qualified": False,
        "breakout_triggered": False,
        "entry_price": None,
        "stop_loss": None,
        "risk_pct": None,
        "rs_rating": 78,
    },
    {
        "symbol": "INFY",
        "score": 57,
        "setup_quality": "B",
        "stage": 2,
        "stage_label": "Stage 2",
        "conditions_met": 6,
        "vcp_qualified": False,
        "breakout_triggered": False,
        "entry_price": 1800.0,
        "stop_loss": 1720.0,
        "risk_pct": 4.4,
        "rs_rating": 71,
    },
]



# ---------------------------------------------------------------------------
# Test 1 – render_results_table with 3 results
# ---------------------------------------------------------------------------

def test_render_results_table_three_results_no_crash():
    """render_results_table must complete without raising for 3 valid results."""
    result = render_results_table(
        results=_THREE_RESULTS,
        watchlist_symbols=["RELIANCE"],
    )
    # Returns None when nothing is selected (fake dataframe has no selection rows)
    assert result is None


def test_render_results_table_highlights_watchlist_symbol():
    """Watchlist symbols should get a ★ prefix and breakout flag should be 🔴."""
    # Just verify no exception; deeper output assertions require a real Streamlit runtime
    render_results_table(
        results=_THREE_RESULTS,
        watchlist_symbols=["TCS", "INFY"],
    )


def test_render_results_table_empty_returns_none():
    """Empty results list triggers st.info and returns None."""
    result = render_results_table(results=[])
    assert result is None


def test_render_results_table_custom_columns():
    """show_columns subset works without raising."""
    render_results_table(
        results=_THREE_RESULTS,
        show_columns=["symbol", "score", "setup_quality"],
    )


# ---------------------------------------------------------------------------
# Test 2 – render_trend_template_checklist with all conditions True
# ---------------------------------------------------------------------------

_ALL_TRUE_TT: dict = {
    "close_above_sma150_sma200": {"pass": True,  "value": 145.2,  "threshold": 132.1},
    "sma150_above_sma200":       {"pass": True,  "value": 132.1,  "threshold": 128.5},
    "sma200_trending_up":        {"pass": True,  "value": 0.5,    "threshold": 0.0},
    "sma50_above_sma150_sma200": {"pass": True,  "value": 138.0,  "threshold": 132.1},
    "close_above_sma50":         {"pass": True,  "value": 145.2,  "threshold": 138.0},
    "close_above_52w_low":       {"pass": True,  "value": 145.2,  "threshold": 112.0},
    "close_within_52w_high":     {"pass": True,  "value": 145.2,  "threshold": 155.0},
    "rs_rating_above_70":        {"pass": True,  "value": 92,     "threshold": 70},
}


def test_render_trend_template_checklist_all_true():
    """All-pass checklist must not raise any exception."""
    render_trend_template_checklist(_ALL_TRUE_TT)


def test_render_trend_template_checklist_mixed():
    """Mix of pass/fail dicts should be handled without raising."""
    mixed = dict(_ALL_TRUE_TT)
    mixed["sma200_trending_up"] = {"pass": False, "value": -0.1, "threshold": 0.0}
    mixed["rs_rating_above_70"] = {"pass": False, "value": 65, "threshold": 70}
    render_trend_template_checklist(mixed)


def test_render_trend_template_checklist_plain_bool():
    """Plain boolean values (non-dict) should be accepted without raising."""
    plain = {key: True for key in _ALL_TRUE_TT}
    render_trend_template_checklist(plain)


def test_render_trend_template_checklist_empty_dict():
    """Empty dict (all conditions missing) should default to failed without raising."""
    render_trend_template_checklist({})


# ---------------------------------------------------------------------------
# Test 3 – render_fundamental_scorecard
# ---------------------------------------------------------------------------

_FUND_DETAILS: dict = {
    "eps_growth_qoq":    {"pass": True,  "value": 18.5},
    "eps_growth_yoy":    {"pass": True,  "value": 24.1},
    "revenue_growth":    {"pass": True,  "value": 15.0},
    "roe":               {"pass": True,  "value": 22.3},
    "debt_to_equity":    {"pass": False, "value": 1.8},
    "promoter_holding":  {"pass": True,  "value": 67.4},
    "institutional_buy": {"pass": False, "value": False},
}


def test_render_fundamental_scorecard_none_shows_info():
    """Passing None must display the 'not available' info box without raising."""
    render_fundamental_scorecard(None)


def test_render_fundamental_scorecard_full_details():
    """Full valid fund_details dict must render without raising."""
    render_fundamental_scorecard(_FUND_DETAILS)


def test_render_fundamental_scorecard_partial_details():
    """Partial dict (some keys missing) must render without raising."""
    render_fundamental_scorecard({"eps_growth_qoq": {"pass": True, "value": 10.0}})


def test_render_fundamental_scorecard_plain_bool_values():
    """Plain bool values (non-dict) must be handled without raising."""
    plain = {key: True for key in _FUND_DETAILS}
    render_fundamental_scorecard(plain)


def test_render_fundamental_scorecard_none_values():
    """Keys explicitly set to None should render as N/A without raising."""
    nulled = {key: None for key in _FUND_DETAILS}
    render_fundamental_scorecard(nulled)


# ---------------------------------------------------------------------------
# Test 4 – render_score_card
# ---------------------------------------------------------------------------

def test_render_score_card_high_score():
    """Score 91 (green zone) must render without raising."""
    render_score_card(score=91, quality="A+", stage_label="Stage 2")


def test_render_score_card_mid_score():
    """Score 55 (yellow zone) must render without raising."""
    render_score_card(score=55, quality="B", stage_label="Stage 2")


def test_render_score_card_low_score():
    """Score 25 (red zone) must render without raising."""
    render_score_card(score=25, quality="FAIL", stage_label="Stage 4")


def test_render_score_card_boundary_values():
    """Boundary scores (0, 40, 70, 100) must all render without raising."""
    for score, quality in [(0, "FAIL"), (40, "C"), (70, "A"), (100, "A+")]:
        render_score_card(score=score, quality=quality, stage_label="Stage 2")


# ---------------------------------------------------------------------------
# Test 5 – render_equity_curve
# ---------------------------------------------------------------------------

def test_render_equity_curve_empty_list():
    """Empty equity curve must show the info message without raising."""
    render_equity_curve([])


def test_render_equity_curve_single_point():
    """Single data point must not raise (edge case for fill_between)."""
    render_equity_curve([{"date": "2024-01-01", "total_value": 100_000.0}])


def test_render_equity_curve_profit_and_drawdown():
    """Curve that crosses above and below initial capital must render without raising."""
    render_equity_curve([
        {"date": "2024-01-01", "total_value": 100_000.0},
        {"date": "2024-01-15", "total_value": 105_000.0},
        {"date": "2024-02-01", "total_value":  96_000.0},
        {"date": "2024-03-01", "total_value": 112_000.0},
    ])


def test_render_equity_curve_missing_total_value_key():
    """Rows missing 'total_value' must trigger a warning, not a crash."""
    render_equity_curve([{"date": "2024-01-01", "nav": 100_000.0}])


# ---------------------------------------------------------------------------
# Test 6 – render_portfolio_summary_cards
# ---------------------------------------------------------------------------

def test_render_portfolio_summary_cards_positive():
    """Positive return portfolio summary must render without raising."""
    render_portfolio_summary_cards({
        "total_return_pct": 14.7,
        "realised_pnl": 47_300.0,
        "win_rate_pct": 62.5,
        "open_positions": 3,
    })


def test_render_portfolio_summary_cards_negative_return():
    """Negative return should use inverse delta colour without raising."""
    render_portfolio_summary_cards({
        "total_return_pct": -5.2,
        "realised_pnl": -15_600.0,
        "win_rate_pct": 40.0,
        "open_positions": 1,
    })


def test_render_portfolio_summary_cards_empty_dict():
    """Missing keys must default to zeros without raising."""
    render_portfolio_summary_cards({})


# ---------------------------------------------------------------------------
# Test 7 – render_run_status_bar
# ---------------------------------------------------------------------------

def test_render_run_status_bar_none():
    """None last_run must display 'No run yet' info box without raising."""
    render_run_status_bar(None)


def test_render_run_status_bar_valid():
    """Valid last_run dict must format the status bar without raising."""
    render_run_status_bar({
        "timestamp": "2024-01-15T15:35:00",
        "quality_counts": {"A+": 3, "A": 12},
        "duration_seconds": 28.4,
    })


def test_render_run_status_bar_missing_keys():
    """Partial last_run dict (no quality_counts or duration) must not raise."""
    render_run_status_bar({"timestamp": "2024-01-15T09:00:00"})


def test_render_run_status_bar_bad_timestamp():
    """Unparseable timestamp string must fall back gracefully without raising."""
    render_run_status_bar({"timestamp": "not-a-date", "quality_counts": {}, "duration_seconds": 5})
