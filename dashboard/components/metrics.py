"""
dashboard/components/metrics.py
---------------------------------
Score cards, portfolio summary metrics, and run-status bar for the SEPA dashboard.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _score_colour(score: int) -> str:
    if score >= 70:
        return "#22c55e"   # green
    if score >= 40:
        return "#f59e0b"   # yellow
    return "#ef4444"       # red


_QUALITY_EMOJI: dict[str, str] = {
    "A+": "🥇", "A": "🟢", "B": "🔵", "C": "⚪", "FAIL": "🔴",
}

# ---------------------------------------------------------------------------
# Score card
# ---------------------------------------------------------------------------

def render_score_card(score: int, quality: str, stage_label: str) -> None:
    """3-column metric card at the top of the Stock deep-dive page."""
    col1, col2, col3 = st.columns(3)

    colour = _score_colour(score)

    with col1:
        st.metric(label="SEPA Score", value=f"{score} / 100")
        st.markdown(
            f'<div style="height:8px;border-radius:4px;background:linear-gradient('
            f'to right,{colour} {score}%,#e5e7eb {score}%)"></div>',
            unsafe_allow_html=True,
        )

    with col2:
        emoji = _QUALITY_EMOJI.get(quality, "")
        st.metric(label="Setup Quality", value=f"{emoji} {quality}")

    with col3:
        st.metric(label="Market Stage", value=stage_label)


# ---------------------------------------------------------------------------
# Portfolio summary cards
# ---------------------------------------------------------------------------

def render_portfolio_summary_cards(summary: dict) -> None:
    """4-column row of portfolio KPI metrics.

    Expected keys in summary:
      total_return_pct, realised_pnl, win_rate_pct, open_positions
    """
    col1, col2, col3, col4 = st.columns(4)

    total_return: float = float(summary.get("total_return_pct", 0.0))
    realised_pnl: float = float(summary.get("realised_pnl", 0.0))
    win_rate: float = float(summary.get("win_rate_pct", 0.0))
    open_pos: int = int(summary.get("open_positions", 0))

    ret_delta_colour = "normal" if total_return >= 0 else "inverse"

    with col1:
        st.metric(
            label="Total Return",
            value=f"{total_return:+.2f}%",
            delta=f"{total_return:+.2f}%",
            delta_color=ret_delta_colour,
        )
    with col2:
        pnl_sign = "+" if realised_pnl >= 0 else ""
        st.metric(
            label="Realised P&L",
            value=f"₹{realised_pnl:,.0f}",
            delta=f"{pnl_sign}₹{realised_pnl:,.0f}",
            delta_color="normal" if realised_pnl >= 0 else "inverse",
        )
    with col3:
        st.metric(label="Win Rate", value=f"{win_rate:.1f}%")
    with col4:
        st.metric(label="Open Positions", value=str(open_pos))


# ---------------------------------------------------------------------------
# Run status bar
# ---------------------------------------------------------------------------

def render_run_status_bar(last_run: dict | None) -> None:
    """Small info bar at the top of the Watchlist page.

    last_run expected keys (all optional):
      timestamp (ISO str), quality_counts (dict), duration_seconds (float)
    """
    if last_run is None:
        st.info("⏳ No run yet — trigger a scan to populate the watchlist.")
        return

    # Parse timestamp
    ts_raw: str = last_run.get("timestamp", "")
    try:
        ts_dt = datetime.fromisoformat(ts_raw)
        ts_str = ts_dt.strftime("%Y-%m-%d %H:%M IST")
    except (ValueError, TypeError):
        ts_str = ts_raw or "Unknown"

    quality_counts: dict = last_run.get("quality_counts", {})
    a_plus = quality_counts.get("A+", 0)
    a_grade = quality_counts.get("A", 0)
    duration: float = float(last_run.get("duration_seconds", 0.0))

    status_msg = (
        f"📅 Last run: **{ts_str}** &nbsp;|&nbsp; "
        f"⭐ A+: **{a_plus}** &nbsp;|&nbsp; "
        f"🟢 A: **{a_grade}** &nbsp;|&nbsp; "
        f"⏱ Duration: **{duration:.0f}s**"
    )
    st.info(status_msg)
