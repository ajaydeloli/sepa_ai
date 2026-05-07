"""
dashboard/components/tables.py
--------------------------------
Screener results table, trend-template checklist, and fundamental scorecard
components for the SEPA Streamlit dashboard.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Internal style helpers
# ---------------------------------------------------------------------------

_QUALITY_BG: dict[str, str] = {
    "A+":   "background-color: #fef08a; color: #713f12;",   # gold
    "A":    "background-color: #bbf7d0; color: #14532d;",   # green
    "B":    "background-color: #bfdbfe; color: #1e3a8a;",   # blue
    "C":    "background-color: #e5e7eb; color: #374151;",   # grey
    "FAIL": "background-color: #fecaca; color: #7f1d1d;",   # red
}

_DEFAULT_COLUMNS: list[str] = [
    "symbol", "score", "setup_quality", "stage", "conditions_met",
    "vcp_qualified", "breakout_triggered", "entry_price", "stop_loss",
    "risk_pct", "rs_rating",
]

_TREND_TEMPLATE_LABELS: dict[str, str] = {
    "close_above_sma150_sma200": "Close > SMA150 & SMA200",
    "sma150_above_sma200":       "SMA150 > SMA200",
    "sma200_trending_up":        "SMA200 trending up ≥ 1 month",
    "sma50_above_sma150_sma200": "SMA50 > SMA150 & SMA200",
    "close_above_sma50":         "Close > SMA50",
    "close_above_52w_low":       "Close ≥ 30% above 52-week low",
    "close_within_52w_high":     "Close within 25% of 52-week high",
    "rs_rating_above_70":        "RS Rating ≥ 70",
}

_FUND_LABELS: dict[str, str] = {
    "eps_growth_qoq":    "EPS Growth QoQ",
    "eps_growth_yoy":    "EPS Growth YoY",
    "revenue_growth":    "Revenue Growth",
    "roe":               "ROE",
    "debt_to_equity":    "Debt/Equity",
    "promoter_holding":  "Promoter Holding",
    "institutional_buy": "Institutional Buying",
}


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------

def render_results_table(
    results: list[dict],
    watchlist_symbols: list[str] | None = None,
    show_columns: list[str] | None = None,
) -> str | None:
    """Renders a styled screener results table.

    Returns the symbol the user clicked (if row selection enabled), or None.
    """
    if not results:
        st.info("No results to display.")
        return None

    watchlist_symbols = watchlist_symbols or []
    columns = show_columns or _DEFAULT_COLUMNS

    df = pd.DataFrame(results)

    # Keep only requested columns that actually exist
    columns = [c for c in columns if c in df.columns]
    df = df[columns].copy()

    # -- Watchlist decoration -------------------------------------------------
    if "symbol" in df.columns and watchlist_symbols:
        df["symbol"] = df["symbol"].apply(
            lambda s: f"★ {s}" if s in watchlist_symbols else s
        )

    # -- Breakout flag --------------------------------------------------------
    if "breakout_triggered" in df.columns:
        df["breakout_triggered"] = df["breakout_triggered"].apply(
            lambda v: "🔴 Yes" if v else "No"
        )

    # -- Numeric formatting ---------------------------------------------------
    for col in ("entry_price", "stop_loss"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").map(
                lambda x: f"₹{x:,.2f}" if pd.notna(x) else "—"
            )
    if "risk_pct" in df.columns:
        df["risk_pct"] = pd.to_numeric(df["risk_pct"], errors="coerce").map(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "—"
        )


    # -- Column config for st.dataframe ---------------------------------------
    col_cfg: dict[str, Any] = {}
    if "score" in df.columns:
        col_cfg["score"] = st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=100, format="%d"
        )
    if "setup_quality" in df.columns:
        col_cfg["setup_quality"] = st.column_config.TextColumn("Quality")

    # -- Row selection --------------------------------------------------------
    event = st.dataframe(
        df,
        use_container_width=True,
        column_config=col_cfg,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
    )

    selected_rows = getattr(event, "selection", {})
    rows_idx: list[int] = (
        selected_rows.get("rows", []) if isinstance(selected_rows, dict)
        else getattr(selected_rows, "rows", [])
    )
    if rows_idx:
        raw_symbol: str = results[rows_idx[0]].get("symbol", "")
        return raw_symbol
    return None


# ---------------------------------------------------------------------------
# Trend template checklist
# ---------------------------------------------------------------------------

def render_trend_template_checklist(tt_details: dict) -> None:
    """Renders the 8 Trend Template conditions as a pass/fail checklist."""
    st.subheader("Trend Template Conditions")
    for key, label in _TREND_TEMPLATE_LABELS.items():
        condition_data = tt_details.get(key, {})
        # Support both plain bool and {"pass": bool, "value": ..., "threshold": ...}
        if isinstance(condition_data, dict):
            passed: bool = bool(condition_data.get("pass", False))
            value = condition_data.get("value")
            threshold = condition_data.get("threshold")
        else:
            passed = bool(condition_data)
            value = None
            threshold = None

        icon = "✅" if passed else "❌"
        detail_parts: list[str] = [f"{label} {icon}"]
        if value is not None:
            detail_parts.append(f"Value: {value:.2f}" if isinstance(value, float) else f"Value: {value}")
        if threshold is not None:
            detail_parts.append(f"Threshold: {threshold:.2f}" if isinstance(threshold, float) else f"Threshold: {threshold}")

        detail = "  |  ".join(detail_parts)
        if passed:
            st.success(detail)
        else:
            st.error(detail)


# ---------------------------------------------------------------------------
# Fundamental scorecard
# ---------------------------------------------------------------------------

def render_fundamental_scorecard(fund_details: dict | None) -> None:
    """Renders 7 fundamental conditions as a 2×4 compact grid."""
    if fund_details is None:
        st.info("Fundamentals not available for this symbol.")
        return

    st.subheader("Fundamental Scorecard")
    items = list(_FUND_LABELS.items())
    col1, col2 = st.columns(2)
    columns = [col1, col2]

    for idx, (key, label) in enumerate(items):
        col = columns[idx % 2]
        raw = fund_details.get(key)
        if raw is None:
            display = "N/A"
            passed_flag = None
        elif isinstance(raw, dict):
            passed_flag = bool(raw.get("pass", False))
            val = raw.get("value", "N/A")
            display = f"{val:.2f}" if isinstance(val, float) else str(val)
        elif isinstance(raw, bool):
            passed_flag = raw
            display = "Pass" if raw else "Fail"
        else:
            passed_flag = None
            display = str(raw)

        icon = ("✅" if passed_flag else "❌") if passed_flag is not None else "ℹ️"
        with col:
            st.metric(label=f"{icon} {label}", value=display)


# Needed for column_config type hints
from typing import Any  # noqa: E402
