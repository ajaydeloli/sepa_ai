# Phase 2 — Feature Engineering: Step-by-Step Session Prompts
# Minervini SEPA Stock Analysis System
# Use each prompt block in a fresh Claude session.
# Always share the relevant existing files listed under "Context files" with Claude before pasting the prompt.

---

## STEP 1 of 9 — `features/moving_averages.py`

### Context files to attach
- `PROJECT_DESIGN.md` (section 4.2 and 6 for feature interface)
- `utils/exceptions.py`
- `utils/logger.py`
- `storage/parquet_store.py` (for reference on patterns only)

### Prompt

```
You are building a feature module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `features/moving_averages.py` from scratch.

--- EXISTING CODE PATTERNS TO FOLLOW ---

All modules use:
  from __future__ import annotations
  from utils.logger import get_logger
  log = get_logger(__name__)

Exception types (from utils/exceptions.py):
  InsufficientDataError(message, required=int, available=int, detail="")

--- INTERFACE CONTRACT ---

Every feature module is a PURE FUNCTION — no side effects, no I/O, no global state:

  def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame

- Input:  cleaned OHLCV DataFrame with DatetimeIndex, columns: open/high/low/close/volume
- Output: same DataFrame with new indicator columns APPENDED (do not drop existing columns)
- Raises InsufficientDataError if len(df) < minimum rows needed for the longest window (200)

--- WHAT TO IMPLEMENT ---

def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Appends these columns to df (all float64, NaN for insufficient history):
      sma_10, sma_21, sma_50, sma_150, sma_200  — simple moving averages
      ema_21                                      — exponential moving average
      ma_slope_50                                 — linear regression slope of SMA_50 over last N days
      ma_slope_200                                — linear regression slope of SMA_200 over last N days

    config keys used:
      config.get("stage", {}).get("ma200_slope_lookback", 20)   → N days for SMA200 slope
      config.get("stage", {}).get("ma50_slope_lookback", 10)    → N days for SMA50 slope

    Slope is computed as the coefficient of a 1D linear regression
    (numpy polyfit degree=1) over the last N values of the MA series.
    Returns the slope per bar (not annualised). Positive = trending up.

    SMA_150 MUST be computed from exactly 150 rows — no approximation.
    If len(df) < 200, raise InsufficientDataError(required=200, available=len(df)).
    """

--- UNIT TESTS ---

Create `tests/unit/test_moving_averages.py` with these tests:
1. Happy path: df with 250 rows → all 8 columns present, no NaN in last row
2. SMA_50 matches pandas rolling(50).mean() exactly (assert_series_equal)
3. EMA_21 matches pandas ewm(span=21, adjust=False).mean() exactly
4. slope_200 is positive when price is trending up (monotonically increasing close)
5. InsufficientDataError raised when len(df) < 200
6. Output has all original columns + exactly 8 new ones (no column duplication)

Use this helper in tests (consistent with existing test style):
  def _make_ohlcv(n=250, trend="flat"):
      # flat: random walk. up: +0.5/day drift. down: -0.5/day drift.
      ...
      return pd.DataFrame({...}, index=pd.bdate_range("2020-01-01", periods=n))

--- ANTI-PATTERNS TO AVOID ---
- Do NOT use TA-Lib (C dependency)
- Do NOT use SMA_150 as an approximation of another period
- Do NOT store state or write files
- Do NOT import from ingestion/ or storage/ modules
```


---

## STEP 2 of 9 — `features/atr.py` + `features/volume.py`

### Context files to attach
- `features/moving_averages.py` (just built — shows the pure-function pattern)
- `utils/exceptions.py`
- `utils/logger.py`

### Prompt

```
You are building feature modules for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement TWO files: `features/atr.py` and `features/volume.py`.

--- INTERFACE CONTRACT (same as all feature modules) ---

  def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame

- Pure function. Input = OHLCV DataFrame with DatetimeIndex.
- Output = same DataFrame with new indicator columns appended.
- Raise InsufficientDataError if insufficient rows.
- from __future__ import annotations
- from utils.logger import get_logger
- from utils.exceptions import InsufficientDataError

--- FILE 1: features/atr.py ---

Append these columns:
  atr_14      — Average True Range over 14 periods
               True Range = max(high-low, |high-prev_close|, |low-prev_close|)
               ATR = Wilder's smoothing (EWM with alpha=1/14, adjust=False)
  atr_pct     — atr_14 / close * 100  (ATR as % of closing price)

config key: config.get("atr", {}).get("period", 14) → default 14
Raise InsufficientDataError if len(df) < 20 (need at least 14 rows + buffer).

--- FILE 2: features/volume.py ---

Append these columns:
  vol_50d_avg      — simple 50-day rolling average of volume
  vol_ratio        — volume / vol_50d_avg  (today's vol relative to 50-day avg)
  up_vol_days      — count of days in last 20 where close > prev_close AND volume > vol_50d_avg
  down_vol_days    — count of days in last 20 where close < prev_close AND volume > vol_50d_avg
  acc_dist_score   — up_vol_days - down_vol_days  (positive = accumulation, negative = distribution)

config key: config.get("volume", {}).get("avg_period", 50)    → default 50
            config.get("volume", {}).get("lookback_days", 20) → default 20
Raise InsufficientDataError if len(df) < 55 (50-day avg + buffer).

--- UNIT TESTS ---

Create `tests/unit/test_atr.py`:
1. ATR_14 is always positive
2. ATR_PCT = atr_14 / close * 100 within float tolerance
3. ATR_14 matches manual Wilder's smoothing on a 5-row toy DataFrame
4. InsufficientDataError when len(df) < 20

Create `tests/unit/test_volume.py`:
1. vol_ratio == 1.0 when volume equals its 50-day average (within tolerance)
2. acc_dist_score == up_vol_days - down_vol_days
3. acc_dist_score is positive on a rising DataFrame where up-volume dominates
4. InsufficientDataError when len(df) < 55
5. All original OHLCV columns preserved in output

Use pd.bdate_range("2020-01-01", periods=N) for test DataFrames.

--- ANTI-PATTERNS ---
- Do NOT use TA-Lib
- Do NOT drop or rename existing columns
- vol_ratio NaN is acceptable for first 50 rows (rolling window not yet full)
```


---

## STEP 3 of 9 — `features/relative_strength.py` + `features/sector_rs.py`

### Context files to attach
- `features/moving_averages.py` (pure-function pattern)
- `utils/exceptions.py`
- `utils/logger.py`
- `data/metadata/symbol_info.csv` (if it exists; otherwise describe its schema below)

### Prompt

```
You are building feature modules for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `features/relative_strength.py` and `features/sector_rs.py`.

--- FILE 1: features/relative_strength.py ---

PURPOSE: Compute the Minervini RS Rating — how much stronger this stock's
63-day return is vs the Nifty 500 benchmark, expressed as a 0–99 percentile rank
across the universe.

TWO functions are needed:

  def compute_rs_raw(
      symbol_df: pd.DataFrame,
      benchmark_df: pd.DataFrame,
      config: dict,
  ) -> pd.DataFrame:
      """
      Appends ONE column to symbol_df:
        rs_raw  — symbol_63d_return / benchmark_63d_return
                  where return = (close_today / close_63_days_ago) - 1

      config key: config.get("rs", {}).get("period", 63)
      benchmark_df must have a 'close' column and a DatetimeIndex aligned to symbol_df.
      Raise InsufficientDataError if len(symbol_df) < 65 (63 + 2 buffer).
      """

  def compute_rs_rating(
      all_rs_raw: dict[str, float],
  ) -> dict[str, int]:
      """
      Rank all symbols by rs_raw and return a 0–99 percentile score.
      all_rs_raw: { "RELIANCE": 1.23, "TCS": 0.87, ... }
      Returns:    { "RELIANCE": 88, "TCS": 62, ... }

      Use scipy.stats.percentileofscore or pure numpy percentile ranking.
      A symbol at the 88th percentile means it outperformed 88% of the universe.
      """

IMPORTANT: compute_rs_raw() is called per-symbol (appends rs_raw to its df).
           compute_rs_rating() is called ONCE for the whole universe after all
           rs_raw values are collected. It never touches DataFrames — just floats.

--- FILE 2: features/sector_rs.py ---

PURPOSE: Rank sectors by median RS rating of their members. Top-5 sectors
get a +5 point bonus in the scorer (applied externally — this module just
produces the ranking).

  def compute_sector_ranks(
      symbol_rs_ratings: dict[str, int],   # {"RELIANCE": 88, "TCS": 62, ...}
      symbol_info: pd.DataFrame,           # data/metadata/symbol_info.csv
  ) -> dict[str, int]:
      """
      Returns { "sector_name": rank } where rank=1 is the strongest sector.
      Sectors ranked by median RS rating of their member symbols.
      symbol_info must have columns: symbol, sector
      """

  def get_sector_score_bonus(
      symbol: str,
      sector_ranks: dict[str, int],
      symbol_info: pd.DataFrame,
      top_n: int = 5,
  ) -> int:
      """
      Returns +5 if symbol's sector is in the top N sectors, else 0.
      Returns 0 if symbol is not found in symbol_info.
      """

symbol_info.csv schema:
  symbol (str), sector (str), industry (str), market_cap_cr (float), listing_date (str)

--- UNIT TESTS ---

Create `tests/unit/test_relative_strength.py`:
1. rs_raw == 0.0 when symbol and benchmark have identical returns
2. rs_raw > 1.0 when symbol return > benchmark return
3. compute_rs_rating: highest rs_raw maps to rating 99, lowest to 0 (or near)
4. compute_rs_rating output values are all integers in [0, 99]
5. InsufficientDataError when len(df) < 65

Create `tests/unit/test_sector_rs.py`:
1. Sector with highest median RS rating gets rank=1
2. get_sector_score_bonus returns 5 for a top-5 sector symbol
3. get_sector_score_bonus returns 0 for a bottom sector symbol
4. get_sector_score_bonus returns 0 for unknown symbol (no crash)

--- ANTI-PATTERNS ---
- Do NOT hardcode the benchmark ticker — load it from config or pass as a parameter
- Do NOT mix the per-symbol compute_rs_raw with the cross-universe compute_rs_rating
- rs_rating is an int 0-99, NOT a float
```


---

## STEP 4 of 9 — `features/pivot.py`

### Context files to attach
- `features/moving_averages.py` (pure-function pattern)
- `utils/exceptions.py`
- `utils/logger.py`

### Prompt

```
You are building a feature module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `features/pivot.py` — swing high/low pivot detection.

--- PURPOSE ---
Pivots are local price extremes used by VCP detection (Step 5) to identify
contraction legs. The rule engine uses last_pivot_high as the breakout level.

--- INTERFACE ---

  def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
      """
      Appends these columns to df:
        pivot_high        — float: price of the most recent confirmed swing high (NaN if none found)
        pivot_low         — float: price of the most recent confirmed swing low (NaN if none found)
        pivot_high_idx    — int: row offset from end of df where pivot_high occurred (0 = most recent bar)
        pivot_low_idx     — int: row offset from end of df where pivot_low occurred

      config key: config.get("vcp", {}).get("pivot_sensitivity", 5)
                  → N bars on each side that must be lower/higher for a point to qualify as a pivot

      Algorithm (ZigZag / N-bar pivot):
        A swing HIGH at index i is confirmed when:
          high[i] > high[i-N ... i-1]  AND  high[i] > high[i+1 ... i+N]
        A swing LOW at index i is confirmed when:
          low[i] < low[i-N ... i-1]   AND  low[i] < low[i+1 ... i+N]

      Only the MOST RECENT confirmed pivot (high or low) is stored in the row.
      The full list of pivots is NOT stored in the DataFrame — only the latest one.
      """

  def find_all_pivots(
      df: pd.DataFrame,
      sensitivity: int = 5,
  ) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
      """
      Returns (swing_highs, swing_lows) where each item is (row_index, price).
      Used internally by vcp.py for contraction counting.
      This is NOT called by compute() directly — it is a shared utility.
      """

--- UNIT TESTS ---

Create `tests/unit/test_pivot.py`:
1. A clear V-shape (down then up) produces exactly one swing low
2. A clear inverted-V (up then down) produces exactly one swing high
3. Flat price series produces no pivots (no highs or lows qualify)
4. pivot_high_idx is 0 when the last bar IS the most recent swing high
5. pivot_high > pivot_low when both are present
6. All original columns are preserved in output
7. InsufficientDataError if len(df) < 2 * sensitivity + 1 (not enough bars to confirm any pivot)

Construct test DataFrames manually with known pivot positions:
  closes = [100, 105, 110, 105, 100, 95, 100, 105, 110]  # known peaks and troughs

--- IMPORTANT DESIGN NOTES ---
- N-bar confirmation means the pivot is LAGGING by N bars. This is intentional.
  The VCP detector accepts this lag. Do not try to eliminate it.
- Use df["high"] for swing highs and df["low"] for swing lows (not "close").
- The sensitivity parameter (default=5) is configurable — do not hardcode it.
```


---

## STEP 5 of 9 — `features/vcp.py`

### Context files to attach
- `features/pivot.py` (just built — find_all_pivots is imported here)
- `features/moving_averages.py` (pure-function pattern)
- `utils/exceptions.py`
- `utils/logger.py`
- `config/settings.yaml` (vcp section)

### Prompt

```
You are building a feature module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `features/vcp.py` — Volatility Contraction Pattern detection.

--- BACKGROUND ---
A VCP is a base pattern where price contracts in successively smaller waves
(tighter swings, lower volume) before a breakout. The rule engine uses VCPMetrics
to decide if a setup qualifies.

--- ARCHITECTURE: ABSTRACT INTERFACE + RULE-BASED IMPLEMENTATION ---

Implement TWO classes:

  from abc import ABC, abstractmethod
  from dataclasses import dataclass
  from typing import Any

  @dataclass
  class VCPMetrics:
      contraction_count: int         # number of swing-to-swing contractions detected
      max_depth_pct: float           # deepest correction in the base (%)
      final_depth_pct: float         # shallowest / most recent correction (%)
      vol_contraction_ratio: float   # volume in last leg / volume in first leg (< 1 = drying up)
      base_length_weeks: int         # total width of the base in calendar weeks
      base_low: float                # lowest low in the entire base (used as stop-loss floor)
      is_valid_vcp: bool             # True if ALL VCP qualification rules pass
      tightness_score: float         # % range of the last 3 weeks (lower = tighter)


  class VCPDetector(ABC):
      @abstractmethod
      def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics: ...


  class RuleBasedVCPDetector(VCPDetector):
      """
      Default detector. Uses find_all_pivots() from features/pivot.py.

      Algorithm:
      1. Call find_all_pivots(df, sensitivity=config["vcp"]["pivot_sensitivity"])
         to get (swing_highs, swing_lows).
      2. Pair swing highs with subsequent swing lows to form contraction legs.
         Each leg = (swing_high_price - swing_low_price) / swing_high_price * 100
      3. contraction_count = number of complete legs
      4. max_depth_pct = deepest leg's correction %
      5. final_depth_pct = most recent (last) leg's correction %
      6. vol_contraction_ratio:
           last_leg_avg_volume / first_leg_avg_volume
           (average volume within each leg's date range)
      7. base_length_weeks = (last_pivot_date - first_pivot_date).days // 7
      8. base_low = min(df["low"]) over the base range
      9. tightness_score = (max(df["high"]) - min(df["low"])) / min(df["low"]) * 100
         computed over the last 3 calendar weeks of data
      10. is_valid_vcp = apply_vcp_rules(metrics, config)

      VCP qualification rules (from config/settings.yaml vcp section):
        contraction_count >= config["vcp"]["min_contractions"]  (default 2)
        contraction_count <= config["vcp"]["max_contractions"]  (default 5)
        final_depth_pct < max_depth_pct  (each leg shallower — REQUIRED)
        vol_contraction_ratio < 1.0  (volume drying up — if require_vol_contraction=true)
        base_length_weeks >= config["vcp"]["min_weeks"]  (default 3)
        base_length_weeks <= config["vcp"]["max_weeks"]  (default 52)
        tightness_score < config["vcp"]["tightness_pct"]  (default 10.0)
        max_depth_pct <= config["vcp"]["max_depth_pct"]  (default 50.0)
      """
      def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics: ...


ALSO add a module-level factory:

  DETECTORS: dict[str, type[VCPDetector]] = {
      "rule_based": RuleBasedVCPDetector,
  }

  def get_detector(config: dict) -> VCPDetector:
      name = config.get("vcp", {}).get("detector", "rule_based")
      cls = DETECTORS.get(name)
      if cls is None:
          raise ConfigurationError(f"Unknown VCP detector: {name!r}")
      return cls()

ALSO add a top-level compute() that integrates into the feature pipeline:

  def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
      """
      Runs get_detector(config).detect(df, config) and appends VCPMetrics
      fields as columns to df:
        vcp_contraction_count, vcp_max_depth_pct, vcp_final_depth_pct,
        vcp_vol_ratio, vcp_base_length_weeks, vcp_base_low,
        vcp_valid, vcp_tightness_score
      All values are scalar (same value in every row — they describe the whole base).
      Catches exceptions from the detector and fills NaN/False on failure (graceful).
      """

--- UNIT TESTS ---

Create `tests/unit/test_vcp.py`:
1. Synthetic 3-leg VCP (3 swing highs, 3 swing lows, each leg shallower):
   - contraction_count == 3
   - is_valid_vcp == True (when all rules pass)
   - final_depth_pct < max_depth_pct
2. Single-leg base (only 1 contraction): is_valid_vcp == False (min_contractions=2)
3. Non-contracting base (second leg deeper than first): is_valid_vcp == False
4. Volume dry-up: vol_contraction_ratio < 1.0 on a DataFrame with declining volume
5. compute() returns df with all 8 vcp_* columns appended
6. compute() does NOT raise — fills NaN/False when pivot detection finds nothing

--- ANTI-PATTERNS ---
- Do NOT inline the pivot logic — import find_all_pivots from features/pivot.py
- Do NOT make VCPDetector aware of I/O, SQLite, or file paths
- Do NOT hardcode thresholds — everything comes from config dict
```


---

## STEP 6 of 9 — `features/feature_store.py`

### Context files to attach
- `storage/parquet_store.py` (full file — this is the I/O layer)
- `utils/exceptions.py`
- `utils/logger.py`
- `pipeline/context.py`
- `config/settings.yaml`
- All five feature modules just built (moving_averages, atr, volume, relative_strength, pivot, vcp)

### Prompt

```
You are building the feature store orchestrator for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `features/feature_store.py`.

This is the ONLY module that does I/O. It coordinates all feature modules
and reads/writes Parquet files. It does NOT compute any indicators directly.

--- IMPORTS AVAILABLE ---

from storage.parquet_store import (
    read_parquet, write_parquet, append_row,
    read_last_n_rows, get_last_date
)
from utils.exceptions import FeatureStoreOutOfSyncError, InsufficientDataError
from utils.logger import get_logger
import features.moving_averages as ma_mod
import features.atr as atr_mod
import features.volume as vol_mod
import features.pivot as pivot_mod
import features.vcp as vcp_mod
# Note: relative_strength is handled differently (cross-symbol, see below)

--- THREE PUBLIC FUNCTIONS ---

1. bootstrap(symbol: str, config: dict) -> None
   """
   Full history computation. Run once on setup, or to repair corruption.
   - Reads ALL rows from data/processed/{symbol}.parquet
   - Runs ALL feature modules in sequence (ma → atr → vol → pivot → vcp)
   - Note: rs_raw column is left as NaN here (requires cross-symbol benchmark, filled externally)
   - Writes result to data/features/{symbol}.parquet (full overwrite via write_parquet)
   - Raises InsufficientDataError (from any feature module) — let it propagate
   - Logs: "bootstrap {symbol}: {len(df)} rows → {len(feature_df)} feature columns"
   """

2. update(symbol: str, run_date: date, config: dict) -> None
   """
   Incremental daily update. FAST PATH — always use this for daily runs.
   - Reads last 300 rows from data/processed/{symbol}.parquet
     (300 is enough for SMA200=200 + RS period=63 + VCP base=52wk + buffer)
   - Runs ALL feature modules on the 300-row window
   - Extracts ONLY the last row (today's computed values)
   - Calls append_row(features_path, new_row) to add it to the feature Parquet file
   - If FeatureStoreOutOfSyncError is raised by append_row → log a warning and return (idempotent)
   - Raises InsufficientDataError if processed data has < 300 rows (trigger bootstrap instead)
   - Logs: "update {symbol} {run_date}: appended 1 feature row"
   """

3. needs_bootstrap(symbol: str, config: dict) -> bool
   """
   Returns True if the feature file is missing or empty.
   Does NOT check for corruption — just file existence + row count.
   - features_path = Path(config["data"]["features_dir"]) / f"{symbol}.parquet"
   - Returns True if file doesn't exist OR has 0 rows
   - Returns False otherwise
   """

--- PATH RESOLUTION ---

processed_path: Path = Path(config["data"]["processed_dir"]) / f"{symbol}.parquet"
features_path:  Path = Path(config["data"]["features_dir"])  / f"{symbol}.parquet"

--- UNIT TESTS ---

Create `tests/unit/test_feature_store.py`:

Use tmp_path (pytest fixture) as the data root. Mock config:
  config = {
      "data": {
          "processed_dir": str(tmp_path / "processed"),
          "features_dir":  str(tmp_path / "features"),
      },
      "stage": {"ma200_slope_lookback": 20, "ma50_slope_lookback": 10},
      "vcp": {"detector": "rule_based", "pivot_sensitivity": 5, ...},
      ...
  }

Tests:
1. needs_bootstrap() returns True for missing file
2. needs_bootstrap() returns False after bootstrap() runs
3. bootstrap() creates the feature Parquet file with expected columns
4. update() appends exactly 1 new row to the feature file
5. update() is idempotent — calling it twice for the same date does not raise, just logs
6. After bootstrap + update, feature file has len(processed) + 1 rows
7. InsufficientDataError propagates from bootstrap when processed data < 200 rows

--- IMPORTANT NOTES ---
- The 300-row read window in update() is a CONSTANT — do not make it configurable.
- rs_raw / rs_rating columns are intentionally left NaN in this module.
  They are filled by a cross-symbol pass in screener/pipeline.py (Phase 3).
- This module must NOT import from screener/, rules/, or api/.
```


---

## STEP 7 of 9 — `screener/pre_filter.py`

### Context files to attach
- `utils/logger.py`
- `config/settings.yaml`

### Prompt

```
You are building a screener module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `screener/pre_filter.py`.

--- PURPOSE ---
Before running the expensive full rule engine on 500-2000 symbols, a cheap
pre-filter eliminates obvious non-candidates in microseconds using only
summary statistics (the last row of each symbol's feature DataFrame).
Target: eliminate ~70% of universe before full rule engine runs.

--- INTERFACE ---

  def pre_filter(
      features_index: dict[str, dict],
      config: dict,
  ) -> list[str]:
      """
      Fast gate using only last-row feature summary values.
      Criteria (intentionally MORE PERMISSIVE than the real Trend Template):

        1. close >= 0.70 * high_52w         (TT uses 75%; we use 70% as buffer)
        2. rs_rating >= 50                   (TT uses 70; we use 50 as pre-filter)
        3. close > sma_200                   (Stage 2 requires this)

      Returns the list of symbols that PASS all three criteria.
      Symbols missing any required key in their feature dict are EXCLUDED (logged).

      features_index format:
        {
          "RELIANCE": {
            "close": 2800.0, "high_52w": 3100.0,
            "rs_rating": 72, "sma_200": 2500.0, ...
          },
          "TCS": { ... },
          ...
        }

      config keys (all optional, defaults shown):
        config.get("pre_filter", {}).get("min_close_pct_of_52w_high", 0.70)
        config.get("pre_filter", {}).get("min_rs_rating", 50)
      """

  def build_features_index(
      universe: list[str],
      config: dict,
  ) -> dict[str, dict]:
      """
      Read the last row of each symbol's feature Parquet file and build the
      features_index dict consumed by pre_filter().

      For each symbol:
        - path = Path(config["data"]["features_dir"]) / f"{symbol}.parquet"
        - If file missing: skip symbol, log warning
        - Read last 1 row (use read_last_n_rows from storage/parquet_store.py with n=1)
        - Extract: close, sma_200, rs_rating, and compute high_52w from raw if needed

      high_52w derivation:
        Also read last 252 rows of data/processed/{symbol}.parquet to get:
          high_52w = max of "high" column over those 252 rows
        (Avoids needing to store high_52w in feature Parquet — it's always recomputable)

      Returns the populated features_index dict.
      Symbols with missing feature files are simply omitted from the dict.
      """

--- UNIT TESTS ---

Create `tests/unit/test_pre_filter.py`:

1. Symbol with close=100, high_52w=100, rs_rating=80, sma_200=90 → PASSES
2. Symbol with close=60, high_52w=100 (60% of 52w high < 70%) → FILTERED OUT
3. Symbol with rs_rating=40 (< 50) → FILTERED OUT
4. Symbol with close=80, sma_200=90 (below SMA200) → FILTERED OUT
5. Symbol with missing "close" key → FILTERED OUT without raising
6. Empty features_index → returns empty list
7. All symbols pass → returns all symbols

--- IMPORTANT NOTES ---
- This module imports from storage/parquet_store.py for build_features_index.
- pre_filter() itself is PURE — no I/O, no file reads. Only build_features_index() does I/O.
- The thresholds (0.70, 50) are configurable but default to design spec values.
- Log: "pre_filter: {len(passed)}/{len(features_index)} symbols passed ({pct:.0%})"
```


---

## STEP 8 of 9 — Test fixtures + RS integration pass

### Context files to attach
- All feature modules built so far (moving_averages, atr, volume, relative_strength, pivot, vcp, feature_store)
- `ingestion/validator.py`
- `storage/parquet_store.py`
- `utils/trading_calendar.py`
- `tests/unit/test_validator.py` (to show existing test patterns)

### Prompt

```
You are adding integration test fixtures and completing the RS rating cross-symbol
pass for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK 1: Create `tests/fixtures/sample_ohlcv.parquet`

Create a script `scripts/create_test_fixtures.py` that generates and saves this file.
The file should contain deterministic OHLCV data for THREE fake symbols:
  - "MOCKUP"  — a clean Stage 2 uptrend with a VCP forming (strong stock)
  - "MOCKDN"  — a Stage 4 decline (price below declining MAs)
  - "MOCKFLAT" — a Stage 1 flat base

For each symbol, generate 300 trading days (pd.bdate_range) of synthetic data with:
  - Realistic open/high/low/close/volume (no negative prices, volume > 0)
  - np.random.default_rng(seed=42) for full reproducibility
  - "MOCKUP" must pass MA ordering checks after 200 bars
  - "MOCKDN" must have close < sma_200 after 200 bars

Save to: tests/fixtures/sample_ohlcv_{symbol}.parquet  (one file per symbol)

TASK 2: Complete `features/relative_strength.py` — integration into feature_store

The RS rating requires a cross-symbol pass that feature_store.py cannot do alone.
Add a standalone function to `features/relative_strength.py`:

  def run_rs_rating_pass(
      universe: list[str],
      run_date: date,
      config: dict,
      benchmark_df: pd.DataFrame,
  ) -> dict[str, int]:
      """
      For each symbol in universe:
        1. Read last 70 rows of data/processed/{symbol}.parquet
        2. Call compute_rs_raw(symbol_df, benchmark_df, config)
        3. Extract the last row's rs_raw value
      Then call compute_rs_rating(all_rs_raw) to get 0-99 ratings.
      Returns { symbol: rs_rating_int } for all symbols with valid data.
      Symbols with insufficient data get rs_rating = 0.
      """

  def write_rs_ratings_to_features(
      rs_ratings: dict[str, int],
      config: dict,
  ) -> None:
      """
      For each (symbol, rating) in rs_ratings:
        - Read the feature Parquet file
        - Set the last row's rs_rating column to rating
        - Write back atomically (write_parquet — full overwrite is OK here)
      This is called ONCE per daily run after run_rs_rating_pass().
      """

TASK 3: Create `tests/integration/test_feature_pipeline_e2e.py`

End-to-end test using the fixture files:
1. Run bootstrap("MOCKUP", config) with tmp_path as data root
2. Verify feature file exists and has expected columns:
   [sma_10, sma_21, sma_50, sma_150, sma_200, ema_21, ma_slope_50, ma_slope_200,
    atr_14, atr_pct, vol_50d_avg, vol_ratio, up_vol_days, down_vol_days, acc_dist_score,
    pivot_high, pivot_low, pivot_high_idx, pivot_low_idx,
    vcp_contraction_count, vcp_max_depth_pct, vcp_final_depth_pct,
    vcp_vol_ratio, vcp_base_length_weeks, vcp_base_low, vcp_valid, vcp_tightness_score,
    rs_raw]  ← rs_raw present but NaN (filled by RS pass)
3. Run update("MOCKUP", today, config) → feature file has 1 more row
4. needs_bootstrap("MOCKUP", config) returns False after bootstrap
5. needs_bootstrap("NONEXISTENT", config) returns True

--- BENCHMARK REQUIREMENT ---
Add a pytest mark to the e2e test:
  @pytest.mark.slow
And a comment: "Bootstrap 300-row symbol must complete in < 2 seconds on any machine."
Use time.perf_counter() to assert this.

--- IMPORTANT NOTES ---
- The fixture script (create_test_fixtures.py) should be runnable standalone:
    python scripts/create_test_fixtures.py
  Output: "Created 3 fixture files in tests/fixtures/"
- Do NOT commit real market data — only synthetic data in fixtures.
```


---

## STEP 9 of 9 — `scripts/rebuild_features.py` + Phase 2 benchmark + BUILD_STATUS update

### Context files to attach
- `features/feature_store.py`
- `screener/pre_filter.py`
- `ingestion/universe_loader.py`
- `pipeline/context.py`
- `scripts/run_daily.py` (to match CLI style)
- `utils/logger.py`
- `config/settings.yaml`
- `BUILD_STATUS.md` (current file)

### Prompt

```
You are completing Phase 2 of a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK 1: Implement `scripts/rebuild_features.py`

A CLI script to recompute all features from scratch for a given universe.
Mirrors the style of scripts/run_daily.py (argparse, get_logger, yaml config load).

  python scripts/rebuild_features.py --universe nifty500
  python scripts/rebuild_features.py --universe all
  python scripts/rebuild_features.py --symbols "RELIANCE,TCS,INFY"

CLI args:
  --universe   : "nifty500" | "all" | "custom"  (default: "nifty500")
  --symbols    : comma-separated list (overrides --universe if provided)
  --config     : path to settings.yaml (default: "config/settings.yaml")
  --dry-run    : log what would happen, skip all writes
  --workers    : number of parallel workers (default: 4)

Implementation:
1. Load config from settings.yaml using yaml.safe_load
2. Resolve symbol list via ingestion/universe_loader.py or --symbols override
3. For each symbol run features/feature_store.bootstrap(symbol, config)
4. Use concurrent.futures.ProcessPoolExecutor(max_workers=workers)
5. Progress bar: print "Rebuilt {n}/{total} symbols" every 10 symbols
6. Final summary: "Rebuilt {success} / {total} symbols in {elapsed:.1f}s"
7. Symbols that raise exceptions are logged with ERROR level and counted as failures

TASK 2: Benchmark test

Create `tests/unit/test_feature_benchmark.py` with a pytest mark @pytest.mark.benchmark:

  def test_single_symbol_bootstrap_under_2s(tmp_path):
      """Bootstrap a 300-row symbol must complete in under 2 seconds."""
      ...

  def test_single_symbol_update_under_50ms(tmp_path):
      """Daily update (300-row window → 1 appended row) must complete in under 50ms."""
      ...

  def test_pre_filter_1000_symbols_under_100ms():
      """pre_filter() on a 1000-symbol features_index dict must run in under 100ms."""
      features_index = {f"SYM{i:04d}": {...} for i in range(1000)}
      ...

TASK 3: Update `BUILD_STATUS.md` Phase 2 section

Read the current BUILD_STATUS.md file and update ONLY the Phase 2 section.
For each task in the Phase 2 checklist, change ❌ to ✅ for completed items.
Update the Quick Summary table: change Phase 2 from "⏳ NOT STARTED | 0%" to "✅ COMPLETE | 100%".
Update "Overall Project Completion" to "~20%".
Do NOT modify any other phase section.

--- FILE HEADER STYLE (match existing scripts) ---

The script should start with:
  #!/usr/bin/env python3
  """
  scripts/rebuild_features.py
  ----------------------------
  CLI: Recompute all feature Parquet files from scratch.
  ...
  """

--- FINAL CHECKLIST FOR PHASE 2 COMPLETION ---
Before marking Phase 2 complete, verify ALL of the following exist:
  features/moving_averages.py  ✓
  features/atr.py              ✓
  features/volume.py           ✓
  features/relative_strength.py ✓
  features/sector_rs.py        ✓
  features/pivot.py            ✓
  features/vcp.py              ✓
  features/feature_store.py    ✓
  screener/pre_filter.py       ✓
  scripts/rebuild_features.py  ✓
  tests/unit/test_moving_averages.py  ✓
  tests/unit/test_atr.py       ✓
  tests/unit/test_volume.py    ✓
  tests/unit/test_relative_strength.py ✓
  tests/unit/test_sector_rs.py ✓
  tests/unit/test_pivot.py     ✓
  tests/unit/test_vcp.py       ✓
  tests/unit/test_feature_store.py ✓
  tests/unit/test_pre_filter.py ✓
  tests/unit/test_feature_benchmark.py ✓
  tests/integration/test_feature_pipeline_e2e.py ✓
  tests/fixtures/sample_ohlcv_MOCKUP.parquet ✓
  tests/fixtures/sample_ohlcv_MOCKDN.parquet ✓
  tests/fixtures/sample_ohlcv_MOCKFLAT.parquet ✓
  scripts/create_test_fixtures.py ✓

Run `make test` and confirm all tests pass before updating BUILD_STATUS.md.
```

---

## QUICK REFERENCE — Step Dependency Order

```
Step 1: features/moving_averages.py          (no deps — first feature module)
Step 2: features/atr.py + volume.py          (no deps on other features)
Step 3: features/relative_strength.py        (no deps on other features)
        features/sector_rs.py                (depends on rs_rating output)
Step 4: features/pivot.py                    (no deps on other features)
Step 5: features/vcp.py                      (depends on pivot.py)
Step 6: features/feature_store.py            (depends on ALL feature modules + parquet_store)
Step 7: screener/pre_filter.py               (depends on parquet_store only)
Step 8: Test fixtures + RS integration pass  (depends on feature_store + all modules)
Step 9: rebuild_features.py + benchmarks     (depends on everything above)
```

## TIPS FOR EACH SESSION

1. **Always share the listed context files** — paste their full content. Claude cannot read your disk.
2. **One step per session** — each prompt is scoped to avoid context overflow.
3. **Run `pytest tests/unit/test_<module>.py -v` immediately** after Claude writes the code.
4. **If tests fail**, paste the failure output back into the same session for a fix.
5. **After Step 6**, run `python scripts/create_test_fixtures.py` to generate fixture files
   before moving to Step 8.
6. **After Step 9**, run `make test` and paste the full output when updating BUILD_STATUS.md.
