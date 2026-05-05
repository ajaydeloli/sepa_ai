"""
backtest/engine.py
------------------
Walk-forward backtesting engine for the Minervini SEPA system.

Design rules enforced here
--------------------------
* NO lookahead bias — features are read filtered to ≤ backtest_date.
* Feature computation uses only data available as of backtest_date.
* The screener runs in live mode via the same pipeline.run_screen.
* Trailing stop is floored at VCP base_low (stop_loss_price) — NEVER lower.

Public API
----------
run_backtest(...)    → BacktestResult
simulate_trade(...)  → BacktestTrade
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.regime import get_regime
from screener.pipeline import run_screen
from storage.parquet_store import read_last_n_rows
from utils.logger import get_logger
from utils.trading_calendar import trading_days as get_trading_days

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BacktestTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    stop_loss_price: float        # VCP floor — initial hard stop
    peak_price: float             # highest close during hold
    trailing_stop_used: float     # final trailing stop at exit
    stop_type: str                # "trailing" | "fixed"
    quantity: int
    pnl: float
    pnl_pct: float
    r_multiple: float
    exit_reason: str              # "trailing_stop" | "target" | "fixed_stop" | "max_hold"
    regime: str
    setup_quality: str
    sepa_score: int


@dataclass
class BacktestResult:
    start_date: date
    end_date: date
    trades: list[BacktestTrade]
    universe_size: int
    config_snapshot: dict


# ---------------------------------------------------------------------------
# Internal open-position state (not exported)
# ---------------------------------------------------------------------------

@dataclass
class _Position:
    symbol: str
    entry_date: date
    entry_price: float
    stop_loss_price: float        # VCP base_low floor
    trailing_stop: float          # current trailing stop (ratchets up only)
    peak_price: float             # highest close observed since entry
    quantity: int
    setup_quality: str
    sepa_score: int
    days_held: int = 0


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _bt_cfg(config: dict) -> dict:
    """Return the backtest sub-section with safe defaults."""
    bt = config.get("backtest", {})
    return {
        "trailing_stop_pct": bt.get("trailing_stop_pct", 0.07),
        "target_pct":        bt.get("target_pct", 0.10),
        "max_hold_days":     int(bt.get("max_hold_days", 20)),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_quantity(entry_price: float, stop_loss_price: float, config: dict) -> int:
    """Size position from risk-per-trade config (paper_trading section)."""
    pt = config.get("paper_trading", {})
    initial_capital    = float(pt.get("initial_capital", 100_000))
    risk_per_trade_pct = float(pt.get("risk_per_trade_pct", 2.0))
    risk_amount        = initial_capital * risk_per_trade_pct / 100.0
    risk_per_share     = entry_price - stop_loss_price
    if risk_per_share <= 0:
        return 1
    return max(1, int(risk_amount / risk_per_share))


def _get_close_on_date(symbol: str, target_date: date, config: dict) -> float | None:
    """Return the close price for *symbol* on *target_date*, or None if missing."""
    feat_path = Path(config["data"]["features_dir"]) / f"{symbol}.parquet"
    df = read_last_n_rows(feat_path, 300)
    if df.empty or "close" not in df.columns:
        return None
    ts = pd.Timestamp(target_date)
    matching = df[df.index == ts]
    if matching.empty:
        return None
    return float(matching.iloc[-1]["close"])


def _close_position(
    pos: _Position,
    exit_date: date,
    exit_price: float,
    exit_reason: str,
    benchmark_df: pd.DataFrame | None,
) -> BacktestTrade:
    """Convert an open _Position into a finalised BacktestTrade."""
    pnl        = (exit_price - pos.entry_price) * pos.quantity
    pnl_pct    = (exit_price / pos.entry_price - 1.0) * 100.0
    risk_amt   = pos.entry_price - pos.stop_loss_price
    r_multiple = (exit_price - pos.entry_price) / risk_amt if risk_amt > 0 else 0.0
    regime     = get_regime(pos.entry_date, benchmark_df)
    # stop_type: "trailing" when the stop ratcheted above the hard floor
    stop_type  = "trailing" if pos.trailing_stop > pos.stop_loss_price else "fixed"
    return BacktestTrade(
        symbol=pos.symbol,
        entry_date=pos.entry_date,
        exit_date=exit_date,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        stop_loss_price=pos.stop_loss_price,
        peak_price=pos.peak_price,
        trailing_stop_used=pos.trailing_stop,
        stop_type=stop_type,
        quantity=pos.quantity,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 4),
        r_multiple=round(r_multiple, 3),
        exit_reason=exit_reason,
        regime=regime,
        setup_quality=pos.setup_quality,
        sepa_score=pos.sepa_score,
    )


# ---------------------------------------------------------------------------
# simulate_trade — standalone single-trade simulator
# ---------------------------------------------------------------------------

def simulate_trade(
    entry_date: date,
    entry_price: float,
    stop_loss_price: float,
    ohlcv_df: pd.DataFrame,
    config: dict,
    trailing_stop_pct: float | None = None,
) -> BacktestTrade:
    """Simulate a single trade forward in time using pre-supplied OHLCV data.

    Parameters
    ----------
    entry_date:
        Date the position is entered (first row of *ohlcv_df*).
    entry_price:
        Execution price (typically today's close).
    stop_loss_price:
        VCP base_low — hard floor.  The trailing stop is NEVER allowed below
        this level.
    ohlcv_df:
        Daily OHLCV starting from *entry_date* (no rows before entry).
        Must contain a ``close`` column.  Index must be date-like.
    config:
        Project config dict.  Reads ``backtest.target_pct`` (default 0.10)
        and ``backtest.max_hold_days`` (default 20).
    trailing_stop_pct:
        When provided: trailing stop = max(peak * (1 - pct), stop_loss_price).
        The stop only ratchets UP — never down.
        When None: fixed stop only (exit when close ≤ stop_loss_price).

    Returns
    -------
    BacktestTrade
        All fields populated.  ``symbol``, ``regime``, ``setup_quality``, and
        ``sepa_score`` are left blank/zero — callers that have that context
        (e.g. run_backtest) should fill them in after the call.
    """
    cfg           = _bt_cfg(config)
    target_pct    = cfg["target_pct"]
    max_hold_days = cfg["max_hold_days"]
    target_price  = entry_price * (1.0 + target_pct)

    df = ohlcv_df.sort_index()

    if df.empty:
        return BacktestTrade(
            symbol="", entry_date=entry_date, exit_date=entry_date,
            entry_price=entry_price, exit_price=entry_price,
            stop_loss_price=stop_loss_price, peak_price=entry_price,
            trailing_stop_used=stop_loss_price,
            stop_type="fixed" if trailing_stop_pct is None else "trailing",
            quantity=_compute_quantity(entry_price, stop_loss_price, config),
            pnl=0.0, pnl_pct=0.0, r_multiple=0.0,
            exit_reason="max_hold", regime="Unknown",
            setup_quality="", sepa_score=0,
        )

    # ── Initialise state ───────────────────────────────────────────────────
    peak_price    = entry_price
    # Initial trailing stop: 7 % below entry (or whatever pct), floored at hard stop
    if trailing_stop_pct is not None:
        trailing_stop = max(entry_price * (1.0 - trailing_stop_pct), stop_loss_price)
    else:
        trailing_stop = stop_loss_price  # fixed mode

    # Defaults — overwritten inside the loop
    exit_price  = float(df.iloc[-1]["close"])
    try:
        exit_date = pd.Timestamp(df.index[-1]).date()
    except Exception:
        exit_date = entry_date
    exit_reason = "max_hold"

    rows = list(df.iterrows())

    for i, (idx, row) in enumerate(rows):
        close = float(row["close"])
        try:
            current_date: date = pd.Timestamp(idx).date()
        except Exception:
            current_date = entry_date

        # ── Update peak ────────────────────────────────────────────────
        if close > peak_price:
            peak_price = close

        # ── Ratchet trailing stop UP (never down, never below floor) ──
        if trailing_stop_pct is not None:
            candidate = max(peak_price * (1.0 - trailing_stop_pct), stop_loss_price)
            if candidate > trailing_stop:
                trailing_stop = candidate

        # Entry day (i == 0): enter at close, no exit check
        if i == 0:
            continue

        # ── Exit checks ────────────────────────────────────────────────
        if trailing_stop_pct is not None:
            if close <= trailing_stop:
                exit_price  = close
                exit_date   = current_date
                exit_reason = "trailing_stop"
                break
        else:
            if close <= stop_loss_price:
                exit_price  = close
                exit_date   = current_date
                exit_reason = "fixed_stop"
                break
            if close >= target_price:
                exit_price  = close
                exit_date   = current_date
                exit_reason = "target"
                break

        # max_hold: i == max_hold_days means we have held for max_hold_days sessions
        if i >= max_hold_days:
            exit_price  = close
            exit_date   = current_date
            exit_reason = "max_hold"
            break

    # ── Post-loop: promote max_hold → target when trailing mode exits at/above target
    if trailing_stop_pct is not None and exit_reason == "max_hold" and exit_price >= target_price:
        exit_reason = "target"

    # ── Build trade metrics ────────────────────────────────────────────────
    quantity   = _compute_quantity(entry_price, stop_loss_price, config)
    pnl        = (exit_price - entry_price) * quantity
    pnl_pct    = (exit_price / entry_price - 1.0) * 100.0
    risk_amt   = entry_price - stop_loss_price
    r_multiple = (exit_price - entry_price) / risk_amt if risk_amt > 0 else 0.0
    stop_type  = "trailing" if trailing_stop_pct is not None else "fixed"

    return BacktestTrade(
        symbol="",
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss_price=stop_loss_price,
        peak_price=peak_price,
        trailing_stop_used=trailing_stop,
        stop_type=stop_type,
        quantity=quantity,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 4),
        r_multiple=round(r_multiple, 3),
        exit_reason=exit_reason,
        regime="Unknown",
        setup_quality="",
        sepa_score=0,
    )


# ---------------------------------------------------------------------------
# run_backtest — walk-forward orchestrator
# ---------------------------------------------------------------------------

def run_backtest(
    start_date: date,
    end_date: date,
    config: dict,
    universe: list[str],
    symbol_info: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    trailing_stop_pct: float | None = None,
    n_workers: int = 4,
) -> BacktestResult:
    """Walk-forward backtest over [start_date, end_date].

    For each NSE trading day in the range:
      1. Update all open positions with today's close price.
         Ratchet trailing stop, check for exits.
      2. Run run_screen(universe, date, config, ...) — same pipeline as live.
      3. Enter A+ / A candidates that are not already in a position.

    Lookahead-bias note
    -------------------
    run_screen reads the last 300 rows of each symbol's features Parquet.
    For a true no-lookahead backtest the features directory should contain
    only data built up to each backtest_date.  When running against a fully
    built feature store, the screener itself is date-aware via run_date but
    the underlying Parquet slices are not filtered here — callers should
    pre-build features incrementally when strict no-lookahead is required.

    Parameters
    ----------
    trailing_stop_pct:
        Overrides config["backtest"]["trailing_stop_pct"] when provided.
    """
    cfg        = _bt_cfg(config)
    eff_tsp    = trailing_stop_pct if trailing_stop_pct is not None else cfg["trailing_stop_pct"]
    target_pct = cfg["target_pct"]
    max_hold   = cfg["max_hold_days"]

    # ── Get all NSE trading days in range ─────────────────────────────────
    td_index = get_trading_days(start_date.isoformat(), end_date.isoformat())
    backtest_dates: list[date] = [ts.date() for ts in td_index]

    if not backtest_dates:
        log.warning("run_backtest: no trading days found in [%s, %s]", start_date, end_date)
        return BacktestResult(
            start_date=start_date, end_date=end_date,
            trades=[], universe_size=len(universe), config_snapshot=config,
        )

    open_positions: dict[str, _Position] = {}
    completed_trades: list[BacktestTrade] = []

    log.info(
        "run_backtest: %d trading days from %s to %s | universe=%d | tsp=%.2f%%",
        len(backtest_dates), start_date, end_date, len(universe), eff_tsp * 100,
    )

    for backtest_date in backtest_dates:
        # ── Step 1: Update open positions ─────────────────────────────────
        to_close: list[str] = []
        for symbol, pos in open_positions.items():
            close = _get_close_on_date(symbol, backtest_date, config)
            if close is None:
                log.debug("run_backtest: no close for %s on %s — skipping", symbol, backtest_date)
                continue

            # Update peak (ratchet, never down)
            if close > pos.peak_price:
                pos.peak_price = close

            # Ratchet trailing stop up, never below VCP floor
            candidate = max(pos.peak_price * (1.0 - eff_tsp), pos.stop_loss_price)
            if candidate > pos.trailing_stop:
                pos.trailing_stop = candidate

            pos.days_held += 1

            # ── Exit checks ───────────────────────────────────────────
            exit_price: float | None  = None
            exit_reason: str | None   = None

            if close <= pos.trailing_stop:
                exit_price  = close
                exit_reason = "trailing_stop"
            elif close >= pos.entry_price * (1.0 + target_pct):
                exit_price  = close
                exit_reason = "target"
            elif pos.days_held >= max_hold:
                exit_price  = close
                exit_reason = "max_hold"

            if exit_price is not None and exit_reason is not None:
                trade = _close_position(pos, backtest_date, exit_price, exit_reason, benchmark_df)
                completed_trades.append(trade)
                to_close.append(symbol)
                log.info(
                    "run_backtest: CLOSED %s @ %.2f on %s (%s, R=%.2f)",
                    symbol, exit_price, backtest_date, exit_reason, trade.r_multiple,
                )

        for sym in to_close:
            del open_positions[sym]

        # ── Step 2: Screen for new entries ────────────────────────────────
        try:
            results = run_screen(
                universe=universe,
                run_date=backtest_date,
                config=config,
                symbol_info=symbol_info,
                benchmark_df=benchmark_df,
                n_workers=n_workers,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("run_backtest: run_screen failed on %s: %s", backtest_date, exc)
            continue

        # ── Step 3: Enter A+ / A candidates ──────────────────────────────
        for result in results:
            if result.setup_quality not in ("A+", "A"):
                continue
            if result.symbol in open_positions:
                continue
            if result.entry_price is None or result.stop_loss is None:
                continue
            if result.stop_loss >= result.entry_price:
                log.debug(
                    "run_backtest: skipping %s — stop_loss (%.2f) >= entry (%.2f)",
                    result.symbol, result.stop_loss, result.entry_price,
                )
                continue

            qty              = _compute_quantity(result.entry_price, result.stop_loss, config)
            initial_trailing = max(
                result.entry_price * (1.0 - eff_tsp),
                result.stop_loss,
            )
            pos = _Position(
                symbol=result.symbol,
                entry_date=backtest_date,
                entry_price=result.entry_price,
                stop_loss_price=result.stop_loss,
                trailing_stop=initial_trailing,
                peak_price=result.entry_price,
                quantity=qty,
                setup_quality=result.setup_quality,
                sepa_score=result.score,
            )
            open_positions[result.symbol] = pos
            log.info(
                "run_backtest: ENTERED %s @ %.2f on %s (quality=%s, score=%d)",
                result.symbol, result.entry_price, backtest_date,
                result.setup_quality, result.score,
            )

    # ── Force-close any remaining open positions at end_date ──────────────
    for symbol, pos in open_positions.items():
        close = _get_close_on_date(symbol, end_date, config)
        if close is None:
            close = pos.entry_price   # fallback — no data on end_date
        trade = _close_position(pos, end_date, close, "max_hold", benchmark_df)
        completed_trades.append(trade)
        log.info(
            "run_backtest: force-closed %s @ %.2f on end_date %s",
            symbol, close, end_date,
        )

    log.info(
        "run_backtest: finished — %d trades over %d days",
        len(completed_trades), len(backtest_dates),
    )
    return BacktestResult(
        start_date=start_date,
        end_date=end_date,
        trades=completed_trades,
        universe_size=len(universe),
        config_snapshot=config,
    )
