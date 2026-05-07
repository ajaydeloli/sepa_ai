"""
dashboard/components/charts.py
--------------------------------
Candlestick + equity-curve chart components for the SEPA Streamlit dashboard.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_STAGE_COLOURS: dict[str, str] = {
    "Stage 1": "#888888",
    "Stage 2": "#22c55e",   # green
    "Stage 3": "#f59e0b",   # amber
    "Stage 4": "#ef4444",   # red
}

_QUALITY_COLOURS: dict[str, str] = {
    "A+": "#f59e0b",
    "A":  "#22c55e",
    "B":  "#3b82f6",
    "C":  "#9ca3af",
    "FAIL": "#ef4444",
}


def render_ohlcv_chart(
    symbol: str,
    ohlcv_df: pd.DataFrame,
    result: dict[str, Any],
    vcp_metrics: dict[str, Any] | None = None,
    n_days: int = 90,
) -> None:
    """Renders a candlestick chart inline in Streamlit.

    Chart elements:
      - Candlestick OHLCV (last n_days)
      - MA ribbons: SMA 50 (blue), SMA 150 (orange), SMA 200 (red)
      - Volume panel (bottom 20%)
      - Stage label annotation: top-right, colour-coded
      - Setup quality badge: top-left ("★ A+")
      - Entry price line (green dashed) if result["entry_price"]
      - Stop loss line (red dashed) if result["stop_loss"]
      - VCP contraction zones (yellow shaded) if vcp_metrics provided
    """
    if ohlcv_df is None or ohlcv_df.empty:
        st.warning(f"No OHLCV data available for {symbol}.")
        return

    # -- Slice to last n_days --------------------------------------------------
    df = ohlcv_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.tail(n_days)

    # Rename to mplfinance canonical names
    col_map = {c: c.capitalize() for c in ["open", "high", "low", "close", "volume"]}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # -- MA add-plots ----------------------------------------------------------
    addplots: list[Any] = []
    ma_config = [
        ("sma_50",  "blue",   "SMA 50"),
        ("sma_150", "orange", "SMA 150"),
        ("sma_200", "red",    "SMA 200"),
    ]
    for col, colour, label in ma_config:
        if col in ohlcv_df.columns:
            ma_series = ohlcv_df[col].tail(n_days)
            ma_series.index = df.index
            addplots.append(
                mpf.make_addplot(ma_series, color=colour, width=1.2, label=label)
            )


    # -- Entry / stop lines ----------------------------------------------------
    entry_price: float | None = result.get("entry_price")
    stop_loss: float | None = result.get("stop_loss")
    if entry_price:
        ep_series = pd.Series(entry_price, index=df.index)
        addplots.append(
            mpf.make_addplot(ep_series, color="green", linestyle="--", width=1.0,
                             label=f"Entry {entry_price:.2f}")
        )
    if stop_loss:
        sl_series = pd.Series(stop_loss, index=df.index)
        addplots.append(
            mpf.make_addplot(sl_series, color="red", linestyle="--", width=1.0,
                             label=f"Stop {stop_loss:.2f}")
        )

    # -- Build figure ----------------------------------------------------------
    style = mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle=":")
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        volume=True,
        volume_panel=1,
        panel_ratios=(4, 1),
        addplot=addplots if addplots else None,
        title=f"\n{symbol}",
        returnfig=True,
        figsize=(12, 6),
        tight_layout=True,
    )
    ax_main = axes[0]

    # -- Stage label (top-right) -----------------------------------------------
    stage_label: str = result.get("stage_label", "")
    quality: str = result.get("setup_quality", "")
    stage_colour = _STAGE_COLOURS.get(stage_label, "#888888")
    ax_main.annotate(
        stage_label,
        xy=(0.98, 0.95),
        xycoords="axes fraction",
        ha="right", va="top",
        fontsize=11, fontweight="bold",
        color="white",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=stage_colour, alpha=0.85),
    )

    # -- Quality badge (top-left) ----------------------------------------------
    badge_colour = _QUALITY_COLOURS.get(quality, "#9ca3af")
    ax_main.annotate(
        f"★ {quality}",
        xy=(0.02, 0.95),
        xycoords="axes fraction",
        ha="left", va="top",
        fontsize=11, fontweight="bold",
        color="white",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=badge_colour, alpha=0.85),
    )


    # -- VCP contraction zones (yellow shaded) --------------------------------
    if vcp_metrics:
        contraction_zones: list[tuple[Any, Any]] = vcp_metrics.get("contraction_zones", [])
        for start_date, end_date in contraction_zones:
            try:
                ax_main.axvspan(
                    pd.Timestamp(start_date),
                    pd.Timestamp(end_date),
                    alpha=0.15,
                    color="yellow",
                )
            except Exception:
                pass  # skip malformed zone quietly

    st.pyplot(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def render_equity_curve(equity_curve: list[dict]) -> None:
    """Renders paper-trading equity curve.

    equity_curve: list of {"date": str, "total_value": float}
    Shows initial_capital as a baseline horizontal line.
    """
    if not equity_curve:
        st.info("No equity curve data yet — run the paper-trading simulator first.")
        return

    df = pd.DataFrame(equity_curve)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    if "total_value" not in df.columns:
        st.warning("Equity curve data missing 'total_value' field.")
        return

    initial_capital: float = float(df["total_value"].iloc[0])

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(df.index, df["total_value"], color="#3b82f6", linewidth=2, label="Portfolio Value")
    ax.axhline(y=initial_capital, color="#9ca3af", linestyle="--", linewidth=1,
               label=f"Initial Capital ₹{initial_capital:,.0f}")
    ax.fill_between(df.index, initial_capital, df["total_value"],
                    where=df["total_value"] >= initial_capital,
                    alpha=0.15, color="#22c55e", label="Profit zone")
    ax.fill_between(df.index, initial_capital, df["total_value"],
                    where=df["total_value"] < initial_capital,
                    alpha=0.15, color="#ef4444", label="Drawdown zone")
    ax.set_title("Paper Trading Equity Curve", fontsize=13)
    ax.set_ylabel("Portfolio Value (₹)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, linestyle=":", alpha=0.5)
    fig.tight_layout()

    st.pyplot(fig)
    plt.close(fig)
