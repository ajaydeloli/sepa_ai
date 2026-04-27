"""
screener/pipeline.py
---------------------
Full SEPA screening pipeline for a batch of symbols.

Public API
----------
run_screen(universe, run_date, config, symbol_info, benchmark_df, n_workers)
    → list[SEPAResult]

Design constraints
------------------
* Workers are top-level functions (_screen_one) so ProcessPoolExecutor can
  pickle them without issues.
* Workers load at most the last 300 rows of the feature Parquet — never the
  full history.
* No imports from api/, dashboard/, or alerts/.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from features.relative_strength import run_rs_rating_pass, write_rs_ratings_to_features
from features.sector_rs import compute_sector_ranks
from features.vcp import VCPMetrics, get_detector
from rules.entry_trigger import check_entry_trigger
from rules.risk_reward import compute_risk_reward
from rules.scorer import SEPAResult, score_symbol
from rules.stage import detect_stage
from rules.stop_loss import compute_stop_loss
from rules.trend_template import check_trend_template
from rules.vcp_rules import qualify_vcp
from screener.pre_filter import build_features_index, pre_filter
from storage.parquet_store import read_last_n_rows
from utils.logger import get_logger

log = get_logger(__name__)

# Maximum rows loaded per symbol in a worker — NEVER change to full history
_MAX_FEATURE_ROWS: int = 300


# ---------------------------------------------------------------------------
# Worker args container  (plain tuple so it's picklable)
# ---------------------------------------------------------------------------
# args layout:
#   (symbol, run_date, config, rs_ratings, sector_ranks, symbol_info_records)
#
# symbol_info_records is a list[dict] (JSON-serialisable) so it survives
# pickle across process boundaries without pandas version mismatches.


def _screen_one(args: tuple) -> SEPAResult:
    """Process a single symbol through the full rule engine.

    This is a TOP-LEVEL function — required for ProcessPoolExecutor pickling.
    It must NOT be a lambda, closure, or instance method.

    Parameters (packed into *args*)
    --------------------------------
    symbol          : str
    run_date        : date
    config          : dict
    rs_ratings      : dict[str, int]   — pre-computed for full universe
    sector_ranks    : dict[str, int]   — pre-computed sector rankings
    symbol_info_rec : list[dict]       — records of symbol_info DataFrame
    """
    (symbol, run_date, config, rs_ratings, sector_ranks, symbol_info_records) = args

    # Reconstruct symbol_info DataFrame inside the worker
    symbol_info = pd.DataFrame(symbol_info_records)

    features_dir = Path(config["data"]["features_dir"])
    feat_path    = features_dir / f"{symbol}.parquet"

    # ------------------------------------------------------------------
    # Helper: build a FAIL result without raising
    # ------------------------------------------------------------------
    def _fail(reason: str) -> SEPAResult:
        from rules.stage import StageResult
        from rules.trend_template import TrendTemplateResult
        stage_fail = StageResult(
            stage=0, label="FAIL", confidence=0,
            reason=reason, ma_slope_200=0.0, ma_slope_50=0.0, is_buyable=False,
        )
        tt_fail = TrendTemplateResult(
            passes=False, conditions_met=0,
            condition_1=False, condition_2=False, condition_3=False,
            condition_4=False, condition_5=False, condition_6=False,
            condition_7=False, condition_8=False,
            details={},
        )
        vcp_fail = VCPMetrics(
            contraction_count=0, max_depth_pct=0.0, final_depth_pct=0.0,
            vol_contraction_ratio=float("nan"), base_length_weeks=0,
            base_low=float("nan"), is_valid_vcp=False, tightness_score=float("nan"),
        )
        row_empty = pd.Series({"close": 0.0, "rs_rating": 0})
        return score_symbol(
            symbol=symbol, run_date=run_date, row=row_empty,
            stage_result=stage_fail, tt_result=tt_fail, vcp_metrics=vcp_fail,
            sector_ranks=sector_ranks, symbol_info=symbol_info, config=config,
        )

    try:
        df = read_last_n_rows(feat_path, _MAX_FEATURE_ROWS)
        if df.empty:
            return _fail(f"empty feature file for {symbol}")

        # Inject the cross-symbol rs_rating into the last row so rule engine
        # has the up-to-date rating (write_rs_ratings_to_features may not
        # have run yet when workers start).
        rating = rs_ratings.get(symbol, 0)
        df.loc[df.index[-1], "rs_rating"] = rating

        row: pd.Series = df.iloc[-1]

        # ── Stage gate (hard gate) ─────────────────────────────────────
        stage_result = detect_stage(row, config)
        if stage_result.stage != 2:
            from rules.trend_template import TrendTemplateResult
            tt_fail = TrendTemplateResult(
                passes=False, conditions_met=0,
                condition_1=False, condition_2=False, condition_3=False,
                condition_4=False, condition_5=False, condition_6=False,
                condition_7=False, condition_8=False, details={},
            )
            vcp_fail = VCPMetrics(
                contraction_count=0, max_depth_pct=0.0, final_depth_pct=0.0,
                vol_contraction_ratio=float("nan"), base_length_weeks=0,
                base_low=float("nan"), is_valid_vcp=False,
                tightness_score=float("nan"),
            )
            return score_symbol(
                symbol=symbol, run_date=run_date, row=row,
                stage_result=stage_result, tt_result=tt_fail,
                vcp_metrics=vcp_fail, sector_ranks=sector_ranks,
                symbol_info=symbol_info, config=config,
            )

        # ── Trend template ─────────────────────────────────────────────
        tt_result = check_trend_template(row, config)

        # ── VCP detection — needs OHLCV columns; use the feature df ───
        vcp_metrics = get_detector(config).detect(df, config)

        # ── Score (score_symbol calls vcp_rules, entry, stop, rr) ─────
        return score_symbol(
            symbol=symbol, run_date=run_date, row=row,
            stage_result=stage_result, tt_result=tt_result,
            vcp_metrics=vcp_metrics, sector_ranks=sector_ranks,
            symbol_info=symbol_info, config=config,
        )

    except Exception as exc:  # noqa: BLE001
        log.warning("_screen_one %s: unexpected error (%s)", symbol, exc)
        return _fail(f"error: {exc}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_screen(
    universe: list[str],
    run_date: date,
    config: dict,
    symbol_info: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    n_workers: int = 4,
) -> list[SEPAResult]:
    """Full SEPA screening pipeline.

    Steps
    -----
    0. Build features_index (last-row summary for all symbols).
    1. pre_filter() — eliminates ~70 % of the universe cheaply.
    2. run_rs_rating_pass() — compute cross-symbol RS ratings.
    3. write_rs_ratings_to_features() — persist ratings into feature Parquet files.
    4. compute_sector_ranks() — rank sectors by median RS.
    5. Parallel rule engine — one ProcessPoolExecutor worker per symbol.
    6. Sort by score DESC and return.

    Parameters
    ----------
    universe:
        Full list of ticker symbols to consider.
    run_date:
        Date of this screening run.
    config:
        Project configuration dict (parsed from settings.yaml).
    symbol_info:
        DataFrame with columns ``symbol`` and ``sector``.
    benchmark_df:
        Benchmark OHLCV DataFrame (e.g. Nifty 500) for RS computation.
    n_workers:
        Number of parallel ProcessPoolExecutor workers.

    Returns
    -------
    list[SEPAResult]
        All results (pass and fail), sorted by score DESC.
    """
    t0 = time.perf_counter()
    total = len(universe)
    log.info("run_screen: starting for %d symbols on %s", total, run_date)

    # ── Step 0: Build features index ──────────────────────────────────────
    log.info("run_screen: Step 0 — building features index")
    features_index = build_features_index(universe, config)

    # ── Step 1: Pre-filter ────────────────────────────────────────────────
    log.info("run_screen: Step 1 — pre_filter")
    passed_symbols: list[str] = pre_filter(features_index, config)
    n_passed = len(passed_symbols)
    log.info(
        "run_screen: pre_filter passed %d/%d symbols", n_passed, total
    )

    # ── Step 2: RS rating pass (cross-symbol) ─────────────────────────────
    log.info("run_screen: Step 2 — run_rs_rating_pass")
    rs_ratings: dict[str, int] = run_rs_rating_pass(
        universe, run_date, config, benchmark_df
    )

    # ── Step 3: Write RS ratings back to feature Parquet files ────────────
    log.info("run_screen: Step 3 — write_rs_ratings_to_features")
    write_rs_ratings_to_features(rs_ratings, config)

    # ── Step 4: Compute sector ranks ──────────────────────────────────────
    log.info("run_screen: Step 4 — compute_sector_ranks")
    sector_ranks: dict[str, int] = compute_sector_ranks(rs_ratings, symbol_info)

    # ── Step 5: Parallel rule engine ──────────────────────────────────────
    log.info(
        "run_screen: Step 5 — rule engine for %d symbols (%d workers)",
        n_passed, n_workers,
    )

    # Serialise symbol_info as records so the worker can reconstruct the
    # DataFrame without pickle issues across Python versions.
    symbol_info_records: list[dict] = symbol_info.to_dict(orient="records")

    worker_args = [
        (sym, run_date, config, rs_ratings, sector_ranks, symbol_info_records)
        for sym in passed_symbols
    ]

    results: list[SEPAResult] = []
    n_stage2 = 0
    n_done   = 0

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        future_to_sym = {
            executor.submit(_screen_one, args): args[0]
            for args in worker_args
        }
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                result: SEPAResult = future.result()
                results.append(result)
                if result.stage == 2:
                    n_stage2 += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("run_screen: worker for %s raised (%s)", sym, exc)
            n_done += 1
            if n_done % 50 == 0 or n_done == n_passed:
                log.info(
                    "screened %d/%d: %d passed pre_filter, %d in Stage 2",
                    n_done, total, n_passed, n_stage2,
                )

    # ── Step 6: Sort by score DESC ────────────────────────────────────────
    results.sort(key=lambda r: r.score, reverse=True)

    elapsed = time.perf_counter() - t0
    log.info(
        "run_screen completed in %.1fs for %d symbols", elapsed, total
    )
    return results
