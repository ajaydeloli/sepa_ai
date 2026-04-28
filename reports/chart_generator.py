"""
reports/chart_generator.py
--------------------------
Candlestick chart generation with MA ribbons, VCP markup, stage annotation,
and pivot markers for the Minervini SEPA screening system.

Public API
----------
generate_chart(symbol, ohlcv_df, result, vcp_metrics, output_dir, run_date, n_days)
    → str  — path to the saved PNG

generate_batch_charts(results, ohlcv_data, vcp_data, output_dir, run_date,
                      min_quality, watchlist_symbols)
    → dict[str, str]  — {symbol: file_path} for successfully generated charts

Design notes
------------
* Never calls plt.show() — every figure is saved then closed via plt.close(fig).
* All matplotlib/mplfinance calls are wrapped in try/except; failures are
  re-raised as ChartGenerationError.
* VCP contraction zones are derived by re-running find_all_pivots() on the
  displayed tail so x-positions align with the mplfinance integer x-axis.
* Quality-threshold filtering in generate_batch_charts() uses an ordinal
  comparison so "B" includes "A+" and "A" as well.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")          # non-interactive backend — must precede other mpl imports
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from features.pivot import find_all_pivots
from features.vcp import VCPMetrics
from rules.scorer import SEPAResult
from utils.exceptions import ChartGenerationError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Ordinal map — lower number = better quality; used for min_quality filtering.
QUALITY_ORDER: dict[str, int] = {"A+": 0, "A": 1, "B": 2, "C": 3, "FAIL": 4}

_STAGE_COLORS: dict[int, str] = {
    1: "#adb5bd",   # grey  — basing
    2: "#2dc653",   # green — advancing
    3: "#ffd166",   # amber — topping
    4: "#ef233c",   # red   — declining
}

_QUALITY_BADGE_COLORS: dict[str, str] = {
    "A+": "#ffd700",   # gold
    "A":  "#2dc653",   # green
    "B":  "#4895ef",   # steel-blue
    "C":  "#fb8500",   # orange
    "FAIL": "#ef233c", # red
}

_MA_CONFIGS: list[dict] = [
    {"col": "sma_50",  "color": "#3a86ff", "width": 1.2, "label": "SMA 50"},
    {"col": "sma_150", "color": "#fb8500", "width": 1.2, "label": "SMA 150"},
    {"col": "sma_200", "color": "#e63946", "width": 1.5, "label": "SMA 200"},
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _meets_quality(quality: str, min_quality: str) -> bool:
    """Return True when *quality* is at least as good as *min_quality*."""
    return QUALITY_ORDER.get(quality, 99) <= QUALITY_ORDER.get(min_quality, 99)


def _build_vcp_legs(
    swing_highs: list[tuple[int, float]],
    swing_lows: list[tuple[int, float]],
) -> list[dict]:
    """Pair each swing high with the first subsequent swing low → contraction legs."""
    legs: list[dict] = []
    for sh_idx, sh_price in swing_highs:
        for sl_idx, sl_price in swing_lows:
            if sl_idx > sh_idx:
                legs.append({"start_idx": sh_idx, "end_idx": sl_idx,
                             "high_price": sh_price, "low_price": sl_price})
                break
    return legs


def _add_vcp_zones(
    ax: "plt.Axes",
    ohlcv_tail: pd.DataFrame,
    vcp_metrics: VCPMetrics,
    sensitivity: int = 5,
) -> None:
    """Shade each VCP contraction leg on *ax* with a translucent yellow box."""
    if vcp_metrics.contraction_count == 0:
        return
    try:
        swing_highs, swing_lows = find_all_pivots(ohlcv_tail, sensitivity=sensitivity)
        legs = _build_vcp_legs(swing_highs, swing_lows)
        for leg in legs:
            ax.axvspan(
                leg["start_idx"] - 0.5,
                leg["end_idx"] + 0.5,
                alpha=0.08,
                color="yellow",
                zorder=0,
            )
    except Exception as exc:                           # noqa: BLE001
        log.debug("_add_vcp_zones: skipping zones due to error: %s", exc)


def _add_pivot_markers(
    ax: "plt.Axes",
    ohlcv_tail: pd.DataFrame,
    sensitivity: int = 5,
) -> None:
    """Draw downward triangles at each confirmed swing high in the tail."""
    try:
        swing_highs, _ = find_all_pivots(ohlcv_tail, sensitivity=sensitivity)
        for sh_idx, sh_price in swing_highs:
            ax.plot(
                sh_idx,
                sh_price * 1.005,          # slightly above the high bar
                marker="v",
                color="#fb8500",
                markersize=7,
                alpha=0.85,
                zorder=5,
                linestyle="None",
            )
    except Exception as exc:               # noqa: BLE001
        log.debug("_add_pivot_markers: skipping due to error: %s", exc)


def _annotate_chart(
    ax: "plt.Axes",
    result: SEPAResult,
    ohlcv_tail: pd.DataFrame,
) -> None:
    """Add all text/line overlays: stage label, quality badge, entry/stop lines."""

    # --- Stage label (top-right) ---
    stage_color = _STAGE_COLORS.get(result.stage, "#adb5bd")
    ax.text(
        0.98, 0.96,
        result.stage_label,
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=9, fontweight="bold",
        color=stage_color,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.7, edgecolor=stage_color),
    )

    # --- Trend template pass / fail (below stage label) ---
    tt_symbol = "✓ Trend Template" if result.trend_template_pass else "✗ Trend Template"
    tt_color  = "#2dc653" if result.trend_template_pass else "#ef233c"
    ax.text(
        0.98, 0.88,
        tt_symbol,
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=8,
        color=tt_color,
    )

    # --- Setup quality badge (top-left) ---
    badge_color = _QUALITY_BADGE_COLORS.get(result.setup_quality, "#adb5bd")
    ax.text(
        0.02, 0.96,
        f"★ {result.setup_quality}",
        transform=ax.transAxes,
        ha="left", va="top",
        fontsize=11, fontweight="bold",
        color=badge_color,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.7, edgecolor=badge_color),
    )

    # --- Entry price dashed line (green) ---
    if result.breakout_triggered and result.entry_price is not None:
        ax.axhline(
            y=result.entry_price,
            color="#2dc653",
            linestyle="--",
            linewidth=1.2,
            alpha=0.8,
            label=f"Entry {result.entry_price:.2f}",
        )
        ax.text(
            0.01, result.entry_price,
            f" Entry {result.entry_price:.2f}",
            transform=ax.get_yaxis_transform(),
            va="bottom", fontsize=7, color="#2dc653",
        )

    # --- Stop-loss dashed line (red) ---
    if result.stop_loss is not None:
        ax.axhline(
            y=result.stop_loss,
            color="#ef233c",
            linestyle="--",
            linewidth=1.2,
            alpha=0.8,
            label=f"Stop {result.stop_loss:.2f}",
        )
        ax.text(
            0.01, result.stop_loss,
            f" Stop {result.stop_loss:.2f}",
            transform=ax.get_yaxis_transform(),
            va="top", fontsize=7, color="#ef233c",
        )


# ---------------------------------------------------------------------------
# Public API — generate_chart()
# ---------------------------------------------------------------------------

def generate_chart(
    symbol: str,
    ohlcv_df: pd.DataFrame,
    result: SEPAResult,
    vcp_metrics: Optional[VCPMetrics],
    output_dir: str,
    run_date: date,
    n_days: int = 90,
) -> str:
    """Generate a candlestick chart PNG and return its path.

    Parameters
    ----------
    symbol:
        Ticker symbol — used in the filename and chart title.
    ohlcv_df:
        Full history DataFrame with DatetimeIndex and columns
        [open, high, low, close, volume].  MA columns sma_50, sma_150,
        sma_200 are used when present; missing ones are silently skipped.
    result:
        SEPAResult for overlay annotations.
    vcp_metrics:
        When provided, VCP contraction zones and pivot markers are drawn.
    output_dir:
        Root output directory.  Charts are saved under ``{output_dir}/charts/``.
    run_date:
        Date of the screening run — used in title and filename.
    n_days:
        How many trailing calendar days of OHLCV to display (default 90).

    Returns
    -------
    str
        Absolute path to the saved PNG file.

    Raises
    ------
    ChartGenerationError
        When *ohlcv_df* is empty, or any rendering step fails.
    """
    if ohlcv_df is None or ohlcv_df.empty:
        raise ChartGenerationError(
            f"generate_chart: empty OHLCV DataFrame for {symbol}",
            detail=f"symbol={symbol} run_date={run_date}",
        )

    fig: Optional["plt.Figure"] = None
    try:
        # ------------------------------------------------------------------
        # Slice to n_days tail and ensure column names match mplfinance spec
        # ------------------------------------------------------------------
        tail = ohlcv_df.iloc[-n_days:].copy()

        # mplfinance requires title-cased or lower-cased OHLCV; normalise to lower
        tail.columns = [c.lower() for c in tail.columns]

        mpf_df = tail[["open", "high", "low", "close", "volume"]].copy()

        # ------------------------------------------------------------------
        # Build MA addplots (skip any column that is absent)
        # ------------------------------------------------------------------
        add_plots = []
        for ma in _MA_CONFIGS:
            col = ma["col"]
            if col in tail.columns and tail[col].notna().any():
                add_plots.append(
                    mpf.make_addplot(
                        tail[col],
                        color=ma["color"],
                        width=ma["width"],
                        label=ma["label"],
                    )
                )

        # ------------------------------------------------------------------
        # Title
        # ------------------------------------------------------------------
        title = f"{symbol} — {run_date} — Score: {result.score}/100"

        # ------------------------------------------------------------------
        # mplfinance plot
        # ------------------------------------------------------------------
        plot_kwargs: dict = dict(
            type="candle",
            style="charles",
            volume=True,
            returnfig=True,
            figsize=(14, 8),
            title=title,
            warn_too_much_data=len(mpf_df) + 1,   # suppress warning
        )
        if add_plots:
            plot_kwargs["addplot"] = add_plots

        fig, axes = mpf.plot(mpf_df, **plot_kwargs)

        # axes[0] = price panel, axes[2] = volume panel (axes[1] is the twin)
        price_ax = axes[0]

        # ------------------------------------------------------------------
        # VCP contraction zones + pivot markers
        # ------------------------------------------------------------------
        if vcp_metrics is not None:
            _add_vcp_zones(price_ax, tail, vcp_metrics)
            _add_pivot_markers(price_ax, tail)

        # ------------------------------------------------------------------
        # Text annotations and price lines
        # ------------------------------------------------------------------
        _annotate_chart(price_ax, result, tail)

        # ------------------------------------------------------------------
        # Save
        # ------------------------------------------------------------------
        charts_dir = Path(output_dir) / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)
        out_path = charts_dir / f"{symbol}_{run_date}.png"
        fig.savefig(str(out_path), dpi=120, bbox_inches="tight")

        log.info(
            "generate_chart: saved %s (score=%d quality=%s stage=%d)",
            out_path, result.score, result.setup_quality, result.stage,
        )
        return str(out_path)

    except ChartGenerationError:
        raise
    except Exception as exc:
        raise ChartGenerationError(
            f"generate_chart failed for {symbol}: {exc}",
            detail=f"symbol={symbol} run_date={run_date}",
        ) from exc
    finally:
        if fig is not None:
            plt.close(fig)


# ---------------------------------------------------------------------------
# Public API — generate_batch_charts()
# ---------------------------------------------------------------------------

def generate_batch_charts(
    results: list[SEPAResult],
    ohlcv_data: dict[str, pd.DataFrame],
    vcp_data: dict[str, VCPMetrics],
    output_dir: str,
    run_date: date,
    min_quality: str = "B",
    watchlist_symbols: list[str] = None,
) -> dict[str, str]:
    """Generate charts for all results meeting the quality threshold.

    Watchlist symbols always get a chart regardless of their quality grade.
    Symbols that fail to render are logged and skipped — this function
    never raises.

    Parameters
    ----------
    results:
        All SEPAResult objects from the screening run.
    ohlcv_data:
        Mapping of symbol → OHLCV DataFrame.
    vcp_data:
        Mapping of symbol → VCPMetrics (missing keys are treated as None).
    output_dir:
        Root output directory (``{output_dir}/charts/`` will be created).
    run_date:
        Date of the run.
    min_quality:
        Minimum quality grade to chart.  "B" includes A+, A, and B.
        Use "FAIL" to chart everything.
    watchlist_symbols:
        Symbols to always chart regardless of quality.

    Returns
    -------
    dict[str, str]
        ``{symbol: file_path}`` for every chart successfully written.
    """
    wl_set: set[str] = set(watchlist_symbols or [])
    generated: dict[str, str] = {}

    for result in results:
        symbol = result.symbol
        force = symbol in wl_set
        qualifies = _meets_quality(result.setup_quality, min_quality)

        if not force and not qualifies:
            log.debug(
                "generate_batch_charts: skipping %s (quality=%s < min=%s)",
                symbol, result.setup_quality, min_quality,
            )
            continue

        ohlcv_df = ohlcv_data.get(symbol)
        if ohlcv_df is None or ohlcv_df.empty:
            log.warning(
                "generate_batch_charts: no OHLCV data for %s — skipping", symbol
            )
            continue

        vcp_metrics = vcp_data.get(symbol)

        try:
            path = generate_chart(
                symbol=symbol,
                ohlcv_df=ohlcv_df,
                result=result,
                vcp_metrics=vcp_metrics,
                output_dir=output_dir,
                run_date=run_date,
            )
            generated[symbol] = path
        except Exception as exc:           # noqa: BLE001
            log.error(
                "generate_batch_charts: failed to chart %s: %s", symbol, exc
            )

    log.info(
        "generate_batch_charts: %d/%d charts generated (min_quality=%s)",
        len(generated), len(results), min_quality,
    )
    return generated
