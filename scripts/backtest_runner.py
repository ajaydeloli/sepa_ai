#!/usr/bin/env python
"""
scripts/backtest_runner.py
--------------------------
CLI runner for the SEPA AI walk-forward backtesting engine.

Usage examples
--------------
    python scripts/backtest_runner.py \\
        --start 2019-01-01 --end 2024-01-01 \\
        --universe nifty500 --trailing-stop 0.07

    python scripts/backtest_runner.py \\
        --start 2019-01-01 --end 2024-01-01 \\
        --universe nifty500 --trailing-stop 0.07 --compare

    python scripts/backtest_runner.py \\
        --start 2020-01-01 --end 2023-01-01 \\
        --no-trailing --output reports/fixed_only/

    python scripts/backtest_runner.py \\
        --start 2019-01-01 --end 2024-01-01 \\
        --config path/to/override.yaml

Arguments
---------
--start          DATE   Backtest start date (YYYY-MM-DD).  Required.
--end            DATE   Backtest end date   (YYYY-MM-DD).  Required.
--universe       STR    "nifty500" | "nse_all" | path to symbols CSV.
--trailing-stop  FLOAT  Trailing stop pct (e.g. 0.07 for 7%).
--no-trailing           Disable trailing stop (use fixed stop only).
--compare               Run BOTH trailing and fixed; include comparison section.
--output         DIR    Output directory.  Default: reports/
--config         FILE   Path to settings.yaml override.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.engine import BacktestResult, run_backtest          # noqa: E402
from backtest.metrics import compute_metrics                       # noqa: E402
from backtest.report import generate_report                        # noqa: E402
from ingestion.universe_loader import resolve_symbols              # noqa: E402
from utils.logger import get_logger                                # noqa: E402

log = get_logger(__name__)

_DEFAULT_CONFIG = _ROOT / "config" / "settings.yaml"
_DEFAULT_OUTPUT = str(_ROOT / "reports")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest_runner.py",
        description="SEPA AI — Walk-forward backtesting CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--start", required=True, metavar="DATE")
    p.add_argument("--end",   required=True, metavar="DATE")
    p.add_argument("--universe", default="nifty500", metavar="STR")
    p.add_argument("--trailing-stop", type=float, default=None, metavar="FLOAT")
    p.add_argument("--no-trailing", action="store_true")
    p.add_argument("--compare",     action="store_true")
    p.add_argument("--output", default=_DEFAULT_OUTPUT, metavar="DIR")
    p.add_argument("--config", default=str(_DEFAULT_CONFIG), metavar="FILE")
    return p


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        log.error("Config file not found: %s", config_path)
        sys.exit(1)
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    log.info("Loaded config: %s", config_path)
    return cfg


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------

def _resolve_universe(universe_arg: str, config: dict) -> list[str]:
    universe_lower = universe_arg.lower()
    if universe_lower in ("nifty500", "nse_all"):
        override = dict(config)
        override.setdefault("universe", {})["index"] = universe_lower
        try:
            symbols = resolve_symbols(override)
            log.info("Resolved universe '%s': %d symbols", universe_arg, len(symbols))
            return symbols
        except Exception as exc:  # noqa: BLE001
            log.error("resolve_symbols failed for '%s': %s", universe_arg, exc)
            sys.exit(1)
    csv_path = Path(universe_arg)
    if csv_path.exists():
        import csv as _csv
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = _csv.reader(fh)
            symbols = [row[0].strip() for row in reader if row and row[0].strip()]
        log.info("Loaded %d symbols from %s", len(symbols), csv_path)
        return symbols
    log.error("Unknown universe value '%s'.", universe_arg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Console printers
# ---------------------------------------------------------------------------

def _print_summary_table(label: str, metrics: dict) -> None:
    inf_pf = metrics.get("profit_factor", 0)
    pf_str = "∞" if inf_pf == float("inf") else f"{inf_pf:.2f}"
    print(
        f"\n{'─'*60}\n  {label}\n{'─'*60}\n"
        f"  CAGR          : {metrics.get('cagr', 0)*100:.2f}%\n"
        f"  Total Return  : {metrics.get('total_return_pct', 0):.2f}%\n"
        f"  Sharpe        : {metrics.get('sharpe_ratio', 0):.2f}\n"
        f"  Max Drawdown  : {metrics.get('max_drawdown_pct', 0):.2f}%\n"
        f"  Win Rate      : {metrics.get('win_rate', 0)*100:.1f}%\n"
        f"  Profit Factor : {pf_str}\n"
        f"  Total Trades  : {metrics.get('total_trades', 0)}\n"
        f"  Avg R-Multiple: {metrics.get('avg_r_multiple', 0):.2f} R\n"
        f"{'─'*60}"
    )


def _print_comparison_table(trailing_metrics: dict, fixed_metrics: dict) -> None:
    rows = [
        ("CAGR",     f"{trailing_metrics.get('cagr',0)*100:.2f}%",
                     f"{fixed_metrics.get('cagr',0)*100:.2f}%"),
        ("Sharpe",   f"{trailing_metrics.get('sharpe_ratio',0):.2f}",
                     f"{fixed_metrics.get('sharpe_ratio',0):.2f}"),
        ("Max DD",   f"{trailing_metrics.get('max_drawdown_pct',0):.2f}%",
                     f"{fixed_metrics.get('max_drawdown_pct',0):.2f}%"),
        ("Win Rate", f"{trailing_metrics.get('win_rate',0)*100:.1f}%",
                     f"{fixed_metrics.get('win_rate',0)*100:.1f}%"),
        ("Trades",   str(trailing_metrics.get("total_trades", 0)),
                     str(fixed_metrics.get("total_trades", 0))),
    ]
    col_w = [20, 18, 18]
    print(f"\n{'═'*58}\n  Trailing vs Fixed Stop — Comparison\n{'═'*58}")
    print("  " + "  ".join(h.ljust(col_w[i]) for i, h in
                            enumerate(["Metric", "Trailing Stop", "Fixed Stop"])))
    print(f"  {'─'*54}")
    for row in rows:
        print("  " + "  ".join(row[i].ljust(col_w[i]) for i in range(len(row))))
    print(f"{'═'*58}\n")


# ---------------------------------------------------------------------------
# Single backtest pass helper
# ---------------------------------------------------------------------------

def _initial_capital(config: dict) -> float:
    return float(config.get("paper_trading", {}).get("initial_capital", 100_000))


def _run_single(
    start: date, end: date, config: dict, universe: list[str],
    symbol_info, benchmark_df, trailing_stop_pct: float | None, label: str,
) -> tuple[BacktestResult, dict, list[dict]]:
    """Execute one backtest pass; return (result, metrics, equity_curve)."""
    log.info("Running backtest: %s [%s → %s] tsp=%s", label, start, end, trailing_stop_pct)
    result = run_backtest(
        start_date=start, end_date=end, config=config, universe=universe,
        symbol_info=symbol_info, benchmark_df=benchmark_df,
        trailing_stop_pct=trailing_stop_pct,
    )
    equity_curve: list[dict] = []
    metrics = compute_metrics(result.trades, equity_curve, _initial_capital(config))
    return result, metrics, equity_curve


# ---------------------------------------------------------------------------
# Parameter sweep — public API
# ---------------------------------------------------------------------------

def run_parameter_sweep(
    base_config: dict,
    start: date,
    end: date,
    universe: list[str],
    trailing_pcts: list[float] | None = None,
    symbol_info: pd.DataFrame | None = None,
    benchmark_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Run a backtest for each *trailing_stop_pct* value and tabulate results.

    For every value in *trailing_pcts* a full walk-forward backtest is
    executed via ``_run_single``.  Results are collected into a tidy
    DataFrame and printed to stdout as a formatted comparison table.

    Parameters
    ----------
    base_config:
        Project config dict (same structure as settings.yaml).
    start, end:
        Backtest date range.
    universe:
        List of symbol strings to screen and trade.
    trailing_pcts:
        Trailing-stop percentages to sweep.  Defaults to
        ``[0.05, 0.07, 0.10, 0.15]`` when *None*.
    symbol_info:
        Optional symbol-metadata DataFrame.  A minimal DataFrame is
        synthesised from *universe* when not supplied.
    benchmark_df:
        Optional benchmark OHLCV DataFrame.  An empty DataFrame is
        used when not supplied (regime = "Unknown" for all trades).

    Returns
    -------
    pd.DataFrame
        Columns: ``trailing_stop_pct``, ``cagr``, ``sharpe``,
        ``max_drawdown``, ``win_rate``, ``total_trades``.
        One row per *trailing_pcts* entry, in input order.
    """
    if trailing_pcts is None:
        trailing_pcts = [0.05, 0.07, 0.10, 0.15]
    if symbol_info is None:
        symbol_info = pd.DataFrame({"symbol": universe}).set_index("symbol")
    if benchmark_df is None:
        benchmark_df = pd.DataFrame()

    rows: list[dict] = []
    for pct in trailing_pcts:
        log.info("Parameter sweep: tsp=%.2f%% [%s → %s]", pct * 100, start, end)
        _, metrics, _ = _run_single(
            start=start, end=end, config=base_config, universe=universe,
            symbol_info=symbol_info, benchmark_df=benchmark_df,
            trailing_stop_pct=pct, label=f"TSP={pct:.0%}",
        )
        rows.append({
            "trailing_stop_pct": pct,
            "cagr":              metrics["cagr"],
            "sharpe":            metrics["sharpe_ratio"],
            "max_drawdown":      metrics["max_drawdown_pct"],
            "win_rate":          metrics["win_rate"],
            "total_trades":      int(metrics["total_trades"]),
        })

    df = pd.DataFrame(rows)
    _print_sweep_table(df)
    return df


def _print_sweep_table(df: pd.DataFrame) -> None:
    """Print a formatted parameter-sweep table to stdout."""
    print("\n" + "═" * 60)
    print("  Parameter Sweep — Trailing Stop Sensitivity")
    print("═" * 60)
    print(f"  {'TSP':>6}  {'CAGR':>8}  {'Sharpe':>7}  "
          f"{'MaxDD%':>7}  {'WinRate':>8}  {'Trades':>7}")
    print("  " + "─" * 56)
    for _, row in df.iterrows():
        print(
            f"  {row['trailing_stop_pct']*100:>5.1f}%"
            f"  {row['cagr']*100:>7.2f}%"
            f"  {row['sharpe']:>7.2f}"
            f"  {row['max_drawdown']:>6.2f}%"
            f"  {row['win_rate']*100:>7.1f}%"
            f"  {int(row['total_trades']):>7d}"
        )
    print("═" * 60 + "\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end   = datetime.strptime(args.end,   "%Y-%m-%d").date()
    except ValueError as exc:
        parser.error(f"Invalid date format: {exc}")

    if start >= end:
        parser.error("--start must be before --end")

    config = _load_config(args.config)

    default_tsp = float(config.get("backtest", {}).get("trailing_stop_pct", 0.07))
    if args.no_trailing and args.compare:
        parser.error("--no-trailing and --compare are mutually exclusive.")

    trailing_stop_pct: float | None
    if args.no_trailing:
        trailing_stop_pct = None
    else:
        trailing_stop_pct = args.trailing_stop if args.trailing_stop is not None else default_tsp

    log.info("Resolving universe: %s", args.universe)
    universe = _resolve_universe(args.universe, config)
    if not universe:
        log.error("Universe is empty — cannot run backtest.")
        sys.exit(1)

    try:
        from storage.parquet_store import read_last_n_rows
        symbol_info = pd.DataFrame({"symbol": universe}).set_index("symbol")
        benchmark_path = (
            Path(config.get("data", {}).get("features_dir", "data/features"))
            / "NIFTY50.parquet"
        )
        if benchmark_path.exists():
            benchmark_df = read_last_n_rows(benchmark_path, 2000)
        else:
            log.warning("Benchmark parquet not found at %s; using empty DataFrame.", benchmark_path)
            benchmark_df = pd.DataFrame()
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to load supporting data: %s", exc)
        sys.exit(1)

    output_dir = args.output

    if args.compare:
        result_t, metrics_t, ec_t = _run_single(
            start, end, config, universe, symbol_info, benchmark_df,
            trailing_stop_pct=default_tsp if args.trailing_stop is None else args.trailing_stop,
            label="Trailing Stop",
        )
        _print_summary_table("Trailing Stop", metrics_t)

        result_f, metrics_f, ec_f = _run_single(
            start, end, config, universe, symbol_info, benchmark_df,
            trailing_stop_pct=None, label="Fixed Stop",
        )
        _print_summary_table("Fixed Stop", metrics_f)
        _print_comparison_table(metrics_t, metrics_f)

        html_path, csv_path = generate_report(
            result=result_t, metrics=metrics_t, output_dir=output_dir,
            equity_curve=ec_t, trailing_metrics=metrics_t, fixed_metrics=metrics_f,
        )
    else:
        result, metrics, ec = _run_single(
            start, end, config, universe, symbol_info, benchmark_df,
            trailing_stop_pct=trailing_stop_pct,
            label="Fixed Stop" if args.no_trailing else "Trailing Stop",
        )
        _print_summary_table(
            "Fixed Stop" if args.no_trailing else "Trailing Stop", metrics,
        )
        html_path, csv_path = generate_report(
            result=result, metrics=metrics, output_dir=output_dir, equity_curve=ec,
        )

    print(f"\n✅  Report written:\n    HTML → {html_path}\n    CSV  → {csv_path}\n")


if __name__ == "__main__":
    main()
