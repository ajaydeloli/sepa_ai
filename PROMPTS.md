# PROMPTS.md
# Minervini SEPA Stock Analysis System — Phase 3–12 Build Prompts
# Use each prompt block in a FRESH Claude session.
# Always attach the listed "Context files" BEFORE pasting the prompt.
# Project root on disk: /home/ubuntu/projects/sepa_ai/

---

## PHASE 3 — Rule Engine (Weeks 7–9)
**Goal:** Deterministic, fully testable SEPA screening logic.

### Dependency order within Phase 3
```
Step 1: rules/stage.py                       (only needs feature columns as input)
Step 2: rules/trend_template.py              (only needs feature columns)
Step 3: rules/vcp_rules.py + entry_trigger.py + stop_loss.py + risk_reward.py
Step 4: rules/scorer.py + SEPAResult          (depends on steps 1–3)
Step 5: screener/pipeline.py + screener/results.py (depends on step 4 + feature_store)
```

---

### PHASE 3 — STEP 1 of 5: `rules/stage.py`

#### Context files to attach
- `PROJECT_DESIGN.md` (sections 7.1 and Appendix C)
- `utils/exceptions.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the rule engine for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `rules/stage.py` — Stage 1/2/3/4 detection.

This is the HARD GATE that runs FIRST before any other rule. A stock not in
Stage 2 is eliminated immediately regardless of anything else.

--- DATACLASS OUTPUT ---

from dataclasses import dataclass

@dataclass
class StageResult:
    stage: int                  # 1 | 2 | 3 | 4
    label: str                  # "Stage 2 — Advancing"
    confidence: int             # 0–100 integer
    reason: str                 # human-readable explanation for the classification
    ma_slope_200: float         # computed slope value (positive = trending up)
    ma_slope_50: float
    is_buyable: bool            # True only when stage == 2

--- FUNCTION SIGNATURE ---

def detect_stage(row: pd.Series, config: dict) -> StageResult:
    """
    Classifies a stock into Stage 1/2/3/4 using the most recent feature row.

    Input: `row` is a pd.Series — one row from the feature Parquet file.
    Expected columns (all must exist; raise KeyError with clear message if missing):
      close, sma_50, sma_200, ma_slope_50, ma_slope_200

    Stage 2 criteria (ALL must be true — is_buyable=True):
      1. close > sma_50
      2. close > sma_200
      3. sma_50 > sma_200          (correct MA stack)
      4. ma_slope_200 > 0          (SMA200 trending up)
      5. ma_slope_50 > 0           (SMA50 trending up)

    Stage 1 — Basing:
      price below both MAs OR (price between MAs with flat slopes)
      slopes both near zero: abs(ma_slope_200) < threshold AND abs(ma_slope_50) < threshold
      threshold = config.get("stage", {}).get("flat_slope_threshold", 0.0005)

    Stage 3 — Topping:
      close < sma_50 AND close > sma_200  (lost 50MA, still above 200MA)
      OR sma_50 is declining AND close recently above both MAs

    Stage 4 — Declining:
      close < sma_50 AND close < sma_200
      ma_slope_200 < 0

    confidence:
      Stage 2: 100 if all 5 conditions clearly pass (slopes > threshold*2), else 70–90
      Other stages: 60–90 based on how clearly the conditions are met

    config keys:
      config.get("stage", {}).get("ma200_slope_lookback", 20)  → used upstream, not here
      config.get("stage", {}).get("flat_slope_threshold", 0.0005)
    """

--- UNIT TESTS ---

Create `tests/unit/test_stage_detection.py`:

1. Classic Stage 2: close=150, sma_50=130, sma_200=110, slopes both positive
   → stage==2, is_buyable==True, confidence>=70

2. Stage 4: close=80, sma_50=100, sma_200=120, slope_200<0
   → stage==4, is_buyable==False

3. Stage 1: close=100, sma_50=102, sma_200=105, both slopes ≈ 0
   → stage==1, is_buyable==False

4. Stage 3: close=95, sma_50=100, sma_200=90, sma_50 starting to decline
   → stage==3, is_buyable==False

5. Missing column raises KeyError with descriptive message

6. Stage 2 stock with very strong slopes → confidence==100

Helper:
  def _make_row(**kwargs) -> pd.Series:
      defaults = {"close":150, "sma_50":130, "sma_200":110,
                  "ma_slope_50":0.05, "ma_slope_200":0.03}
      defaults.update(kwargs)
      return pd.Series(defaults)

--- ANTI-PATTERNS ---
- Do NOT load any files — input is a single pd.Series row
- Do NOT import from screener/, pipeline/, api/, or dashboard/
- Stage 2 requires ALL 5 conditions — do not short-circuit on partial match
```


---

### PHASE 3 — STEP 2 of 5: `rules/trend_template.py`

#### Context files to attach
- `PROJECT_DESIGN.md` (section 7.2 and Appendix A)
- `rules/stage.py` (just built — shows StageResult pattern)
- `utils/exceptions.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the rule engine for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `rules/trend_template.py` — Minervini's 8 Trend Template conditions.

--- DATACLASS OUTPUT ---

from dataclasses import dataclass, field

@dataclass
class TrendTemplateResult:
    passes: bool                           # True only when ALL 8 conditions pass
    conditions_met: int                    # count of True conditions (0–8)
    condition_1: bool   # price > SMA_150 AND price > SMA_200
    condition_2: bool   # SMA_150 > SMA_200
    condition_3: bool   # SMA_200 slope > 0 (trending up)
    condition_4: bool   # SMA_50 > SMA_150 AND SMA_50 > SMA_200
    condition_5: bool   # price > SMA_50
    condition_6: bool   # price >= N% above 52-week low
    condition_7: bool   # price within N% of 52-week high
    condition_8: bool   # RS Rating >= min_rs_rating
    details: dict = field(default_factory=dict)   # numeric values for each check

--- FUNCTION SIGNATURE ---

def check_trend_template(row: pd.Series, config: dict) -> TrendTemplateResult:
    """
    Evaluates all 8 Minervini Trend Template conditions against a single feature row.

    Required row columns:
      close, sma_50, sma_150, sma_200, ma_slope_200,
      high_52w, low_52w, rs_rating

    Config keys (all under "trend_template" section):
      pct_above_52w_low: 25.0   (condition 6: close >= low_52w * (1 + N/100))
      pct_below_52w_high: 25.0  (condition 7: close >= high_52w * (1 - N/100))
      min_rs_rating: 70         (condition 8: rs_rating >= N)

    The `passes` field is True ONLY when all 8 conditions are True.
    `conditions_met` is always the exact count of True conditions.

    The `details` dict should contain the computed values for debuggability:
      {
        "close": float, "sma_50": float, "sma_150": float, "sma_200": float,
        "ma_slope_200": float, "high_52w": float, "low_52w": float,
        "rs_rating": int, "pct_above_52w_low": float, "pct_below_52w_high": float
      }

    Missing columns: fill the corresponding condition as False and log a warning.
    Do NOT raise — TrendTemplateResult with passes=False is the graceful output.
    """

--- UNIT TESTS ---

Create `tests/unit/test_trend_template.py`:

1. All 8 conditions pass → passes==True, conditions_met==8
2. Condition 1 fails (close < sma_150) → passes==False, conditions_met==7
3. Condition 8 fails (rs_rating=55 < 70) → passes==False, conditions_met==7
4. Condition 6 fails (close only 10% above 52w low, threshold=25%) → passes==False
5. Condition 7 fails (close is 30% below 52w high, threshold=25%) → passes==False
6. Missing sma_150 column → condition_1==False, no exception raised
7. Custom config: min_rs_rating=80 → stock with rs_rating=75 fails condition_8
8. details dict contains all numeric values listed above

Use this helper:
  def _make_passing_row() -> pd.Series:
      return pd.Series({
          "close": 100, "sma_50": 85, "sma_150": 80, "sma_200": 75,
          "ma_slope_200": 0.02, "high_52w": 110, "low_52w": 60, "rs_rating": 82
      })

  def _default_config() -> dict:
      return {"trend_template": {
          "pct_above_52w_low": 25.0, "pct_below_52w_high": 25.0, "min_rs_rating": 70
      }}

--- IMPORTANT NOTES ---
- SMA_150 is a REQUIRED column — do not substitute SMA_200 or an approximation.
  If sma_150 is NaN, condition_1 and condition_2 both evaluate to False.
- Condition 3 uses ma_slope_200, which is pre-computed by features/moving_averages.py.
  Do NOT recompute the slope here — read it from the row.
- high_52w and low_52w: if not in row, build_features_index in pre_filter.py computes them.
  For the rule engine, assume they are present in the feature row.
```


---

### PHASE 3 — STEP 3 of 5: `rules/vcp_rules.py` + `rules/entry_trigger.py` + `rules/stop_loss.py` + `rules/risk_reward.py`

#### Context files to attach
- `features/vcp.py` (VCPMetrics dataclass — this is the input)
- `features/pivot.py` (find_all_pivots — used for entry trigger)
- `rules/stage.py` (StageResult pattern)
- `utils/exceptions.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the rule engine for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement FOUR small rule files in one session:
  rules/vcp_rules.py
  rules/entry_trigger.py
  rules/stop_loss.py
  rules/risk_reward.py

--- FILE 1: rules/vcp_rules.py ---

from features.vcp import VCPMetrics

def qualify_vcp(metrics: VCPMetrics, config: dict) -> tuple[bool, dict]:
    """
    Applies VCP qualification rules to a VCPMetrics object.
    Returns (qualified: bool, details: dict).

    Rules (all configurable from config["vcp"]):
      contraction_count >= min_contractions  (default 2)
      contraction_count <= max_contractions  (default 5)
      final_depth_pct < max_depth_pct        (declining depth REQUIRED)
      vol_contraction_ratio < 1.0            (if require_vol_contraction=true)
      base_length_weeks >= min_weeks         (default 3)
      base_length_weeks <= max_weeks         (default 52)
      tightness_score < tightness_pct        (default 10.0)
      max_depth_pct <= max_depth_pct_abs     (default 50.0)

    details dict: { "rule_name": bool, ... } one key per rule above.
    If metrics.is_valid_vcp is False, return (False, details) immediately.
    """

--- FILE 2: rules/entry_trigger.py ---

from dataclasses import dataclass

@dataclass
class EntryTrigger:
    triggered: bool
    entry_price: float | None       # breakout level (last pivot high + small buffer)
    pivot_high: float | None        # the VCP pivot high being broken
    volume_confirmed: bool          # True if today's vol_ratio >= breakout_vol_threshold
    reason: str                     # "breakout above pivot {price} with vol confirmation" etc.

def check_entry_trigger(row: pd.Series, config: dict) -> EntryTrigger:
    """
    Detects if price has broken above the VCP pivot high with volume confirmation.

    Required row columns: close, pivot_high, vol_ratio
    (pivot_high comes from features/pivot.py, vol_ratio from features/volume.py)

    Breakout condition:
      close > pivot_high * (1 + buffer_pct)
      buffer_pct = config.get("entry", {}).get("breakout_buffer_pct", 0.001)  (0.1%)

    Volume confirmation:
      vol_ratio >= breakout_vol_threshold
      breakout_vol_threshold = config.get("entry", {}).get("breakout_vol_threshold", 1.5)

    entry_price = pivot_high * (1 + buffer_pct) when triggered.
    If pivot_high is NaN or 0, triggered=False with reason="no pivot high available".
    """

--- FILE 3: rules/stop_loss.py ---

def compute_stop_loss(
    row: pd.Series,
    vcp_base_low: float | None,
    config: dict,
) -> tuple[float | None, float | None, str]:
    """
    Returns (stop_price, risk_pct, method_used) where:
      stop_price: the stop-loss price
      risk_pct:   (entry_price - stop_price) / entry_price * 100
      method_used: "vcp_base_low" | "atr" | "pct"

    Priority:
      1. VCP base_low (preferred — from VCPMetrics.base_low):
           stop = vcp_base_low * (1 - stop_buffer_pct)
           Valid if vcp_base_low is not None and vcp_base_low > 0
           and risk_pct <= max_risk_pct

      2. ATR fallback (if VCP stop invalid or risk too wide):
           stop = close - (atr_14 * atr_multiplier)
           atr_multiplier = config.get("stop_loss", {}).get("atr_multiplier", 2.0)

      3. Fixed % fallback:
           stop = close * (1 - fixed_stop_pct)
           fixed_stop_pct = config.get("stop_loss", {}).get("fixed_stop_pct", 0.07)

    Required row columns: close, atr_14
    max_risk_pct = config.get("stop_loss", {}).get("max_risk_pct", 15.0)
    stop_buffer_pct = config.get("stop_loss", {}).get("stop_buffer_pct", 0.005)
    Returns (None, None, "no_data") if close is NaN or 0.
    """

--- FILE 4: rules/risk_reward.py ---

def compute_risk_reward(
    entry_price: float,
    stop_price: float,
    config: dict,
    resistance_price: float | None = None,
) -> tuple[float, float, float]:
    """
    Returns (target_price, risk_amount, reward_risk_ratio).

    target_price:
      If resistance_price provided and resistance > entry: use resistance as target.
      Otherwise: entry + (entry - stop) * min_rr_ratio
      min_rr_ratio = config.get("risk_reward", {}).get("min_rr_ratio", 2.0)

    risk_amount = entry_price - stop_price
    reward_risk_ratio = (target_price - entry_price) / risk_amount

    Returns (0.0, 0.0, 0.0) if entry_price <= stop_price.
    """

--- UNIT TESTS ---

Create `tests/unit/test_vcp_rules.py`:
1. VCPMetrics with 3 contractions, declining depth, vol dry-up → qualified==True
2. VCPMetrics with 1 contraction (< min_contractions=2) → qualified==False
3. VCPMetrics where final_depth > max_depth (not declining) → qualified==False
4. VCPMetrics with tightness_score=12 > threshold=10 → qualified==False
5. qualify_vcp with metrics.is_valid_vcp=False → immediately returns (False, ...)

Create `tests/unit/test_entry_trigger.py`:
1. close=102, pivot_high=100, vol_ratio=2.0 → triggered==True, volume_confirmed==True
2. close=99, pivot_high=100 → triggered==False
3. close=101, pivot_high=100, vol_ratio=1.2 < 1.5 → triggered==True but volume_confirmed==False
4. pivot_high=NaN → triggered==False, no exception

Create `tests/unit/test_stop_loss.py`:
1. VCP base_low=85, close=100, atr_14=3 → method=="vcp_base_low"
2. VCP base_low=50 (risk=50% > max 15%) → falls back to ATR method
3. vcp_base_low=None → falls back to ATR
4. close=0 → returns (None, None, "no_data")

Create `tests/unit/test_risk_reward.py`:
1. entry=100, stop=93 → risk=7, target=114 (2R), rr=2.0
2. entry=100, stop=93, resistance=120 → uses resistance as target, rr=2.86
3. entry <= stop → returns (0, 0, 0)
```


---

### PHASE 3 — STEP 4 of 5: `rules/scorer.py` + `SEPAResult` dataclass

#### Context files to attach
- `rules/stage.py` (StageResult)
- `rules/trend_template.py` (TrendTemplateResult)
- `rules/vcp_rules.py` (qualify_vcp)
- `rules/entry_trigger.py` (EntryTrigger)
- `rules/stop_loss.py` (compute_stop_loss)
- `rules/risk_reward.py` (compute_risk_reward)
- `features/vcp.py` (VCPMetrics)
- `features/sector_rs.py` (get_sector_score_bonus)
- `PROJECT_DESIGN.md` (section 7.4 — scoring weights)
- `config/settings.yaml`

#### Prompt
```
You are building the rule engine for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `rules/scorer.py` — the SEPAResult dataclass and composite scorer.

--- SEPAResult DATACLASS ---

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

@dataclass
class SEPAResult:
    symbol: str
    run_date: date
    stage: int
    stage_label: str
    stage_confidence: int
    trend_template_pass: bool
    trend_template_details: dict[str, Any]
    conditions_met: int                    # 0–8 trend template conditions
    fundamental_pass: bool                 # Phase 5 — default False
    fundamental_details: dict[str, Any]    # Phase 5 — default {}
    vcp_qualified: bool
    vcp_details: dict[str, Any]
    breakout_triggered: bool
    entry_price: float | None
    stop_loss: float | None
    risk_pct: float | None
    target_price: float | None
    reward_risk_ratio: float | None
    rs_rating: int
    sector_bonus: int                      # 0 or 5
    news_score: float | None               # Phase 5 — default None
    setup_quality: Literal["A+", "A", "B", "C", "FAIL"]
    score: int                             # 0–100

--- SCORE WEIGHTS (explicit constants at module level) ---

SCORE_WEIGHTS = {
    "rs_rating":   0.30,
    "trend":       0.25,
    "vcp":         0.22,
    "volume":      0.10,
    "fundamental": 0.07,
    "news":        0.06,
}
# Stage 2 is a hard gate: final_score = 0 if stage != 2

--- MAIN FUNCTION ---

def score_symbol(
    symbol: str,
    run_date: date,
    row: pd.Series,
    stage_result: StageResult,
    tt_result: TrendTemplateResult,
    vcp_metrics: VCPMetrics,
    sector_ranks: dict[str, int],
    symbol_info: pd.DataFrame,
    config: dict,
    fundamental_result: dict | None = None,   # Phase 5 — pass None for now
    news_score: float | None = None,          # Phase 5 — pass None for now
) -> SEPAResult:
    """
    Assembles all rule outputs into a final SEPAResult.
    
    Score calculation per component (each normalised 0–100 before weighting):
      rs_rating_score  = row["rs_rating"]          (already 0–99; treat as 0–100)
      trend_score      = conditions_met / 8 * 100
      vcp_score        = compute_vcp_score(vcp_metrics)   (see below)
      volume_score     = compute_volume_score(row)         (see below)
      fundamental_score = fundamental_result["score"] if provided else 50  (neutral)
      news_score_norm  = (news_score + 100) / 2 if news_score is not None else 50

    vcp_score (0–100):
      If vcp_qualified: 60 + (contraction quality bonus)
        bonus = (3 - abs(vcp_metrics.contraction_count - 3)) * 10   (ideal=3 contractions)
        bonus += 20 if vol_contraction_ratio < 0.5 else 10 if < 0.8 else 0
        bonus = min(40, bonus)
      If not vcp_qualified: max(0, vcp_metrics.contraction_count * 15)

    volume_score (0–100):
      vol_ratio = row.get("vol_ratio", 1.0)
      If breakout_triggered:
        volume_score = min(100, vol_ratio / 3.0 * 100)   (3× avg = perfect score)
      Else:
        volume_score = min(100, max(0, (row.get("acc_dist_score", 0) + 20) * 2.5))

    weighted_score = sum(component * weight for component, weight in ...)
    sector_bonus = get_sector_score_bonus(symbol, sector_ranks, symbol_info)
    final_score = int(min(100, weighted_score + sector_bonus))

    Stage gate: if stage_result.stage != 2: final_score = 0

    Setup quality:
      A+  → score >= 85 AND stage==2 AND conditions_met==8 AND vcp_qualified
      A   → score >= 70 AND stage==2 AND conditions_met==8
      B   → score >= 55 AND stage==2 AND conditions_met >= 6
      C   → score >= 40 AND stage==2
      FAIL → everything else (non-Stage-2, score<40, or <6 conditions)
    """

--- UNIT TESTS ---

Create `tests/unit/test_scorer.py`:

1. Stage 2, 8/8 TT, VCP qualified, rs_rating=88 → score >= 85, quality=="A+"
2. Stage 4 (non-buyable) → score==0, quality=="FAIL"
3. Stage 2, 6/8 conditions, score in 55–69 range → quality=="B"
4. Stage 2, 8/8 TT, VCP NOT qualified → quality=="A" or "B" depending on score
5. Sector bonus: symbol in top-5 sector → score = base_score + 5
6. fundamental_result=None → fundamental_score treated as neutral (50)
7. news_score=None → news treated as neutral (50)
8. SEPAResult is a dataclass → can be serialised with dataclasses.asdict()

Create `tests/integration/test_known_setups.py`:

  def test_stage4_blocked_despite_tt_pass():
      """Stage 4 stock scores FAIL even if all 8 TT conditions pass."""
      # Construct a row that passes TT but has Stage 4 MA arrangement
      row = _make_stage4_row()  # close < sma_50 < sma_200, slopes negative
      stage = detect_stage(row, config)
      result = score_symbol("TEST", date.today(), row, stage, tt, vcp, ...)
      assert result.stage == 4
      assert result.setup_quality == "FAIL"
      assert result.score == 0

--- NOTES ---
- SCORE_WEIGHTS must sum to exactly 1.0 — add an assertion at module load time.
- All individual component scores are 0–100 before weighting.
- The SEPAResult dataclass must be importable by pipeline.py (Phase 3 Step 5).
- fundamental_pass and news_score fields default to False/None for Phase 3;
  they are wired in Phase 5.
```


---

### PHASE 3 — STEP 5 of 5: `screener/pipeline.py` + `screener/results.py`

#### Context files to attach
- `rules/scorer.py` (SEPAResult)
- `rules/stage.py`, `rules/trend_template.py`, `rules/vcp_rules.py`, `rules/entry_trigger.py`, `rules/stop_loss.py`, `rules/risk_reward.py`
- `features/feature_store.py`
- `features/vcp.py` (VCPMetrics, get_detector)
- `screener/pre_filter.py` (pre_filter, build_features_index)
- `features/relative_strength.py` (run_rs_rating_pass)
- `storage/sqlite_store.py`
- `ingestion/universe_loader.py`
- `pipeline/context.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the screener for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `screener/pipeline.py` and `screener/results.py`.

--- FILE 1: screener/results.py ---

from rules.scorer import SEPAResult
from storage.sqlite_store import SQLiteStore

def persist_results(results: list[SEPAResult], db: SQLiteStore, run_date: date) -> None:
    """
    Write a list of SEPAResult objects to the SQLite `results` table.
    Uses INSERT OR REPLACE (upsert on symbol + run_date).
    Schema (create if not exists):
      symbol TEXT, run_date DATE, stage INT, score INT, setup_quality TEXT,
      trend_template_pass INT, conditions_met INT, vcp_qualified INT,
      breakout_triggered INT, entry_price REAL, stop_loss REAL, risk_pct REAL,
      rs_rating INT, news_score REAL, fundamental_pass INT,
      full_json TEXT,  -- dataclasses.asdict(result) serialised as JSON
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (symbol, run_date)
    """

def load_results(db: SQLiteStore, run_date: date | None = None) -> list[dict]:
    """
    Load results from SQLite. If run_date is None, load the most recent run.
    Returns list of dicts (one per symbol), sorted by score DESC.
    """

def get_top_candidates(
    db: SQLiteStore,
    run_date: date | None = None,
    min_quality: str = "A",
    limit: int = 20,
) -> list[dict]:
    """Filter results by quality and return top N by score."""

--- FILE 2: screener/pipeline.py ---

from concurrent.futures import ProcessPoolExecutor, as_completed
from rules.scorer import SEPAResult, score_symbol
from rules.stage import detect_stage
from rules.trend_template import check_trend_template
from features.vcp import get_detector
from rules.vcp_rules import qualify_vcp
from rules.entry_trigger import check_entry_trigger
from rules.stop_loss import compute_stop_loss
from rules.risk_reward import compute_risk_reward
from screener.pre_filter import pre_filter, build_features_index
from features.relative_strength import run_rs_rating_pass
from storage.parquet_store import read_last_n_rows

def run_screen(
    universe: list[str],
    run_date: date,
    config: dict,
    symbol_info: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    n_workers: int = 4,
) -> list[SEPAResult]:
    """
    Full screening pipeline for a list of symbols.

    Step 0: Build features_index (last-row summary for all symbols)
    Step 1: pre_filter() — eliminates ~70% of universe
    Step 2: run_rs_rating_pass() — compute cross-symbol RS ratings
    Step 3: write_rs_ratings_to_features() — update feature Parquet files
    Step 4: Compute sector_ranks from RS ratings
    Step 5: For each symbol that passed pre_filter:
              - Load last 5 rows of feature Parquet (rule engine only needs latest row)
              - Extract row = last row of feature df
              - detect_stage(row, config)           → StageResult
              - If not Stage 2: append FAIL result and continue
              - check_trend_template(row, config)   → TrendTemplateResult
              - get_detector(config).detect(df, config) → VCPMetrics
              - qualify_vcp(vcp_metrics, config)    → (bool, details)
              - check_entry_trigger(row, config)    → EntryTrigger
              - compute_stop_loss(row, ...)         → stop_price, risk_pct, method
              - compute_risk_reward(...)            → target, rr
              - score_symbol(...)                   → SEPAResult
            Run Step 5 in ProcessPoolExecutor(max_workers=n_workers)
    Step 6: Sort results by score DESC, return list

    Each worker processes ONE symbol. Use a helper function:
      def _screen_one(args: tuple) -> SEPAResult: ...
    (top-level function required for ProcessPoolExecutor pickling)

    Log progress: "screened {n}/{total}: {passed} passed pre_filter, {stage2} in Stage 2"
    Log timing: "run_screen completed in {elapsed:.1f}s for {len(universe)} symbols"
    """

--- UNIT TESTS ---

Create `tests/integration/test_screener_batch.py`:

1. Mock universe of 3 symbols (MOCKUP, MOCKDN, MOCKFLAT) using fixture data:
   - MOCKUP (Stage 2, strong RS) → stage==2, quality in ["A+","A","B"]
   - MOCKDN (Stage 4) → score==0, quality=="FAIL"
   - run_screen returns sorted list, MOCKUP appears before MOCKDN

2. pre_filter eliminates MOCKDN before rule engine runs
   (verify via log inspection or by checking pre_filter output separately)

3. persist_results + load_results round-trip:
   - Persist 3 results, load them back, verify score ordering preserved

4. get_top_candidates(min_quality="A") returns only A/A+ results

5. run_screen is idempotent — running twice for same date overwrites, not duplicates

--- ANTI-PATTERNS ---
- ProcessPoolExecutor workers must use top-level functions (not lambdas or instance methods)
- Do NOT load the full feature history inside a worker — only last 300 rows maximum
- screener/pipeline.py must NOT import from api/, dashboard/, or alerts/
- The rule engine (stage.py through scorer.py) operates on a pd.Series row, never a full df
```


---
---

## PHASE 4 — Reports, Charts, Alerts & Early Paper Trading (Weeks 10–12)
**Goal:** Human-consumable outputs, Telegram alerts with deduplication, and early paper trading.

### Dependency order within Phase 4
```
Step 1: reports/daily_watchlist.py + reports/templates/watchlist.html.j2
Step 2: reports/chart_generator.py
Step 3: alerts/alert_deduplicator.py + alerts/telegram_alert.py + alerts/email_alert.py
Step 4: pipeline/runner.py + pipeline/scheduler.py
Step 5: paper_trading/simulator.py (basic) + portfolio.py + order_queue.py
```

---

### PHASE 4 — STEP 1 of 5: `reports/daily_watchlist.py` + HTML template

#### Context files to attach
- `rules/scorer.py` (SEPAResult dataclass)
- `screener/results.py` (load_results, get_top_candidates)
- `PROJECT_DESIGN.md` (section 13.2 — Watchlist page layout)
- `utils/logger.py`
- `utils/date_utils.py`

#### Prompt
```
You are building the reports module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `reports/daily_watchlist.py` and `reports/templates/watchlist.html.j2`.

--- FILE 1: reports/daily_watchlist.py ---

from rules.scorer import SEPAResult

def generate_csv_report(
    results: list[SEPAResult],
    output_dir: str,
    run_date: date,
    watchlist_symbols: list[str] = None,
) -> str:
    """
    Writes watchlist_{run_date}.csv to output_dir.
    Columns (in order):
      rank, symbol, score, setup_quality, stage, conditions_met,
      vcp_qualified, breakout_triggered, entry_price, stop_loss,
      risk_pct, rs_rating, is_watchlist (bool)
    Rows: sorted by score DESC; A+/A only unless include_all=True.
    Watchlist symbols get is_watchlist=True regardless of quality.
    Returns the path to the written file.
    """

def generate_html_report(
    results: list[SEPAResult],
    output_dir: str,
    run_date: date,
    watchlist_symbols: list[str] = None,
    llm_briefs: dict[str, str] = None,    # symbol → brief text (Phase 6)
) -> str:
    """
    Renders watchlist_{run_date}.html using Jinja2 template.
    llm_briefs is optional — if None, brief sections are hidden.
    Returns the path to the written file.
    """

def get_report_summary(results: list[SEPAResult]) -> dict:
    """
    Returns: {
      "total_screened": int, "a_plus": int, "a": int, "b": int,
      "c": int, "fail": int, "stage2_count": int
    }
    """

--- FILE 2: reports/templates/watchlist.html.j2 ---

Jinja2 template for the daily watchlist HTML report.
Design requirements:
  - Clean, professional table layout (dark background or white — your choice)
  - Header: "SEPA Watchlist — {{ run_date }}" + summary stats row
  - Table columns: Rank | Symbol | Score | Quality | Stage | TT | VCP | Entry | Stop | Risk% | RS
  - Quality badge: colour-coded (A+=gold, A=green, B=blue, C=grey, FAIL=red)
  - ★ badge next to symbol name for watchlist symbols
  - Optional: LLM trade brief below each row (hidden if brief is empty)
  - Responsive: readable on desktop without horizontal scroll
  - Footer: "Generated by SEPA AI at {{ generated_at }}"

No external CSS frameworks — inline styles only (so the file is self-contained).

--- UNIT TESTS ---

Create `tests/unit/test_daily_watchlist.py`:
1. generate_csv_report creates a file with correct columns and row count
2. CSV rows are sorted by score DESC
3. Watchlist symbols have is_watchlist=True
4. generate_html_report creates valid HTML (contains <table> and quality badges)
5. get_report_summary counts correctly for a known list of SEPAResults
6. Empty results list → generates report with "No candidates" message, no crash
```



---

### PHASE 4 — STEP 2 of 5: `reports/chart_generator.py`

#### Context files to attach
- `rules/scorer.py` (SEPAResult dataclass)
- `features/vcp.py` (VCPMetrics — for VCP zone overlays)
- `features/pivot.py` (find_all_pivots — for pivot markers)
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the reports module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `reports/chart_generator.py` — candlestick chart with MA ribbons,
VCP markup, stage annotation, and pivot markers.

--- FUNCTION SIGNATURES ---

def generate_chart(
    symbol: str,
    ohlcv_df: pd.DataFrame,
    result: SEPAResult,
    vcp_metrics: VCPMetrics | None,
    output_dir: str,
    run_date: date,
    n_days: int = 90,
) -> str:
    """
    Generates a candlestick chart with:
      - Candlestick OHLCV (last n_days of data)
      - MA ribbons: SMA 50 (blue), SMA 150 (orange), SMA 200 (red)
      - Volume bar chart (bottom panel, 20% height)
      - Stage label in top-right corner: "Stage 2 — Advancing" (green) or other stage (red/grey)
      - Trend Template pass/fail indicator: green check or red X
      - VCP contraction zones: shaded boxes for each contraction (if vcp_metrics provided)
      - Pivot high markers: small triangles at swing highs
      - Entry price dashed line (green) if breakout_triggered
      - Stop loss dashed line (red) if stop_loss available
      - Setup quality badge: "A+" (gold), "A" (green), etc. in top-left corner
      - Title: "{symbol} — {run_date} — Score: {score}/100"

    Uses mplfinance for the main chart. Saves to:
      {output_dir}/charts/{symbol}_{run_date}.png

    Returns the path to the saved file.
    Raises ChartGenerationError (from utils/exceptions.py) if ohlcv_df is empty.

    ohlcv_df must have: DatetimeIndex, columns [open, high, low, close, volume]
    MA columns expected in ohlcv_df: sma_50, sma_150, sma_200 (if missing, skip that MA)
    """

def generate_batch_charts(
    results: list[SEPAResult],
    ohlcv_data: dict[str, pd.DataFrame],   # symbol → df
    vcp_data: dict[str, VCPMetrics],        # symbol → metrics
    output_dir: str,
    run_date: date,
    min_quality: str = "B",
    watchlist_symbols: list[str] = None,
) -> dict[str, str]:
    """
    Generate charts for all results meeting min_quality threshold.
    Always generate charts for watchlist_symbols regardless of quality.
    Returns { symbol: file_path } for successfully generated charts.
    Logs and skips symbols with errors — never raises.
    """

--- MPLFINANCE APPROACH ---

Use mplfinance with the following pattern:
  import mplfinance as mpf

  # Create addplot elements
  add_plots = [
      mpf.make_addplot(df["sma_50"],  color="#3a86ff", width=1.2),
      mpf.make_addplot(df["sma_150"], color="#fb8500", width=1.2),
      mpf.make_addplot(df["sma_200"], color="#e63946", width=1.5),
  ]

  fig, axes = mpf.plot(
      ohlcv_tail,
      type="candle",
      style="charles",        # or "nightclouds" for dark theme
      addplot=add_plots,
      volume=True,
      returnfig=True,
      figsize=(14, 8),
      title=title,
  )

For VCP zones: use axes[0].axvspan(x_start, x_end, alpha=0.08, color="yellow") per contraction.
For entry/stop lines: axes[0].axhline(y=entry_price, color="green", linestyle="--", alpha=0.7)
For stage label: axes[0].text(0.98, 0.96, stage_label, transform=axes[0].transAxes, ...)
For quality badge: axes[0].text(0.02, 0.96, f"★ {setup_quality}", ...)

--- UNIT TESTS ---

Create `tests/unit/test_chart_generator.py`:
1. generate_chart with valid OHLCV df + SEPAResult → file created at expected path
2. generate_chart with empty df → raises ChartGenerationError
3. Missing sma_150 column → chart generates without sma_150 MA (no exception)
4. generate_batch_charts: watchlist symbol with quality="C" still gets chart
5. generate_batch_charts: quality="B" filter skips "C" non-watchlist symbols
6. Output directory is created if it doesn't exist

Use `tests/fixtures/sample_ohlcv.parquet` as test OHLCV data.
Create a minimal mock SEPAResult for tests (stage=2, quality="A", score=75, etc.).

--- ANTI-PATTERNS ---
- Do NOT use plt.show() — always save to file and close figure with plt.close(fig)
- Do NOT hard-code output paths — use output_dir parameter
- Keep all matplotlib/mplfinance calls inside try/except and re-raise as ChartGenerationError
```


---

### PHASE 4 — STEP 3 of 5: `alerts/alert_deduplicator.py` + `alerts/telegram_alert.py` + `alerts/email_alert.py`

#### Context files to attach
- `rules/scorer.py` (SEPAResult)
- `storage/sqlite_store.py`
- `utils/logger.py`
- `utils/trading_calendar.py`
- `config/settings.yaml`

#### Prompt
```
You are building the alerts module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement THREE files:
  alerts/alert_deduplicator.py
  alerts/telegram_alert.py
  alerts/email_alert.py

--- FILE 1: alerts/alert_deduplicator.py ---

from rules.scorer import SEPAResult
from storage.sqlite_store import SQLiteStore

QUALITY_RANK = {"FAIL": 0, "C": 1, "B": 2, "A": 3, "A+": 4}

def should_alert(result: SEPAResult, db: SQLiteStore, config: dict) -> bool:
    """
    Returns True if this symbol should generate a new alert.

    Reads last alert record from SQLite `alert_history` table:
      CREATE TABLE IF NOT EXISTS alert_history (
          id INTEGER PRIMARY KEY,
          symbol TEXT NOT NULL,
          alerted_date DATE NOT NULL,
          score REAL NOT NULL,
          setup_quality TEXT NOT NULL,
          breakout_triggered INTEGER NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(symbol, alerted_date)
      )

    Re-alert conditions (ANY is sufficient):
      1. Symbol has never been alerted before (no row in alert_history)
      2. Days since last alert >= config["alerts"]["dedup_days"] (default: 3)
      3. Score improved by >= config["alerts"]["dedup_score_jump"] (default: 10)
      4. Setup quality improved (e.g. "B" → "A" or "A" → "A+")
      5. Breakout newly triggered: result.breakout_triggered=True AND prev was False

    Returns True if any condition is met. Returns False if within dedup window
    and none of conditions 3–5 are met.

    Config path: config.get("alerts", {})
    """

def record_alert(result: SEPAResult, db: SQLiteStore) -> None:
    """
    Persists alert record to alert_history table after a successful alert dispatch.
    Uses INSERT OR REPLACE on (symbol, alerted_date).
    """

--- FILE 2: alerts/telegram_alert.py ---

from rules.scorer import SEPAResult

def send_daily_watchlist(
    results: list[SEPAResult],
    chart_paths: dict[str, str],
    config: dict,
    run_date: date,
    watchlist_symbols: list[str] = None,
) -> int:
    """
    Sends Telegram messages for all A+/A results that pass deduplication.
    Watchlist symbols also sent if quality >= B (lower threshold).

    Message format per symbol:
      ★ WATCHLIST (if in watchlist)
      *DIXON* — A+ (Score: 91/100)
      Stage: Stage 2 — Advancing
      TT: ✅ 8/8 | VCP: ✅ | Breakout: 🔴 Triggered
      Entry: ₹14,200 | Stop: ₹13,100 | Risk: 7.7%
      RS Rating: 88

    Sends chart image as photo if chart_paths[symbol] exists.
    Sends text-only if no chart available.

    Uses python-telegram-bot (sync API: Bot.send_message + Bot.send_photo).
    TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from config or environment variables.

    Returns count of messages sent. Logs and continues on individual send failures.
    Skips if config["alerts"]["telegram"]["enabled"] is False.
    Skips if TELEGRAM_BOT_TOKEN is empty.

    Sends a summary message at the end:
      "📊 SEPA Screen — {run_date}
       A+: {n} | A: {n} | B: {n} | Total screened: {n}
       Next run: tomorrow at 15:35 IST"
    """

def send_error_alert(error_msg: str, config: dict) -> None:
    """
    Sends a simple text alert when the pipeline fails.
    Used by pipeline/runner.py in the except block.
    """

--- FILE 3: alerts/email_alert.py ---

def send_daily_summary(
    results: list[SEPAResult],
    html_report_path: str,
    config: dict,
    run_date: date,
) -> bool:
    """
    Sends HTML email summary with the watchlist report attached.
    Uses smtplib + email.mime (stdlib only — no third-party SMTP libraries).

    Subject: "SEPA Watchlist — {run_date} — {a_plus_count} A+ | {a_count} A setups"
    Body: Simple HTML with top-5 candidates table inline.
    Attachment: watchlist_{run_date}.html

    Returns True if sent, False if skipped (disabled in config or missing credentials).
    Skips silently if config["alerts"]["email"]["enabled"] is False.
    Logs SMTP errors but does not re-raise (graceful degradation).
    """

--- UNIT TESTS ---

Create `tests/unit/test_alert_deduplicator.py`:
1. No prior alert → should_alert returns True
2. Alert from yesterday (dedup_days=3) → returns False (all other conditions unmet)
3. Alert from 5 days ago (dedup_days=3) → returns True (condition 2)
4. Score jumped 12 points → returns True regardless of days (condition 3)
5. Quality improved A → A+ → returns True (condition 4)
6. Breakout newly triggered → returns True (condition 5)
7. record_alert persists row; second call with same date does not duplicate
8. should_alert reads from the in-memory SQLite DB (use `:memory:` in tests)

--- ANTI-PATTERNS ---
- Do NOT use async Telegram API — use synchronous python-telegram-bot send methods
- Never raise from telegram/email send functions — log errors and return False/0
- Config values are read from config dict, not direct os.environ (env loaded upstream)
- dedup check reads SQLite, not in-memory set — must survive process restarts
```


---

### PHASE 4 — STEP 4 of 5: `pipeline/runner.py` + `pipeline/scheduler.py`

#### Context files to attach
- `pipeline/context.py` (RunContext)
- `screener/pipeline.py` (run_screen)
- `screener/results.py` (persist_results)
- `reports/daily_watchlist.py` (generate_csv_report, generate_html_report)
- `reports/chart_generator.py` (generate_batch_charts)
- `alerts/alert_deduplicator.py`, `alerts/telegram_alert.py`
- `features/feature_store.py` (update, bootstrap, needs_bootstrap)
- `ingestion/universe_loader.py` (resolve_symbols)
- `storage/sqlite_store.py`
- `utils/trading_calendar.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the pipeline orchestrator for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `pipeline/runner.py` and `pipeline/scheduler.py`.

--- FILE 1: pipeline/runner.py ---

from pipeline.context import RunContext
from features.feature_store import update, bootstrap, needs_bootstrap
from screener.pipeline import run_screen
from screener.results import persist_results
from reports.daily_watchlist import generate_csv_report, generate_html_report, get_report_summary
from reports.chart_generator import generate_batch_charts
from alerts.alert_deduplicator import should_alert, record_alert
from alerts.telegram_alert import send_daily_watchlist, send_error_alert
from ingestion.universe_loader import resolve_symbols

def run_daily(ctx: RunContext) -> dict:
    """
    Main daily pipeline. Called by run_daily.py CLI and scheduler.

    Steps:
      1. resolve_symbols(ctx.config, ctx.cli_watchlist_file, ctx.cli_symbols, ctx.scope)
         → RunSymbols (watchlist + universe)
      2. Fetch today's OHLCV via source_factory.get_source(config).fetch_universe_batch()
      3. Append each symbol's OHLCV to data/processed/{symbol}.parquet
      4. For each symbol:
           if needs_bootstrap(symbol): bootstrap(symbol, config)  # self-healing
           else: update(symbol, run_date, config)
      5. Load benchmark_df (Nifty 500 index) and symbol_info from metadata
      6. run_screen(universe, run_date, config, symbol_info, benchmark_df)
         → list[SEPAResult]
      7. persist_results(results, db, run_date)
      8. generate_csv_report(results, output_dir, run_date, watchlist_symbols)
      9. generate_html_report(results, output_dir, run_date, watchlist_symbols)
     10. generate_batch_charts(results, ohlcv_data, vcp_data, output_dir, run_date)
     11. Filter results that pass should_alert()
     12. send_daily_watchlist(alertable, chart_paths, config, run_date, watchlist_symbols)
     13. record_alert() for each sent alert
     14. Log run_history to SQLite: duration, counts, git_sha, config_hash
     15. Return run summary dict

    Error handling:
      - Wrap step 2–14 in try/except; on failure: send_error_alert + log + re-raise
      - Individual symbol failures in step 3–4: log warning, skip symbol, continue
      - steps 8–13 failures: log error but do NOT abort (reports/alerts are non-critical)

    def run_historical(ctx: RunContext, start: date, end: date) -> list[dict]:
        '''Runs daily pipeline for each trading day in range. Returns list of summaries.'''

Returns: {
    "run_date": str, "duration_sec": float,
    "universe_size": int, "passed_stage2": int, "a_plus": int, "a": int,
    "report_csv": str, "report_html": str, "alerts_sent": int
}
    """

--- FILE 2: pipeline/scheduler.py ---

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from utils.trading_calendar import is_trading_day

def start_scheduler(config: dict) -> None:
    """
    Starts the APScheduler blocking scheduler.
    Runs run_daily() Mon–Fri at configured time (default 15:35 IST).

    Before running, checks is_trading_day(today) — skips on NSE holidays.
    Logs "Skipping: {date} is not an NSE trading day" when skipped.

    Schedule from config:
      config["scheduler"]["run_time"]  → "15:35" (HH:MM)
      config["scheduler"]["timezone"]  → "Asia/Kolkata"

    Runs the bootstrap check monthly (1st of month at 02:00 IST) for sanity:
      python scripts/bootstrap.py --universe all --dry-run
    """

def run_once_now(config: dict, scope: str = "all") -> dict:
    """
    Trigger a single run immediately (used by API's POST /api/v1/run).
    Returns the run summary dict from run_daily().
    """

--- UNIT TESTS ---

Create `tests/unit/test_runner.py`:
1. run_daily with mock universe of 2 symbols completes without error
2. Symbols with needs_bootstrap=True trigger bootstrap() not update()
3. Individual symbol OHLCV fetch failure logs warning and continues
4. Reports and alerts failure does not abort the run (non-critical steps)
5. run_historical over 3-day range calls run_daily 3 times (or fewer if holidays)

Mock all external calls: yfinance fetch, Telegram send, screener pipeline.
Use tmp_path fixture for output directories.

--- ANTI-PATTERNS ---
- run_daily must NEVER crash silently — either complete or raise with full traceback
- Never load the full feature history in runner.py — only the incremental update path
- Scheduler must check is_trading_day before every run — not just on startup
- run_daily returns a dict (not None) — the API layer reads this return value
```


---

### PHASE 4 — STEP 5 of 5: `paper_trading/simulator.py` + `portfolio.py` + `order_queue.py`

#### Context files to attach
- `rules/scorer.py` (SEPAResult)
- `utils/trading_calendar.py` (is_trading_day, next_trading_day)
- `utils/logger.py`
- `utils/exceptions.py`
- `config/settings.yaml`

#### Prompt
```
You are building the paper trading module for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `paper_trading/simulator.py`, `paper_trading/portfolio.py`,
and `paper_trading/order_queue.py`.

--- FILE 1: paper_trading/portfolio.py ---

from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Position:
    symbol: str
    entry_date: date
    entry_price: float
    quantity: int
    stop_loss: float
    target_price: float | None
    sepa_score: int
    setup_quality: str
    pyramided: bool = False          # True if pyramid add was made
    pyramid_qty: int = 0

@dataclass
class ClosedTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str    # "stop_loss" | "target" | "manual" | "end_of_backtest"
    r_multiple: float   # (exit_price - entry_price) / (entry_price - stop_loss)

class Portfolio:
    def __init__(self, initial_capital: float, config: dict):
        self.cash: float = initial_capital
        self.initial_capital: float = initial_capital
        self.positions: dict[str, Position] = {}     # symbol → Position
        self.closed_trades: list[ClosedTrade] = []
        self.config = config

    def add_position(self, position: Position) -> None: ...
    def close_position(self, symbol: str, exit_price: float, reason: str, exit_date: date) -> ClosedTrade: ...
    def get_open_value(self, current_prices: dict[str, float]) -> float: ...
    def get_total_value(self, current_prices: dict[str, float]) -> float: ...
    def get_summary(self, current_prices: dict[str, float]) -> dict: ...
        # Returns: cash, open_value, total_value, initial_capital,
        #   total_return_pct, realised_pnl, unrealised_pnl,
        #   win_rate, total_trades, open_count, closed_count,
        #   positions (list of dicts with unrealised P&L)

    def to_json(self) -> dict: ...   # for persistence to data/paper_trading/portfolio.json
    @classmethod
    def from_json(cls, data: dict, config: dict) -> "Portfolio": ...

--- FILE 2: paper_trading/order_queue.py ---

from utils.trading_calendar import is_trading_day, next_trading_day

ORDERS_FILE = "data/paper_trading/pending_orders.json"

def queue_order(symbol: str, order_type: str, result_dict: dict) -> None:
    """
    Persist a pending order to ORDERS_FILE.
    order_type: "BUY" | "SELL"
    result_dict: serialisable dict from SEPAResult
    Queued orders execute at next market open (9:15 IST).
    """

def execute_pending_orders(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    run_date: date,
) -> list[ClosedTrade | Position]:
    """
    Called at market open (9:15 IST, or at start of daily run).
    Executes all BUY orders at current_prices with slippage applied:
      fill_price = current_prices[symbol] * (1 + slippage_pct)
    Skips orders where current_prices[symbol] is not available.
    Clears executed orders from ORDERS_FILE.
    Returns list of opened/closed positions.
    """

def get_pending_orders() -> list[dict]: ...
def clear_pending_orders() -> None: ...

--- FILE 3: paper_trading/simulator.py ---

from paper_trading.portfolio import Portfolio, Position, ClosedTrade
from paper_trading.order_queue import queue_order, execute_pending_orders
from rules.scorer import SEPAResult
from utils.trading_calendar import is_trading_day

def enter_trade(
    result: SEPAResult,
    portfolio: Portfolio,
    current_price: float,
    run_date: date,
) -> Position | None:
    """
    Enter a new paper position for result.symbol.

    Pre-conditions (return None if any fail):
      - result.stage == 2
      - result.score >= config["paper_trading"]["min_score_to_trade"]  (default 70)
      - symbol NOT already in portfolio.positions
      - len(portfolio.positions) < config["paper_trading"]["max_positions"]  (default 10)
      - portfolio.cash > 0

    Position sizing:
      risk_amount = portfolio.get_total_value(current_prices) * (risk_per_trade_pct / 100)
      risk_per_share = current_price - result.stop_loss
      quantity = max(1, int(risk_amount / risk_per_share))

    Slippage:
      fill_price = current_price * (1 + slippage_pct)   # default 0.0015

    If is_trading_day(run_date) and within market hours (09:15–15:30):
      Create Position immediately
    Else:
      queue_order(symbol, "BUY", result_dict) for next open
      Return None (order queued, not filled yet)
    """

def pyramid_position(
    result: SEPAResult,
    portfolio: Portfolio,
    current_price: float,
    run_date: date,
) -> Position | None:
    """
    Add to an existing winning position.

    Conditions (ALL must be true):
      - symbol in portfolio.positions
      - NOT portfolio.positions[symbol].pyramided
      - result.setup_quality == "A"
      - result.vcp_qualified
      - VCP vol dry-up: row.get("vol_ratio", 1.0) < 0.4
      - current_price within 2% above result.entry_price (VCP pivot)

    Add quantity: 50% of original position (rounded down, min 1)
    Uses same slippage model as enter_trade.
    Sets position.pyramided = True to prevent second pyramid.
    Returns None if conditions not met (never raises).
    """

def check_exits(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    run_date: date,
) -> list[ClosedTrade]:
    """
    Check all open positions for stop-loss or target hits.
    close_position() if current_price <= stop_loss OR >= target_price.
    Returns list of ClosedTrade objects for this check.
    """

def reset_portfolio(confirm: bool = False) -> None:
    """
    Resets portfolio to initial state. Requires confirm=True.
    Deletes data/paper_trading/portfolio.json, trades.json, pending_orders.json.
    Called by Makefile `paper-reset` target.
    """

--- UNIT TESTS ---

Create `tests/unit/test_paper_trading.py`:
1. enter_trade: valid A+ result → Position created, cash reduced
2. enter_trade: position count at max → returns None
3. enter_trade: symbol already held → returns None
4. pyramid_position: already pyramided → returns None
5. pyramid_position: valid VCP Grade A + vol dry-up → position.pyramid_qty set
6. check_exits: price hits stop_loss → ClosedTrade with exit_reason=="stop_loss"
7. check_exits: price hits target → ClosedTrade with exit_reason=="target"
8. Portfolio.get_summary returns correct total_return_pct
9. Portfolio.to_json / from_json round-trip (no data loss)
10. Non-trading day → order queued, enter_trade returns None

--- ANTI-PATTERNS ---
- Never import from screener/, api/, or dashboard/ in paper_trading/
- Position sizing must cap at available cash (never allow negative cash balance)
- Pyramid add must check pyramided flag — max ONE pyramid per position ever
- Paper trading state is file-based (JSON) — no SQLite for portfolio state
```




---
---

## PHASE 5 — Fundamentals & News Sentiment (Weeks 13–14)
**Goal:** Add Minervini fundamental conditions and news sentiment as scoring inputs.

### Dependency order within Phase 5
```
Step 1: ingestion/fundamentals.py  (Screener.in scraper + 7-day cache)
Step 2: rules/fundamental_template.py  (7 Minervini conditions)
Step 3: ingestion/news.py  (RSS + NewsData.io + keyword + LLM scorer)
Step 4: Wire scores into rules/scorer.py + update reports/alerts
```

---

### PHASE 5 — STEP 1 of 4: `ingestion/fundamentals.py`

#### Context files to attach
- `utils/logger.py`
- `utils/exceptions.py`
- `config/settings.yaml`
- `PROJECT_DESIGN.md` (section 9 — Fundamentals Layer)

#### Prompt
```
You are building the fundamentals ingestion module for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `ingestion/fundamentals.py` — Screener.in scraper with 7-day cache.

--- CACHE STRATEGY ---
Cache file: data/fundamentals/{symbol}.json
TTL: 7 days (fundamentals change quarterly)
Cache check: compare fetched_at ISO timestamp vs. now. If within TTL, return cached.
force_refresh=True bypasses the cache check.

--- FUNCTION SIGNATURES ---

def fetch_fundamentals(symbol: str, force_refresh: bool = False) -> dict | None:
    """
    Fetch and cache fundamental data from Screener.in.

    URL patterns to try in order:
      1. https://www.screener.in/company/{symbol}/consolidated/
      2. https://www.screener.in/company/{symbol}/          (standalone fallback)

    Parse the following from HTML (use BeautifulSoup):
      pe_ratio         → "#top-ratios" li containing "Stock P/E"
      pb_ratio         → li containing "Price to Book"
      roe              → li containing "Return on Equity" (trailing twelve months)
      roce             → li containing "ROCE"
      debt_to_equity   → li containing "Debt to equity"
      promoter_holding → table with class "data-table" in the "Shareholding Pattern" section
      eps              → "Earning Per Share" in ratios
      eps_values       → last 4 quarterly EPS values from the "Quarterly Results" table
      eps_growth_rates → QoQ growth computed from eps_values
      eps_accelerating → True if eps_growth_rates[-1] > eps_growth_rates[-2]
      sales_growth_yoy → annual revenue growth (latest year vs prior year, %)
      profit_growth    → net profit growth YoY (%)
      fii_holding_pct  → FII holding % from shareholding table
      fii_trend        → "rising" | "flat" | "falling" based on last 3 quarters

    Returns None gracefully if:
      - HTTP request fails (timeout, 404, 403)
      - Parsing fails (site structure changed)
      Logs a warning, does NOT raise.

    Returned dict always includes:
      { "symbol": symbol, "fetched_at": ISO timestamp, ...all parsed fields... }

    Use requests with timeout=10. Add User-Agent header to avoid 403.
    Respect rate limits: sleep 0.5s between consecutive fetches.
    """

def get_fundamentals_age_days(symbol: str) -> float | None:
    """
    Returns age of cached fundamentals in days, or None if not cached.
    Used by pipeline/runner.py to show data freshness in reports.
    """

def clear_fundamentals_cache(symbol: str = None) -> None:
    """
    Clear cache for one symbol or all symbols (symbol=None).
    """

--- UNIT TESTS ---

Create `tests/unit/test_fundamentals.py`:
1. Cache miss → fetches URL (mock requests.get), saves to cache
2. Cache hit (< 7 days) → returns cached dict, no HTTP call
3. Cache expired (> 7 days) → fetches fresh data
4. HTTP 404 → returns None (no exception)
5. BeautifulSoup parse error → returns None (no exception)
6. force_refresh=True → fetches even if cache valid
7. Returned dict has "fetched_at" ISO timestamp
8. eps_accelerating=True when latest QoQ growth > previous
9. fii_trend="rising" when last 3 quarters show increasing FII%

Use tests/fixtures/sample_fundamentals.json as expected output fixture.
Mock all HTTP calls with responses library or unittest.mock.patch.

--- ANTI-PATTERNS ---
- Never call Screener.in in CI/unit tests — always mock requests.get
- Parsing must be defensive — wrap every field extraction in try/except
- Do NOT import from rules/, screener/, api/, or dashboard/
- The 0.5s sleep must be skippable in tests (inject or env-var controlled)
```


---

### PHASE 5 — STEP 2 of 4: `rules/fundamental_template.py`

#### Context files to attach
- `ingestion/fundamentals.py` (output dict structure)
- `utils/logger.py`
- `config/settings.yaml`
- `PROJECT_DESIGN.md` (section 9.3 and Appendix D)

#### Prompt
```
You are building the rule engine for a Minervini SEPA stock screening system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `rules/fundamental_template.py` — 7 Minervini fundamental conditions.

--- DATACLASS OUTPUT ---

from dataclasses import dataclass, field

@dataclass
class FundamentalResult:
    passes: bool                    # True only when ALL 7 conditions pass
    conditions_met: int             # 0–7
    f1_eps_positive: bool           # F1: latest EPS > 0
    f2_eps_accelerating: bool       # F2: most recent QoQ > previous QoQ
    f3_sales_growth: bool           # F3: sales growth >= min_sales_growth_yoy
    f4_roe: bool                    # F4: ROE >= min_roe
    f5_de_ratio: bool               # F5: D/E <= max_de
    f6_promoter_holding: bool       # F6: promoter_holding >= min_promoter_holding
    f7_profit_growth: bool          # F7: profit_growth > 0
    score: int                      # 0–100 (conditions_met / 7 * 100, rounded)
    hard_fails: list[str]           # names of conditions that failed (for reporting)
    values: dict = field(default_factory=dict)  # raw numeric values for each condition

--- FUNCTION SIGNATURE ---

def check_fundamental_template(
    fundamentals: dict | None,
    config: dict,
) -> FundamentalResult:
    """
    Evaluates 7 Minervini fundamental conditions.

    If fundamentals is None or empty:
      Return FundamentalResult with all conditions False,
      passes=False, conditions_met=0, score=0.
      Never raises.

    Config keys (all under "fundamentals.conditions"):
      min_roe: 15.0
      max_de: 1.0
      min_promoter_holding: 35.0
      min_sales_growth_yoy: 10.0

    F1: float(fundamentals.get("eps", 0)) > 0
    F2: fundamentals.get("eps_accelerating", False)
    F3: float(fundamentals.get("sales_growth_yoy", 0)) >= min_sales_growth_yoy
    F4: float(fundamentals.get("roe", 0)) >= min_roe
    F5: float(fundamentals.get("debt_to_equity", 99)) <= max_de
    F6: float(fundamentals.get("promoter_holding", 0)) >= min_promoter_holding
    F7: float(fundamentals.get("profit_growth", 0)) > 0

    Parse numeric values safely — strings like "12.5%" or "N/A" must be handled:
      def _parse_float(val) -> float:
          try: return float(str(val).replace("%","").replace(",","").strip())
          except: return 0.0

    values dict: {"eps": float, "roe": float, "de_ratio": float, ...}
    hard_fails: list of condition names that failed (e.g. ["F4_ROE", "F6_PROMOTER"])
    """

--- UNIT TESTS ---

Create `tests/unit/test_fundamental_template.py`:
1. All 7 conditions pass → passes=True, conditions_met=7, score=100
2. fundamentals=None → passes=False, conditions_met=0, no exception
3. F1 fails: eps="-0.5" → f1_eps_positive=False
4. F2 fails: eps_accelerating=False → f2_eps_accelerating=False
5. F3 fails: sales_growth_yoy="8.5" (< 10) → f3_sales_growth=False
6. F4 fails: roe="12.3%" (< 15) → f4_roe=False
7. F5 fails: debt_to_equity="1.5" (> 1.0) → f5_de_ratio=False
8. F6 fails: promoter_holding="30.1%" (< 35) → f6_promoter_holding=False
9. hard_fails list matches failed conditions
10. String values with "%" and "," parse correctly (e.g. "1,234.5" → 1234.5)
11. Custom config: min_roe=20.0 → stock with roe=16 fails F4

Use tests/fixtures/sample_fundamentals.json to build test dicts.
```


---

### PHASE 5 — STEP 3 of 4: `ingestion/news.py`

#### Context files to attach
- `utils/logger.py`
- `utils/exceptions.py`
- `config/settings.yaml`
- `config/symbol_aliases.yaml`
- `PROJECT_DESIGN.md` (section 10 — News Sentiment Layer)

#### Prompt
```
You are building the news ingestion module for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `ingestion/news.py` — RSS feed fetcher + keyword scorer + LLM re-scorer.

--- CACHE STRATEGY ---
Cache file: data/news/market_news.json
TTL: 30 minutes (news changes frequently)
Cache check: compare fetched_at vs now. If within TTL, return cached articles.

--- RSS FEEDS (from config["news"]["rss_feeds"]) ---
Default feeds:
  - https://www.moneycontrol.com/rss/marketreports.xml
  - https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms
  - https://www.business-standard.com/rss/markets-106.rss
  - https://www.moneycontrol.com/rss/business.xml

--- KEYWORD LISTS ---
BULLISH_KEYWORDS = [
    "surge", "rally", "upgrade", "order win", "buyback", "dividend",
    "record high", "expansion", "profit rise", "strong earnings",
    "outperform", "beat estimates", "acquisition", "deal win",
]
BEARISH_KEYWORDS = [
    "probe", "fraud", "miss", "downgrade", "resignation", "sebi notice",
    "loss", "decline", "weak", "disappoints", "below estimates",
    "penalty", "lawsuit", "downgrade", "margin pressure",
]

--- FUNCTION SIGNATURES ---

def fetch_market_news(force_refresh: bool = False) -> list[dict]:
    """
    Fetch RSS articles from all configured feeds.
    Cache for 30 minutes in data/news/market_news.json.

    Each article dict:
      {
        "title": str, "description": str, "link": str,
        "published": str (ISO), "source": str (feed domain),
        "keyword_sentiment": str ("bullish" | "bearish" | "neutral"),
        "keyword_score": float  (-1.0 to +1.0, keyword-only)
      }

    Parsing: use feedparser library.
    On individual feed failure: log warning, skip feed, continue with others.
    Returns empty list if all feeds fail.
    """

def fetch_symbol_news(
    symbol: str,
    all_news: list[dict] | None = None,
    use_llm: bool = True,
    config: dict = None,
) -> list[dict]:
    """
    Filter market_news for articles mentioning the symbol (via alias matching).
    Re-scores matched articles with LLM for better accuracy (if use_llm=True and LLM available).
    Falls back to keyword_score if LLM unavailable.

    Symbol alias lookup from config/symbol_aliases.yaml.
    Case-insensitive matching in title + description.

    Returns list of matched article dicts, each with added:
      "llm_sentiment": str | None
      "llm_score": float | None   (-1.0 to +1.0)
      "final_score": float        (llm_score if available, else keyword_score)
    """

def compute_news_score(articles: list[dict]) -> float:
    """
    Aggregate article sentiments into a -100 to +100 score.
    Method: weighted average of final_score × 100.
    More recent articles weighted higher (decay factor: 0.9 per day).
    Returns 0.0 if articles list is empty.
    """

def _keyword_score_article(article: dict) -> float:
    """
    Fast keyword scoring for a single article. Returns -1.0 to +1.0.
    bullish_count - bearish_count, normalised to [-1, 1].
    """

--- LLM INTEGRATION ---

For LLM re-scoring, call the LLM with a minimal prompt:
  "Rate the sentiment of this financial news article for stock {symbol}.
   Title: {title}
   Description: {description[:300]}
   
   Respond with ONLY a JSON object: {"sentiment": "bullish|bearish|neutral", "score": float}
   where score is -1.0 (very bearish) to +1.0 (very bullish).
   Consider context — negative news about a competitor may be bullish for the subject."

Use config["llm"]["provider"] to call the appropriate LLM client (from Phase 6).
If llm not available (import error or no API key): fall back to keyword scoring silently.

--- UNIT TESTS ---

Create `tests/unit/test_news.py`:
1. fetch_market_news: mock feedparser → returns list of article dicts with keyword_sentiment
2. Cache hit (< 30 min) → returns cached, no HTTP call
3. Cache expired → fetches fresh
4. One feed fails → logs warning, returns articles from other feeds
5. fetch_symbol_news: article mentioning "reliance industries" matches symbol "RELIANCE"
6. fetch_symbol_news: alias from symbol_aliases.yaml used for matching
7. compute_news_score: all bullish articles → score > 0
8. compute_news_score: empty list → 0.0
9. use_llm=False → keyword scoring only (no LLM import needed)
10. LLM unavailable → falls back to keyword_score, no exception

Mock all HTTP calls. Use tests/fixtures/sample_news_articles.json (create this fixture).

--- ANTI-PATTERNS ---
- Never call LLM for keyword scoring — LLM only for re-scoring symbol-matched articles
- Never use regex for alias matching — use str.lower().find() for performance
- News scoring must be usable without LLM (use_llm=False path must be fully functional)
- Cache is global (market_news.json) — do NOT create per-symbol news files
```


---

### PHASE 5 — STEP 4 of 4: Wire fundamental + news scores into scorer + update reports

#### Context files to attach
- `rules/scorer.py` (current SEPAResult + score_symbol)
- `rules/fundamental_template.py` (FundamentalResult)
- `ingestion/news.py` (compute_news_score)
- `reports/daily_watchlist.py` (generate_html_report)
- `alerts/telegram_alert.py` (send_daily_watchlist)
- `pipeline/runner.py` (run_daily)

#### Prompt
```
You are wiring Phase 5 data into the existing rule engine and reports.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Update 4 existing files to incorporate fundamental and news scores.

--- FILE 1: Update rules/scorer.py ---

The score_symbol() function already accepts fundamental_result and news_score params.
Now wire them properly:

1. If fundamentals.enabled=True in config:
   Call check_fundamental_template(fundamental_result, config) → FundamentalResult
   Set result.fundamental_pass = fundamental_result.passes
   Set result.fundamental_details = fundamental_result.__dict__
   fundamental_score = fundamental_result.score  (already 0–100)
   If fundamentals.hard_gate=True AND not fundamental_result.passes:
     Downgrade quality to "FAIL" (but keep score for reporting)

2. If news.enabled=True in config and news_score is not None:
   news_score_norm = (news_score + 100) / 2  (map -100..+100 to 0..100)
   result.news_score = news_score

3. These were using neutral defaults (50) in Phase 3.
   Now use real values when available, neutral 50 when None.

No other changes to score_symbol(). The weights in SCORE_WEIGHTS remain unchanged.

--- FILE 2: Update pipeline/runner.py ---

After step 4 (feature update), add for each candidate that passed pre_filter:

  if config.get("fundamentals", {}).get("enabled", True):
      fundamentals = fetch_fundamentals(symbol)
  else:
      fundamentals = None

  if config.get("news", {}).get("enabled", True):
      all_news = fetch_market_news()  # already cached — only 1 HTTP call total
      symbol_articles = fetch_symbol_news(symbol, all_news, use_llm=True)
      news_score = compute_news_score(symbol_articles)
  else:
      news_score = None

Pass these to run_screen() as additional parameters (or add to the feature index).

--- FILE 3: Update reports/daily_watchlist.py + watchlist.html.j2 ---

Add to the HTML template:
  - Fundamental conditions card per candidate (7 F-conditions as pass/fail checklist)
  - EPS acceleration badge (green ▲ or grey —)
  - News score indicator (🟢 Positive / 🔴 Negative / ⚪ Neutral)
  - FII trend label (Rising / Flat / Falling)

If fundamental_details is empty dict or passes=False, show "Fundamentals: N/A".

--- FILE 4: Update alerts/telegram_alert.py ---

Add to each alert message:
  EPS: ▲ Accelerating | ROE: 24.3% | D/E: 0.4 | Promoter: 52.1%
  News: 🟢 Positive (+42)

If fundamentals not available: skip the line entirely.
Keep total message under 1000 chars (Telegram limit).

--- UNIT TESTS ---

Update `tests/unit/test_scorer.py`:
1. fundamental_result with passes=True → fundamental_pass=True in SEPAResult
2. fundamentals.hard_gate=True + fundamentals failed → quality forced to "FAIL"
3. news_score=-80 → news_score_norm=10, penalises overall score
4. fundamental_result=None → neutral score (50) used, no exception
5. news_score=None → neutral score (50) used, no exception

Update `tests/unit/test_daily_watchlist.py`:
6. HTML report with SEPAResult having fundamental_details → shows F-conditions table
7. HTML report with empty fundamental_details → shows "N/A" block, no crash
```




---
---

## PHASE 6 — LLM Narrative Layer (Weeks 15–16)
**Goal:** AI-generated trade briefs as an optional overlay on top of the deterministic rule engine.

### Dependency order within Phase 6
```
Step 1: llm/llm_client.py  (abstract base + all 5 provider implementations)
Step 2: llm/prompt_templates/trade_brief.j2 + watchlist_summary.j2
Step 3: llm/explainer.py  (generate_trade_brief + generate_watchlist_summary)
Step 4: Wire into reports/daily_watchlist.py + pipeline/runner.py
```

---

### PHASE 6 — STEP 1 of 4: `llm/llm_client.py` — Multi-Provider LLM Client

#### Context files to attach
- `utils/logger.py`
- `utils/exceptions.py`
- `config/settings.yaml`

#### Prompt
```
You are building the LLM client abstraction for a Minervini SEPA stock system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `llm/llm_client.py` with abstract base class and 5 provider adapters.

--- ABSTRACT BASE ---

from abc import ABC, abstractmethod

class LLMClient(ABC):
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """
        Send prompt and return response text.
        Raises LLMError (from utils/exceptions.py) on unrecoverable failure.
        Raises LLMUnavailableError if API key missing or service unreachable.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True if client is configured and reachable."""

    def complete_with_fallback(self, prompt: str, fallback: str = "", max_tokens: int = 350) -> str:
        """
        Calls complete(). Returns fallback string on any exception.
        Logs a warning with the error. Never raises.
        """

--- PROVIDER IMPLEMENTATIONS ---

All read config from their respective init params. API keys from os.environ (not config dict).

class GroqClient(LLMClient):
    """
    Uses Groq API with model from config (default: llama-3.3-70b-versatile).
    API key: os.environ["GROQ_API_KEY"]
    Endpoint: https://api.groq.com/openai/v1/chat/completions
    Use openai library with base_url override (Groq is OpenAI-compatible).
    """

class AnthropicClient(LLMClient):
    """
    Uses Anthropic Claude API.
    API key: os.environ["ANTHROPIC_API_KEY"]
    Default model: claude-haiku-4-5 (cheapest, fastest)
    Use anthropic library: client.messages.create()
    """

class OpenAIClient(LLMClient):
    """
    Uses OpenAI API.
    API key: os.environ["OPENAI_API_KEY"]
    Default model: gpt-4o-mini
    """

class OpenRouterClient(LLMClient):
    """
    Uses OpenRouter API (deepseek-r1:free by default — best reasoning, free tier).
    API key: os.environ["OPENROUTER_API_KEY"]
    Endpoint: https://openrouter.ai/api/v1/chat/completions
    OpenAI-compatible format with base_url override.
    Extra headers: {"HTTP-Referer": "https://github.com/sepa-ai"}
    """

class OllamaClient(LLMClient):
    """
    Local Ollama instance (zero API cost).
    No API key needed.
    Endpoint: http://localhost:11434/api/chat
    Default model: from config["llm"]["model"] (e.g. "llama3.2")
    is_available(): check if localhost:11434 is reachable (quick timeout=1s).
    """

--- FACTORY FUNCTION ---

CLIENTS = {
    "groq":       GroqClient,
    "anthropic":  AnthropicClient,
    "openai":     OpenAIClient,
    "openrouter": OpenRouterClient,
    "ollama":     OllamaClient,
}

def get_llm_client(config: dict) -> LLMClient:
    """
    Returns the configured LLM client.
    Falls back to OllamaClient if configured provider fails is_available() check.
    Falls back to None if Ollama also unavailable (explainer handles None gracefully).
    """

--- TOKEN COST LOGGING ---

Add to each provider's complete() method:
  logger.debug(f"LLM [{provider}] prompt={len(prompt)} chars, response={len(response)} chars")

Maintain a module-level counter for total tokens used per session:
  _session_tokens = {"prompt": 0, "completion": 0}

def get_session_token_usage() -> dict:
    """Returns current session token usage estimate."""

--- UNIT TESTS ---

Create `tests/unit/test_llm_client.py`:
1. get_llm_client with provider="groq" → returns GroqClient instance
2. get_llm_client with missing GROQ_API_KEY → falls back to OllamaClient
3. complete_with_fallback: LLM raises exception → returns fallback string, no re-raise
4. OllamaClient.is_available(): mock requests → True if 200, False if connection error
5. Each provider class instantiates without error even with empty API key
   (lazy validation — only fail on actual complete() call)
6. Mock GroqClient.complete() → returns string → test that get_session_token_usage increments

Do NOT call real LLM APIs in tests — mock all HTTP calls.
```


---

### PHASE 6 — STEP 2 of 4: Prompt Templates

#### Context files to attach
- `rules/scorer.py` (SEPAResult dataclass — all fields)
- `PROJECT_DESIGN.md` (section 8.2 — Trade Brief Template)

#### Prompt
```
You are building the Jinja2 prompt templates for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Create TWO Jinja2 templates in llm/prompt_templates/:

--- FILE 1: llm/prompt_templates/trade_brief.j2 ---

Create a Jinja2 template that renders a prompt for generating a trade brief.
The rendered prompt will be sent directly to an LLM.

Template variables available (from SEPAResult + context):
  symbol, run_date, setup_quality, score, stage_label, stage_confidence,
  conditions_met, trend_template_pass, vcp_qualified, vcp_details,
  rs_rating, breakout_triggered, entry_price, stop_loss, risk_pct,
  reward_risk_ratio, fundamental_pass, fundamental_details,
  news_score, sector_bonus

Template must:
  1. Open with a clear role instruction (analyst explaining a SEPA setup)
  2. Present the structured data in a clean, readable format
  3. Explicitly instruct the LLM:
     - Write 3–4 sentences maximum
     - Focus on what the chart/technicals are saying
     - Do NOT make a buy/sell recommendation
     - Do NOT change the setup_quality rating
     - Tone: professional and factual
     - Mention the stage, VCP status, RS ranking, and breakout status
  4. Include fundamental summary if fundamental_pass is not None
  5. Include news sentiment if news_score is not None
  6. End with: "Respond with ONLY the trade brief, no headers, no lists."

--- FILE 2: llm/prompt_templates/watchlist_summary.j2 ---

Template for generating a daily watchlist narrative summary.
Variables: run_date, a_plus_count, a_count, b_count, market_mood,
           top_candidates (list of dicts with symbol, score, quality, stage_label),
           sector_leaders (list of top sectors by RS rank)

Template must instruct LLM to:
  - Write a 2-paragraph daily market commentary
  - Paragraph 1: market breadth observation (how many A+/A/B setups, vs recent average)
  - Paragraph 2: highlight 2–3 notable setups from top_candidates (without recommending)
  - Mention leading sectors if provided
  - Keep total under 200 words
  - Tone: factual, not hype, not doom

--- UNIT TESTS ---

Create `tests/unit/test_prompt_templates.py`:
1. trade_brief.j2 renders without error with a minimal SEPAResult dict
2. Rendered trade_brief prompt contains "Do NOT make a buy/sell recommendation"
3. trade_brief with fundamental_pass=None → no fundamental section in output
4. trade_brief with news_score provided → news_score mentioned in output
5. watchlist_summary.j2 renders without error with 3 top_candidates
6. Both templates render to non-empty strings (> 100 chars)

Use Jinja2 Environment with FileSystemLoader pointing to llm/prompt_templates/.
```


---

### PHASE 6 — STEP 3 of 4: `llm/explainer.py`

#### Context files to attach
- `llm/llm_client.py` (LLMClient, get_llm_client)
- `rules/scorer.py` (SEPAResult)
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the LLM explainer for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `llm/explainer.py` — narrative generator using Jinja2 templates + LLM client.

--- FUNCTION SIGNATURES ---

def generate_trade_brief(
    result: SEPAResult,
    ohlcv_tail: pd.DataFrame,
    config: dict,
    client: LLMClient | None = None,
) -> str | None:
    """
    Generate a plain-English trade brief for a single SEPA setup.

    Steps:
      1. Only generate for quality in config["llm"]["only_for_quality"] (default: ["A+","A"])
         Return None immediately for other qualities (cost saving).
      2. If client is None: try get_llm_client(config). If still None: return None.
      3. Render trade_brief.j2 with dataclasses.asdict(result) + ohlcv context
      4. Add 5-row OHLCV summary to context: recent_prices (list of {date,close,vol_ratio})
      5. Call client.complete_with_fallback(prompt, fallback=None, max_tokens=350)
      6. Validate response: non-empty, under 600 chars, no JSON/code blocks
         If validation fails: log warning, return None
      7. Return the clean narrative string

    ohlcv_tail: last 5 rows of processed OHLCV (date, close, volume columns)
    """

def generate_watchlist_summary(
    results: list[SEPAResult],
    run_date: date,
    config: dict,
    client: LLMClient | None = None,
) -> str | None:
    """
    Generate a 2-paragraph daily watchlist commentary.
    Uses watchlist_summary.j2 template.
    Only called if config["llm"]["enabled"] is True.
    Returns None gracefully if LLM unavailable.
    """

def generate_batch_briefs(
    results: list[SEPAResult],
    ohlcv_data: dict[str, pd.DataFrame],
    config: dict,
) -> dict[str, str]:
    """
    Generate trade briefs for all qualifying results.
    Uses single LLM client instance for the whole batch.
    Returns { symbol: brief_text } (only for symbols that got a brief).
    Logs total token usage at the end.
    """

--- UNIT TESTS ---

Create `tests/unit/test_explainer.py`:
1. generate_trade_brief with quality="C" → returns None (below threshold)
2. generate_trade_brief with quality="A+" + mock LLM → returns non-empty string
3. LLM returns empty string → returns None (validation fail)
4. LLM returns JSON block → returns None (validation: no code blocks)
5. client=None + no configured LLM → returns None, no exception
6. generate_batch_briefs: 3 A+ results → 3 entries in returned dict
7. generate_batch_briefs: individual LLM failure → skips that symbol, continues

Mock LLMClient.complete() to return a fixed string like "DIXON shows a classic VCP..."
```


---

### PHASE 6 — STEP 4 of 4: Wire LLM briefs into reports + pipeline

#### Context files to attach
- `llm/explainer.py` (generate_batch_briefs, generate_watchlist_summary)
- `reports/daily_watchlist.py` (generate_html_report)
- `pipeline/runner.py` (run_daily)

#### Prompt
```
You are wiring Phase 6 LLM briefs into existing modules.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Update 2 existing files to incorporate LLM trade briefs.

--- FILE 1: Update pipeline/runner.py ---

After step 10 (generate_batch_charts), add:

  if config.get("llm", {}).get("enabled", False):
      # Load ohlcv_data dict for qualifying symbols (A+/A only)
      qualifying = [r for r in results if r.setup_quality in ("A+", "A")]
      ohlcv_data = {s: read_last_n_rows(features_dir / f"{s}.parquet", 5)
                    for s in [r.symbol for r in qualifying]}
      llm_briefs = generate_batch_briefs(qualifying, ohlcv_data, config)
      watchlist_summary = generate_watchlist_summary(results, run_date, config)
  else:
      llm_briefs = {}
      watchlist_summary = None

Pass llm_briefs to generate_html_report() and watchlist_summary as report header.
Add "llm_briefs_generated": len(llm_briefs) to the run summary dict.

--- FILE 2: Update reports/daily_watchlist.py + watchlist.html.j2 ---

generate_html_report() already accepts llm_briefs parameter (added in Phase 4 Step 1).
Now actually render the brief when it's present:

In the HTML template, add below each candidate row:
  {% if llm_briefs[symbol] %}
  <div class="trade-brief">
    <strong>💬 Trade Brief</strong>
    <p>{{ llm_briefs[symbol] }}</p>
  </div>
  {% endif %}

At the top of the report, add the watchlist_summary if provided:
  <div class="market-commentary">
    <h2>📊 Daily Market Commentary</h2>
    <p>{{ watchlist_summary }}</p>
  </div>

--- UNIT TESTS ---

Update `tests/unit/test_daily_watchlist.py`:
1. generate_html_report with llm_briefs dict → brief appears in HTML output
2. generate_html_report with llm_briefs={} → no brief section, no crash
3. generate_html_report with watchlist_summary → commentary section in HTML
4. Brief text is HTML-escaped (< > & are safe in Jinja2 with |e filter)

--- KEY DESIGN RULE ---
The LLM brief is cosmetic. If llm.enabled=False or LLM is unavailable,
run_daily() continues normally without briefs. Never fail the pipeline
because of LLM unavailability. The brief is always an optional annotation.
```




---
---

## PHASE 7 — Paper Trading Simulator (Full) (Weeks 17–18)
**Goal:** Complete paper trading with pyramiding, full order queue, and performance reporting.
Phase 4 Step 5 built the basic simulator. Phase 7 completes it.

### Dependency order within Phase 7
```
Step 1: Complete paper_trading/simulator.py — pyramiding + full exit logic
Step 2: paper_trading/portfolio.py — complete P&L + equity curve
Step 3: paper_trading/order_queue.py — full market-hours queue
Step 4: paper_trading/report.py — performance summary report
```

---

### PHASE 7 — STEP 1 of 4: Complete `paper_trading/simulator.py`

#### Context files to attach
- `paper_trading/portfolio.py` (Portfolio, Position, ClosedTrade from Phase 4)
- `paper_trading/order_queue.py` (queue_order, execute_pending_orders from Phase 4)
- `rules/scorer.py` (SEPAResult)
- `utils/trading_calendar.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are completing the paper trading simulator for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Extend the existing `paper_trading/simulator.py` from Phase 4 with:
  - Full trailing stop logic
  - Position pyramid add improvements
  - Exit reason tracking
  - Integration with pipeline/runner.py

--- NEW FUNCTION: apply_trailing_stop ---

def apply_trailing_stop(
    position: Position,
    current_price: float,
    config: dict,
) -> float:
    """
    Computes the current trailing stop price.
    trailing_stop_pct = config["backtest"]["trailing_stop_pct"]  (default 0.07)

    Logic:
      trailing = peak_close * (1 - trailing_stop_pct)
      floored at position.stop_loss  (VCP base_low — NEVER drops below this)

    peak_close is tracked on the Position object (add peak_close: float field).
    Called every check_exits() cycle.

    Returns the higher of: VCP floor stop OR trailing calculation.
    Never allows stop to DECREASE — only moves upward.
    """

--- UPDATE Position DATACLASS ---

Add to Position:
  peak_close: float          # tracks highest close since entry (for trailing stop)
  trailing_stop: float       # current trailing stop (updated each check_exits call)
  days_held: int = 0

--- UPDATE check_exits ---

def check_exits(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    run_date: date,
) -> list[ClosedTrade]:
    """
    For each open position:
      1. Update position.peak_close = max(peak_close, current_price)
      2. Update position.days_held += 1
      3. Compute trailing stop via apply_trailing_stop()
      4. Update position.trailing_stop
      5. Exit if:
           current_price <= position.trailing_stop  → exit_reason="trailing_stop"
           current_price >= position.target_price   → exit_reason="target"
           days_held > max_hold_days                → exit_reason="max_hold_days"
      6. Apply brokerage on exit: pnl reduced by (exit_price * qty * brokerage_pct)
         brokerage_pct = config["paper_trading"]["brokerage_pct"]  (default 0.0005)
    """

--- PERSIST TRADES ---

def save_state(portfolio: Portfolio) -> None:
    """
    Persist portfolio + closed trades to JSON files:
      data/paper_trading/portfolio.json  → portfolio.to_json()
      data/paper_trading/trades.json     → [t.__dict__ for t in portfolio.closed_trades]
    Atomic write: write to .tmp file, then rename.
    """

def load_state(config: dict) -> Portfolio:
    """
    Load portfolio from data/paper_trading/portfolio.json.
    If file missing, return fresh Portfolio(initial_capital, config).
    """

--- UNIT TESTS ---

Create `tests/unit/test_trailing_stop.py`:
1. Trailing stop moves up as price rises
2. Trailing stop never drops below VCP floor (position.stop_loss)
3. Trailing stop does not decrease on price pullback
4. Exit triggered when price drops below trailing stop
5. Exit NOT triggered while price stays above trailing stop

Update `tests/unit/test_paper_trading.py`:
6. check_exits respects brokerage deduction in pnl
7. max_hold_days exit: days_held > 20 → ClosedTrade with exit_reason="max_hold_days"
8. save_state / load_state round-trip preserves all positions and trades
9. load_state with missing file → returns fresh portfolio (no exception)
```


---

### PHASE 7 — STEP 2 of 4: Complete `paper_trading/portfolio.py`

#### Context files to attach
- `paper_trading/portfolio.py` (current version from Phase 4)
- `utils/logger.py`

#### Prompt
```
You are completing the portfolio module for the paper trading simulator.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Extend `paper_trading/portfolio.py` with equity curve tracking and full P&L.

--- ADD equity_curve TO Portfolio ---

Add to Portfolio.__init__:
  self.equity_curve: list[dict] = []
  # Each entry: {"date": str(date), "total_value": float, "cash": float}

def record_equity_point(self, current_prices: dict, run_date: date) -> None:
    """
    Appends a daily equity snapshot to equity_curve.
    Called once per day by pipeline/runner.py after check_exits.
    """

--- UPDATE get_summary ---

def get_summary(self, current_prices: dict[str, float]) -> dict:
    """
    Returns full portfolio summary including:
      cash, open_value, total_value, initial_capital,
      total_return_pct, realised_pnl, unrealised_pnl,
      win_rate, total_trades, open_count, closed_count,
      avg_r_multiple (average R-multiple of closed trades),
      profit_factor (sum of winning trades / abs sum of losing trades),
      best_trade_pct, worst_trade_pct, avg_hold_days,
      positions: list[{symbol, entry_price, current_price, unrealised_pnl_pct,
                        days_held, stop_loss, trailing_stop, quality}]
    """

def get_r_multiple(trade: ClosedTrade) -> float:
    """
    R = (exit_price - entry_price) / (entry_price - stop_loss)
    Returns 0.0 if entry_price == stop_loss.
    """

--- UNIT TESTS ---

Add to `tests/unit/test_paper_trading.py`:
1. record_equity_point appends daily snapshot to equity_curve
2. get_summary.win_rate = 0.67 for 2 wins + 1 loss
3. get_summary.profit_factor = sum_wins / abs_sum_losses (correct ratio)
4. get_summary.avg_r_multiple computes correctly from 3 closed trades
5. get_summary with 0 closed trades → win_rate=0, profit_factor=0 (no division error)
6. Portfolio.to_json() includes equity_curve; from_json() restores it
```


---

### PHASE 7 — STEP 3 of 4: Complete `paper_trading/order_queue.py`

#### Context files to attach
- `paper_trading/portfolio.py`
- `utils/trading_calendar.py`
- `utils/logger.py`

#### Prompt
```
You are completing the order queue for the paper trading simulator.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Extend `paper_trading/order_queue.py` with expiry logic and full execution.

ORDERS_FILE = "data/paper_trading/pending_orders.json"
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

def is_market_open(dt: datetime | None = None) -> bool:
    """
    Returns True if current IST time is within NSE market hours (9:15–15:30)
    AND today is an NSE trading day.
    dt parameter for testing (defaults to datetime.now(IST)).
    """

def queue_order(
    symbol: str,
    order_type: str,
    result_dict: dict,
    expiry_days: int = 3,
) -> None:
    """
    Append pending order to ORDERS_FILE.
    Each order: { symbol, order_type, result_dict, queued_at, expiry_date }
    expiry_date = queued_at + expiry_days trading days.
    """

def execute_pending_orders(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    run_date: date,
) -> list[Position]:
    """
    Called at start of each daily run (simulating market open).
    For each pending BUY order:
      - Check not expired (expiry_date >= run_date)
      - Check symbol in current_prices
      - Check portfolio has capacity (positions < max_positions)
      - Fill at current_prices[symbol] * (1 + slippage_pct)
      - Remove from queue after execution (success or expiry)
    Returns list of newly opened Positions.
    Logs expired orders with "Order expired: {symbol} queued {days} ago"
    """

--- UNIT TESTS ---

Add to `tests/unit/test_paper_trading.py`:
1. is_market_open at 10:00 IST on trading day → True
2. is_market_open at 16:00 IST → False (after close)
3. is_market_open on NSE holiday → False
4. queue_order: order appears in ORDERS_FILE with expiry_date
5. execute_pending_orders: valid order → Position added to portfolio
6. Expired order (3 days old with expiry_days=3) → skipped, removed from queue
7. Order for symbol not in current_prices → skipped, stays in queue
```


---

### PHASE 7 — STEP 4 of 4: `paper_trading/report.py`

#### Context files to attach
- `paper_trading/portfolio.py` (Portfolio, ClosedTrade)
- `utils/logger.py`

#### Prompt
```
You are building the paper trading performance report module.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `paper_trading/report.py` — performance summary + equity curve report.

--- FUNCTION SIGNATURES ---

def generate_performance_report(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    output_dir: str,
    run_date: date,
) -> str:
    """
    Generates a comprehensive performance report.
    Writes to: {output_dir}/paper_trading_{run_date}.html

    Sections:
      1. Summary cards: Total Return %, Realised P&L, Win Rate, Avg R-Multiple, 
                        Profit Factor, Total Trades, Open Positions
      2. Equity curve chart (matplotlib, save as base64 PNG, embed in HTML)
      3. Open positions table: symbol, entry, current, unrealised P&L%, days held, stop
      4. Closed trades table: symbol, entry date, exit date, entry price, exit price, 
                              P&L%, R-multiple, exit reason
      5. Quality breakdown: win rate by setup_quality (A+, A, B, C)
      6. Hold time distribution: histogram of days_held for closed trades

    Returns path to generated HTML file.
    Uses Jinja2 or f-string template (no external CSS).
    """

def get_quality_breakdown(trades: list[ClosedTrade]) -> dict:
    """
    Returns win rate and avg R-multiple grouped by setup_quality.
    { "A+": {"trades": 5, "wins": 4, "win_rate": 0.80, "avg_r": 2.1}, ... }
    """

def get_monthly_pnl(trades: list[ClosedTrade]) -> dict[str, float]:
    """
    Returns P&L grouped by month: { "2024-01": 12500.0, "2024-02": -3200.0, ... }
    Uses exit_date for grouping.
    """

--- UNIT TESTS ---

Create `tests/unit/test_paper_report.py`:
1. generate_performance_report with 5 closed trades + 2 open → HTML file created
2. get_quality_breakdown: 3 A+ trades (2 wins, 1 loss) → win_rate=0.667
3. get_monthly_pnl groups by month correctly
4. Empty trades → report generates with "No closed trades yet" section
5. Equity curve section is present in HTML (contains <img> or <canvas> tag)
```




---
---

## PHASE 8 — Backtesting Engine (Weeks 19–22)
**Goal:** Walk-forward backtester with trailing stops, regime labelling, and full metrics.

### Dependency order within Phase 8
```
Step 1: backtest/regime.py  (Bull/Bear/Sideways labelling)
Step 2: backtest/engine.py  (walk-forward backtester — simulate_trade + run_backtest)
Step 3: backtest/portfolio.py + backtest/metrics.py
Step 4: backtest/report.py + scripts/backtest_runner.py
Step 5: Integration test + parameter sweep
```

---

### PHASE 8 — STEP 1 of 5: `backtest/regime.py`

#### Context files to attach
- `utils/trading_calendar.py`
- `utils/logger.py`
- `PROJECT_DESIGN.md` (Appendix E — NSE Market Regime Calendar)

#### Prompt
```
You are building the market regime module for the backtesting engine.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `backtest/regime.py` — Bull/Bear/Sideways labelling.

--- REGIME CALENDAR (from Appendix E) ---

NSE_REGIME_CALENDAR = [
    {"start": "2014-05-01", "end": "2018-01-31", "regime": "Bull",
     "rationale": "Modi wave + GST + recovery"},
    {"start": "2018-02-01", "end": "2019-03-31", "regime": "Sideways",
     "rationale": "IL&FS crisis, NBFC stress, mid-cap collapse"},
    {"start": "2019-04-01", "end": "2020-01-31", "regime": "Bull",
     "rationale": "Pre-COVID recovery"},
    {"start": "2020-02-01", "end": "2020-03-31", "regime": "Bear",
     "rationale": "COVID crash"},
    {"start": "2020-04-01", "end": "2021-12-31", "regime": "Bull",
     "rationale": "V-shaped recovery, liquidity rally"},
    {"start": "2022-01-01", "end": "2022-12-31", "regime": "Sideways",
     "rationale": "Fed rate hikes, FII selling"},
    {"start": "2023-01-01", "end": "2024-09-30", "regime": "Bull",
     "rationale": "Earnings recovery, domestic flows"},
    {"start": "2024-10-01", "end": "2025-03-31", "regime": "Sideways",
     "rationale": "Global uncertainty"},
    # After 2025-03-31: use slope fallback
]

--- FUNCTION SIGNATURES ---

from typing import Literal
RegimeType = Literal["Bull", "Bear", "Sideways", "Unknown"]

def get_regime(
    trade_date: date,
    benchmark_df: pd.DataFrame | None = None,
) -> RegimeType:
    """
    Returns regime label for a given date.

    Priority:
      1. Check NSE_REGIME_CALENDAR — if date falls within a defined period, return it.
      2. Slope fallback (if date is after calendar end or outside all periods):
           Requires benchmark_df with DatetimeIndex and 'sma_200' column.
           slope = benchmark_df.loc[:trade_date, "sma_200"].pct_change(20).iloc[-1]
           slope > +0.0005 → "Bull"
           slope < -0.0005 → "Bear"
           else           → "Sideways"
      3. If benchmark_df is None and date outside calendar → return "Unknown"
    """

def label_trades(
    trades: list[dict],
    benchmark_df: pd.DataFrame | None = None,
) -> list[dict]:
    """
    Adds "regime" key to each trade dict. Returns modified list.
    Uses trade["entry_date"] for regime lookup.
    """

def get_regime_stats(
    trades: list[dict],
) -> dict[str, dict]:
    """
    Groups trades by regime and computes:
      { "Bull": {"count": int, "win_rate": float, "avg_pnl_pct": float},
        "Bear": {...}, "Sideways": {...} }
    """

--- UNIT TESTS ---

Create `tests/unit/test_regime.py`:
1. Date 2020-06-15 → "Bull" (V-shape recovery period)
2. Date 2020-02-20 → "Bear" (COVID crash period)
3. Date 2022-06-01 → "Sideways" (Fed rate hike period)
4. Date 2025-06-01 (after calendar) + mock benchmark_df with positive slope → "Bull"
5. Date 2025-06-01 + no benchmark_df → "Unknown"
6. label_trades adds "regime" key to each trade dict
7. get_regime_stats: 3 Bull trades (2 wins), 1 Bear trade (0 wins):
   Bull win_rate=0.667, Bear win_rate=0.0
```


---

### PHASE 8 — STEP 2 of 5: `backtest/engine.py`

#### Context files to attach
- `backtest/regime.py`
- `features/feature_store.py` (bootstrap, update interfaces)
- `screener/pipeline.py` (run_screen)
- `rules/scorer.py` (SEPAResult)
- `utils/trading_calendar.py`
- `utils/logger.py`
- `config/settings.yaml`

#### Prompt
```
You are building the walk-forward backtesting engine for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `backtest/engine.py` — walk-forward backtester with trailing stops.

--- KEY DESIGN RULES ---
- NO lookahead bias: on any given date, only data up to that date is visible
- Feature computation uses only data available as of backtest_date
- The screener runs exactly as it would in live mode (same pipeline.run_screen)
- Trailing stop is floored at VCP base_low — NEVER goes below entry stop

--- DATACLASSES ---

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

--- MAIN FUNCTION ---

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
    """
    Walk-forward backtest over [start_date, end_date].

    For each trading day in the range:
      1. Run run_screen(universe, date, config, ...) with data up to `date`
      2. Get A+/A candidates
      3. For new entries: call enter_backtest_trade()
      4. For open positions: call update_trailing_stops() + check_exits()

    trailing_stop_pct overrides config["backtest"]["trailing_stop_pct"] if provided.
    """

def simulate_trade(
    entry_date: date,
    entry_price: float,
    stop_loss_price: float,
    ohlcv_df: pd.DataFrame,
    config: dict,
    trailing_stop_pct: float | None = None,
) -> BacktestTrade:
    """
    Simulate a single trade forward in time using the OHLCV data.

    Parameters:
      ohlcv_df: daily OHLCV starting from entry_date (no data before entry)
      stop_loss_price: VCP base_low (hard floor for trailing stop)
      trailing_stop_pct: if None, use fixed stop (no trailing)

    Trailing stop logic:
      trailing = peak_close * (1 - trailing_stop_pct)
      trailing = max(trailing, stop_loss_price)  ← NEVER below VCP floor
      trailing ONLY moves UP — never down
      Exit when close <= trailing (or close >= target, or max_hold_days exceeded)

    Fixed stop: exit when close <= stop_loss_price.

    target_price = entry_price * (1 + config["backtest"]["target_pct"])  (default 1.10)
    max_hold_days = config["backtest"]["max_hold_days"]  (default 20)

    Returns BacktestTrade with all fields populated.
    """

--- UNIT TESTS ---

Create `tests/unit/test_backtest_engine.py`:
1. simulate_trade: price rises then falls to trailing stop → exit_reason="trailing_stop"
2. simulate_trade: trailing stop NEVER drops below VCP floor
3. simulate_trade: price hits target → exit_reason="target"
4. simulate_trade: max_hold_days exceeded → exit_reason="max_hold"
5. simulate_trade with trailing_stop_pct=None → uses fixed stop only
6. simulate_trade: trailing stop moves up 3 times as price rises

  def test_trailing_stop_never_drops_below_vcp_floor():
      """Critical regression test — must always pass."""
      ohlcv = make_trending_then_falling_df(peak=120, valley=80)
      trade = simulate_trade(
          entry_date=..., entry_price=100, stop_loss_price=85,
          ohlcv_df=ohlcv, config=..., trailing_stop_pct=0.07
      )
      # When price is at 120, trailing = 120 * 0.93 = 111.6 (above floor 85)
      # When price falls to 111, trailing should trigger exit
      assert trade.trailing_stop_used >= 85.0   # floor respected
      assert trade.exit_reason == "trailing_stop"
```


---

### PHASE 8 — STEP 3 of 5: `backtest/portfolio.py` + `backtest/metrics.py`

#### Context files to attach
- `backtest/engine.py` (BacktestTrade, BacktestResult)
- `backtest/regime.py` (get_regime_stats)

#### Prompt
```
You are building the backtest portfolio and metrics modules.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `backtest/portfolio.py` and `backtest/metrics.py`.

--- FILE 1: backtest/portfolio.py ---

class BacktestPortfolio:
    def __init__(self, initial_capital: float, config: dict):
        self.capital: float = initial_capital
        self.positions: dict[str, BacktestTrade] = {}    # open positions
        self.closed_trades: list[BacktestTrade] = []
        self.equity_curve: list[dict] = []
        self.max_positions = config["backtest"].get("max_positions", 10)

    def can_enter(self) -> bool:
        return len(self.positions) < self.max_positions and self.capital > 0

    def enter(self, result: SEPAResult, entry_price: float, entry_date: date) -> bool:
        """
        Position sizing: 1R = 1% of current portfolio value.
        risk_per_trade = portfolio_value * 0.01
        risk_per_share = entry_price - result.stop_loss
        quantity = max(1, int(risk_per_trade / risk_per_share))
        Returns False if not can_enter() or insufficient capital.
        """

    def close(self, symbol: str, exit_price: float, exit_date: date, reason: str) -> BacktestTrade:
        """Closes open position. Computes pnl, r_multiple, updates capital. Returns BacktestTrade."""

    def record_equity(self, current_prices: dict, backtest_date: date) -> None:
        """Append equity snapshot to equity_curve."""

    def get_portfolio_value(self, current_prices: dict) -> float:
        """cash + sum(pos.quantity * current_prices[sym] for sym in positions)"""

--- FILE 2: backtest/metrics.py ---

def compute_metrics(
    trades: list[BacktestTrade],
    equity_curve: list[dict],
    initial_capital: float,
) -> dict:
    """
    Computes full performance metrics:
      cagr: float             # annualised return
      total_return_pct: float
      sharpe_ratio: float     # annualised (assuming 252 trading days, risk-free=0.06)
      max_drawdown_pct: float # peak-to-trough equity decline
      win_rate: float         # % of profitable trades
      avg_r_multiple: float
      profit_factor: float    # sum winners / abs sum losers
      expectancy: float       # avg_win * win_rate - avg_loss * (1 - win_rate)
      total_trades: int
      avg_hold_days: float
      best_trade_pct: float
      worst_trade_pct: float
    """

def compute_cagr(initial: float, final: float, years: float) -> float:
    """(final/initial)^(1/years) - 1"""

def compute_max_drawdown(equity_values: list[float]) -> float:
    """Returns maximum peak-to-trough drawdown as a positive percentage."""

def compute_sharpe(daily_returns: list[float], risk_free_daily: float = 0.06/252) -> float:
    """Annualised Sharpe ratio."""

--- UNIT TESTS ---

Create `tests/unit/test_backtest_metrics.py`:
1. compute_cagr: 100k → 150k over 2 years → CAGR ≈ 22.5%
2. compute_max_drawdown: [100, 120, 90, 110] → 25% drawdown
3. compute_sharpe with known returns → within 5% of manually computed value
4. compute_metrics with 10 trades (6 wins) → win_rate=0.6, profit_factor correct
5. compute_metrics with 0 trades → returns zeros, no division error
6. BacktestPortfolio.enter: position sizing produces quantity from 1% risk rule
7. BacktestPortfolio capacity: refuses entry when positions at max
```


---

### PHASE 8 — STEP 4 of 5: `backtest/report.py` + `scripts/backtest_runner.py`

#### Context files to attach
- `backtest/metrics.py`
- `backtest/regime.py`
- `backtest/engine.py` (BacktestResult)
- `utils/logger.py`

#### Prompt
```
You are building the backtest report and CLI runner.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `backtest/report.py` and `scripts/backtest_runner.py`.

--- FILE 1: backtest/report.py ---

def generate_report(
    result: BacktestResult,
    metrics: dict,
    output_dir: str,
) -> tuple[str, str]:
    """
    Generates HTML + CSV backtest report.
    Returns (html_path, csv_path).

    HTML report sections:
      1. Header: date range, universe, config snapshot
      2. Key metrics cards: CAGR, Sharpe, Max Drawdown, Win Rate, Profit Factor
      3. Equity curve chart (matplotlib, embedded as base64)
      4. Regime breakdown table:
         | Regime | Trades | Win Rate | Avg P&L% | Avg R-Multiple |
      5. VCP quality breakdown:
         | Quality | Trades | Win Rate | Avg R-Multiple |
      6. Trailing vs Fixed stop comparison (if both were tested):
         | Stop Type | CAGR | Sharpe | Max DD | Win Rate |
      7. Top 10 winning trades table
      8. Bottom 10 losing trades table
      9. Full trades table (all trades, sorted by entry_date)

    CSV: one row per trade with all BacktestTrade fields.
    """

def plot_equity_curve(equity_curve: list[dict]) -> str:
    """
    Plots equity curve with matplotlib, returns base64-encoded PNG string.
    Adds drawdown shading (red fill between curve and previous peak).
    """

--- FILE 2: scripts/backtest_runner.py ---

CLI script. Arguments:
  --start   DATE   start date (YYYY-MM-DD)
  --end     DATE   end date (YYYY-MM-DD)
  --universe STR   "nifty500" | "nse_all" | path to CSV (default: nifty500)
  --trailing-stop FLOAT  trailing stop % (default: from settings.yaml)
  --no-trailing    FLAG   disable trailing stop (use fixed stop only)
  --compare        FLAG   run BOTH trailing and fixed; include comparison in report
  --output  DIR    output directory (default: reports/)
  --config  FILE   settings.yaml override

Example:
  python scripts/backtest_runner.py \
    --start 2019-01-01 --end 2024-01-01 \
    --universe nifty500 --trailing-stop 0.07 --compare

When --compare is used:
  - Run backtest twice (trailing + fixed)
  - Merge results into single report with comparison section
  - Print summary table to console

--- UNIT TESTS ---

Create `tests/unit/test_backtest_report.py`:
1. generate_report with 10 trades → HTML and CSV files created
2. HTML contains regime breakdown table
3. plot_equity_curve returns valid base64 string
4. CSV has one row per trade + header row
5. generate_report with 0 trades → "No trades" HTML generated, no crash

Smoke test for CLI (no actual backtest):
6. backtest_runner.py --help → exits 0 without error
```


---

### PHASE 8 — STEP 5 of 5: Integration test + Parameter sweep

#### Context files to attach
- `backtest/engine.py` (simulate_trade)
- `backtest/metrics.py`
- `tests/fixtures/sample_ohlcv.parquet`
- `utils/trading_calendar.py`

#### Prompt
```
You are writing integration tests and a parameter sweep for the backtesting engine.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Write integration tests and parameter sweep logic for the backtest engine.

--- FILE 1: tests/integration/test_backtest_e2e.py ---

def test_trailing_stop_never_drops_below_vcp_floor():
    """
    Critical regression test. Must always pass.
    Loads sample_ohlcv.parquet fixture.
    Creates a trade where price rises significantly then falls.
    Verifies trailing_stop_used >= stop_loss_price at ALL times.
    """
    # Build a synthetic rising-then-falling price series
    # Entry: 100, VCP stop: 88, trailing: 7%
    # Price rises to 130 → trailing becomes 130*0.93=120.9
    # Price drops to 119 → trailing stop triggers
    # Verify: trailing_stop_used was never below 88 at any point

def test_no_lookahead_bias():
    """
    Run backtest on date D. Verify that feature values used in rules
    do not include data from after date D.
    Load fixture with known future data, verify backtest ignores it.
    """

def test_gate_stats_reporting():
    """
    Run backtest on 100 symbols for 10 days.
    Verify run_backtest returns stats:
      pct_passing_stage2, pct_passing_tt, pct_both
    These should be between 0 and 1.
    """

--- FILE 2: Parameter sweep in scripts/backtest_runner.py --compare mode ---

def run_parameter_sweep(
    base_config: dict,
    start: date,
    end: date,
    universe: list[str],
    trailing_pcts: list[float] = [0.05, 0.07, 0.10, 0.15],
) -> pd.DataFrame:
    """
    Runs backtest for each trailing_stop_pct value.
    Returns DataFrame with columns:
      trailing_stop_pct, cagr, sharpe, max_drawdown, win_rate, total_trades
    Prints to console as a formatted table.
    """

--- UNIT TESTS ---

1. run_parameter_sweep with 2 trailing values → returns DataFrame with 2 rows
2. DataFrame has all required metric columns
3. test_trailing_stop_never_drops_below_vcp_floor passes (critical)
4. test_no_lookahead_bias: backtest on 2020-01-01 does not use 2020-03-01 data
```




---
---

## PHASE 9 — Hardening & Production (Weeks 23–26)
**Goal:** Production-ready pipeline running unattended on ShreeVault (Ubuntu server).

### Dependency order within Phase 9
```
Step 1: Full test coverage + CI — unit + integration + smoke tests
Step 2: Data lineage (run_history wiring) + Prometheus metrics
Step 3: systemd service files + Makefile completion
Step 4: scripts/rebuild_features.py + Runbook
```

---

### PHASE 9 — STEP 1 of 4: Full Test Coverage

#### Context files to attach
- All `tests/unit/` test files built in previous phases
- `tests/integration/test_pipeline_e2e.py` (if it exists, else create)
- `Makefile`

#### Prompt
```
You are hardening the test suite for a Minervini SEPA stock system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Write missing integration tests + smoke tests to achieve full coverage.

--- FILE 1: tests/integration/test_pipeline_e2e.py ---

def test_full_daily_run_e2e(tmp_path):
    """
    End-to-end test of the daily pipeline with fixture data.
    Uses mock yfinance source returning tests/fixtures/sample_ohlcv.parquet.
    Verifies the full chain:
      fetch → validate → feature update → run_screen → persist_results
      → generate_csv_report → generate_html_report
    No real HTTP calls. No real Telegram. No real LLM.
    Assert:
      - CSV report file created and non-empty
      - HTML report file created and non-empty
      - SQLite results table has rows
      - No exceptions raised
    """

def test_screener_batch_e2e(tmp_path):
    """
    Run run_screen() with 5 mock symbols (2 Stage 2, 3 non-Stage-2).
    Verify:
      - Results list length == 5 (one per symbol, even FAILs)
      - Non-Stage-2 results have score==0 and quality=="FAIL"
      - Results sorted by score DESC
      - Stage 2 results appear before Stage 4 results
    """

def test_watchlist_flow_e2e(tmp_path):
    """
    Tests the watchlist-scoped run:
      1. Add 3 symbols to SQLite watchlist
      2. Run with scope="watchlist"
      3. Verify only those 3 symbols are screened (universe symbols skipped)
      4. Verify watchlist symbols appear first in CSV report
    """

--- FILE 2: tests/smoke/test_smoke.py ---

def test_imports_all_modules():
    """All main modules import without error."""
    import ingestion.base
    import ingestion.yfinance_source
    import features.feature_store
    import rules.scorer
    import screener.pipeline
    import paper_trading.simulator
    import api.main  # FastAPI app object loadable
    # etc.

def test_config_loads():
    """settings.yaml loads without validation errors."""
    from config.loader import load_config
    config = load_config("config/settings.yaml")
    assert config["universe"]["source"] in ("yfinance", "angel_one", "upstox")

def test_sqlite_store_creates_tables(tmp_path):
    """SQLiteStore creates all required tables on init."""
    from storage.sqlite_store import SQLiteStore
    db = SQLiteStore(str(tmp_path / "test.db"))
    tables = db.list_tables()
    assert "results" in tables
    assert "run_history" in tables
    assert "watchlist" in tables
    assert "alert_history" in tables

--- COVERAGE REQUIREMENTS ---

Run: pytest tests/ --cov=. --cov-report=term-missing
Target: >= 80% line coverage across all modules
Critical paths (must be 100%): rules/, features/, screener/pre_filter.py

Add to Makefile:
  test-coverage:
      pytest tests/ --cov=. --cov-report=html --cov-report=term-missing
      @echo "Coverage report: htmlcov/index.html"

  test-smoke:
      pytest tests/smoke/ -v

  test-integration:
      pytest tests/integration/ -v

--- CI CONFIGURATION ---

Create `.github/workflows/test.yml`:
  name: Test Suite
  on: [push, pull_request]
  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with: { python-version: "3.11" }
        - run: pip install -e ".[dev]"
        - run: make test
        - run: make lint

Target: `make test` completes in < 3 minutes.
Mock all HTTP calls, yfinance calls, Telegram, and LLM API calls in tests.
```


---

### PHASE 9 — STEP 2 of 4: Data Lineage + Prometheus Metrics

#### Context files to attach
- `storage/sqlite_store.py`
- `pipeline/runner.py` (run_daily return dict)
- `utils/logger.py`

#### Prompt
```
You are adding data lineage tracking and metrics to the pipeline.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Wire run_history table and add optional Prometheus metrics endpoint.

--- FILE 1: Wire run_history in pipeline/runner.py ---

At the end of run_daily(), before returning the summary dict, write to run_history:

  import subprocess, hashlib, json

  def _get_git_sha() -> str:
      try:
          return subprocess.check_output(
              ["git", "rev-parse", "--short", "HEAD"], text=True
          ).strip()
      except Exception:
          return "unknown"

  def _config_hash(config: dict) -> str:
      return hashlib.md5(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]

  db.write_run_history({
      "run_date": str(run_date),
      "run_mode": "daily",
      "git_sha": _get_git_sha(),
      "config_hash": _config_hash(config),
      "universe_size": len(universe),
      "passed_stage2": sum(1 for r in results if r.stage == 2),
      "passed_tt": sum(1 for r in results if r.trend_template_pass),
      "vcp_qualified": sum(1 for r in results if r.vcp_qualified),
      "a_plus_count": sum(1 for r in results if r.setup_quality == "A+"),
      "a_count": sum(1 for r in results if r.setup_quality == "A"),
      "duration_sec": elapsed,
      "status": "success",
      "error_msg": None,
  })

On exception in run_daily: write status="failed", error_msg=str(e) before re-raising.

--- FILE 2: utils/metrics.py (optional Prometheus endpoint) ---

Only create this file if prometheus_client is available in requirements.txt.
Skip silently if not installed.

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

SEPA_RUNS_TOTAL = Counter("sepa_runs_total", "Total pipeline runs", ["status"])
SEPA_CANDIDATES_GAUGE = Gauge("sepa_candidates", "Candidates by quality", ["quality"])
SEPA_RUN_DURATION = Histogram("sepa_run_duration_seconds", "Run duration")

def record_run_metrics(summary: dict) -> None:
    """Update Prometheus metrics from run_daily() summary dict."""

def metrics_endpoint() -> tuple[str, str]:
    """Returns (metrics_text, content_type) for FastAPI health router."""

--- UNIT TESTS ---

Create `tests/unit/test_lineage.py`:
1. run_daily completion → run_history table has 1 row with correct run_date
2. run_daily failure → run_history has status="failed" and error_msg set
3. _config_hash returns same hash for identical configs
4. _config_hash returns different hash when config changes
5. run_history rows are never deleted (append-only log)
```


---

### PHASE 9 — STEP 3 of 4: systemd Services + Makefile Completion

#### Context files to attach
- `Makefile` (existing)
- `PROJECT_DESIGN.md` (section 18.2 — systemd config)

#### Prompt
```
You are setting up production deployment for the Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Create systemd service files and complete the Makefile.

--- FILE 1: deploy/minervini-daily.service ---
Create systemd oneshot service (see PROJECT_DESIGN.md section 18.2 for template).
WorkingDirectory: /home/ubuntu/projects/sepa_ai
User: ubuntu
ExecStart: /home/ubuntu/projects/sepa_ai/.venv/bin/python scripts/run_daily.py --date today

--- FILE 2: deploy/minervini-daily.timer ---
Run Mon–Fri at 15:35 IST (10:05 UTC).
Persistent=true (catches up if server was off).

--- FILE 3: deploy/minervini-api.service ---
FastAPI uvicorn service. Always running. Restart=always. Port 8000. 2 workers.

--- FILE 4: deploy/minervini-dashboard.service ---
Streamlit service. Always running. Restart=always. Port 8501. Depends on api.service.

--- FILE 5: deploy/install.sh ---
Bash script to:
  1. Copy service files to /etc/systemd/system/
  2. Run: systemctl daemon-reload
  3. Enable and start all services
  4. Print status of each service
  Requires sudo. Checks Python venv exists before proceeding.

--- FILE 6: Update Makefile ---

Complete ALL Makefile targets from PROJECT_DESIGN.md section 18.1:

install:       pip install -e ".[dev]"
test:          pytest tests/ -v --cov=. --cov-report=term-missing
test-smoke:    pytest tests/smoke/ -v
lint:          ruff check . && ruff format --check .
format:        ruff format .
daily:         python scripts/run_daily.py --date today
backtest:      python scripts/backtest_runner.py --start $(START) --end $(END)
rebuild:       python scripts/rebuild_features.py --universe nifty500
paper-reset:   python -c "from paper_trading.simulator import reset_portfolio; reset_portfolio(confirm=True)"
api:           uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
dashboard:     streamlit run dashboard/app.py --server.port 8501
deploy:        bash deploy/install.sh
status:        systemctl status minervini-daily.timer minervini-api.service minervini-dashboard.service
logs:          journalctl -u minervini-daily.service -n 50 --no-pager
logs-api:      journalctl -u minervini-api.service -n 50 --no-pager

--- UNIT TESTS (manual verification steps) ---

Document in deploy/README.md:
1. How to verify timer is active: systemctl list-timers | grep minervini
2. How to trigger a manual run: make daily
3. How to check last run result: sqlite3 data/sepa_ai.db "SELECT * FROM run_history ORDER BY id DESC LIMIT 1;"
4. How to reset paper trading: make paper-reset
5. How to rebuild features from scratch: make rebuild
```


---

### PHASE 9 — STEP 4 of 4: `scripts/rebuild_features.py` + Runbook

#### Context files to attach
- `features/feature_store.py` (bootstrap, needs_bootstrap)
- `ingestion/universe_loader.py`
- `utils/logger.py`

#### Prompt
```
You are creating the rebuild script and operational runbook.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `scripts/rebuild_features.py` and create `docs/RUNBOOK.md`.

--- FILE 1: scripts/rebuild_features.py ---

CLI Arguments:
  --universe STR     "nifty500" | "nse_all" | path to CSV (default: nifty500)
  --symbol STR       rebuild single symbol only
  --force            rebuild even if feature file exists and seems valid
  --dry-run          list symbols that would be rebuilt, don't rebuild
  --workers INT      parallel workers (default: 4)
  --config FILE      settings.yaml path

Logic:
  For each symbol in universe:
    if needs_bootstrap(symbol) OR force:
        bootstrap(symbol, config)
        logger.info(f"Rebuilt {symbol}")
    else:
        logger.debug(f"Skipping {symbol} — feature file OK")

  Print summary at end: "Rebuilt N/total symbols in X seconds"

Error handling:
  Individual symbol failures: log error, continue, count failures.
  Print final count: "Failures: {n}" — exit code 1 if any failures.

--- FILE 2: docs/RUNBOOK.md ---

# Minervini SEPA — Operations Runbook

Sections:

## 1. Daily Operations
- How to check today's run status
- How to re-run manually if the timer missed
- How to check the Telegram bot is working

## 2. Adding a New Data Source
Step-by-step:
  1. Create ingestion/{source_name}_source.py implementing DataSource interface
  2. Add to SOURCES dict in ingestion/source_factory.py
  3. Add API key to .env.example and .env
  4. Set universe.source in settings.yaml
  5. Run: python -c "from ingestion.source_factory import get_source; ..."

## 3. Adding a New Rule Condition
Step-by-step:
  1. Add feature computation to features/ (if new indicator needed)
  2. Add condition to rules/trend_template.py or rules/vcp_rules.py
  3. Add unit test with pass/fail case
  4. Update SCORE_WEIGHTS in rules/scorer.py if condition affects score
  5. Run: make test

## 4. Recovering from Data Corruption
  - Feature file corrupt: make rebuild --symbol SYMBOLNAME
  - SQLite corrupt: restore from data/backups/ or rerun pipeline
  - Paper trading state corrupt: make paper-reset (resets from scratch)

## 5. Adding Symbols to Watchlist
  Via CLI: python scripts/run_daily.py --symbols "RELIANCE,TCS"
  Via file: python scripts/run_daily.py --watchlist mylist.csv
  Persistent: Use API POST /api/v1/watchlist/bulk (Phase 10)

## 6. Server Health Checks
  systemctl status minervini-daily.timer
  curl http://localhost:8000/api/v1/health
  sqlite3 data/sepa_ai.db "SELECT * FROM run_history ORDER BY id DESC LIMIT 5;"

## 7. Log Locations
  Pipeline:   journalctl -u minervini-daily.service
  API:        journalctl -u minervini-api.service
  Dashboard:  journalctl -u minervini-dashboard.service
  File logs:  logs/sepa_ai.log (last 30 days, rotating)
```




---
---

## PHASE 10 — API Layer (FastAPI) (Weeks 27–29)
**Goal:** Expose screener results, watchlist, and paper trading over HTTP.

### Dependency order within Phase 10
```
Step 1: api/schemas/ — Pydantic response models
Step 2: api/auth.py + api/rate_limit.py + api/deps.py
Step 3: api/routers/health.py + api/routers/stocks.py
Step 4: api/routers/watchlist.py + api/routers/portfolio.py
Step 5: api/main.py + systemd service + endpoint tests
```

---

### PHASE 10 — STEP 1 of 5: `api/schemas/`

#### Context files to attach
- `rules/scorer.py` (SEPAResult dataclass — all fields)
- `paper_trading/portfolio.py` (Portfolio summary dict)

#### Prompt
```
You are building the FastAPI schema layer for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement all Pydantic schemas in `api/schemas/`.

--- FILE 1: api/schemas/common.py ---

from pydantic import BaseModel
from typing import TypeVar, Generic

T = TypeVar("T")

class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: T
    meta: dict | None = None
    error: str | None = None

class PaginationMeta(BaseModel):
    total: int
    page: int
    per_page: int
    date: str | None = None

class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str | None = None

--- FILE 2: api/schemas/stock.py ---

class TrendTemplateSchema(BaseModel):
    passes: bool
    conditions_met: int
    condition_1: bool
    condition_2: bool
    condition_3: bool
    condition_4: bool
    condition_5: bool
    condition_6: bool
    condition_7: bool
    condition_8: bool

class VCPSchema(BaseModel):
    qualified: bool
    contraction_count: int | None = None
    max_depth_pct: float | None = None
    final_depth_pct: float | None = None
    vol_contraction_ratio: float | None = None
    base_length_weeks: int | None = None
    tightness_score: float | None = None

class StockResultSchema(BaseModel):
    symbol: str
    run_date: str
    score: int
    setup_quality: str             # "A+" | "A" | "B" | "C" | "FAIL"
    stage: int
    stage_label: str
    stage_confidence: int
    trend_template_pass: bool
    conditions_met: int
    vcp_qualified: bool
    breakout_triggered: bool
    entry_price: float | None = None
    stop_loss: float | None = None
    risk_pct: float | None = None
    target_price: float | None = None
    reward_risk_ratio: float | None = None
    rs_rating: int
    news_score: float | None = None
    fundamental_pass: bool = False
    is_watchlist: bool = False
    trend_template_details: TrendTemplateSchema | None = None
    vcp_details: VCPSchema | None = None
    llm_brief: str | None = None   # Phase 6 optional field

class StockHistorySchema(BaseModel):
    symbol: str
    history: list[dict]   # list of {run_date, score, quality, stage}

--- FILE 3: api/schemas/portfolio.py ---

class PositionSchema(BaseModel):
    symbol: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_loss: float
    trailing_stop: float
    target_price: float | None = None
    days_held: int
    unrealised_pnl: float
    unrealised_pnl_pct: float
    setup_quality: str

class TradeSchema(BaseModel):
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    r_multiple: float
    exit_reason: str
    setup_quality: str

class PortfolioSummarySchema(BaseModel):
    cash: float
    open_value: float
    total_value: float
    initial_capital: float
    total_return_pct: float
    realised_pnl: float
    unrealised_pnl: float
    win_rate: float
    total_trades: int
    open_count: int
    closed_count: int
    profit_factor: float
    avg_r_multiple: float
    positions: list[PositionSchema]

--- UNIT TESTS ---

Create `tests/unit/test_schemas.py`:
1. StockResultSchema validates SEPAResult dict (via dataclasses.asdict)
2. APIResponse[list[StockResultSchema]] serialises correctly
3. PortfolioSummarySchema validates portfolio.get_summary() output
4. All Optional fields default to None without error
5. APIResponse with error → success=False, error set, data can be None
```


---

### PHASE 10 — STEP 2 of 5: `api/auth.py` + `api/rate_limit.py` + `api/deps.py`

#### Context files to attach
- `storage/sqlite_store.py`
- `config/settings.yaml`

#### Prompt
```
You are building the auth, rate limiting, and dependency injection for FastAPI.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `api/auth.py`, `api/rate_limit.py`, and `api/deps.py`.

--- FILE 1: api/auth.py ---

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

def require_read_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """
    Validates X-API-Key header for read endpoints.
    Accepts both read_key and admin_key (admin can read too).
    Keys loaded from os.environ: API_READ_KEY, API_ADMIN_KEY
    Raises HTTP 401 if key is missing or invalid.
    """

def require_admin_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """
    Validates X-API-Key header for admin-only endpoints (POST /api/v1/run, etc.).
    Only accepts API_ADMIN_KEY.
    Raises HTTP 403 if read key used for admin endpoint.
    Raises HTTP 401 if no key provided.
    """

def get_auth_status() -> dict:
    """
    Returns {"auth_enabled": bool} — False if both env vars are empty.
    When auth_disabled, all requests pass. Useful for development.
    """

--- FILE 2: api/rate_limit.py ---

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Decorators to use on route functions:
# read_limit = "100/minute"   → for GET endpoints
# admin_limit = "10/minute"   → for POST /run and other admin endpoints

def get_limiter() -> Limiter:
    return limiter

--- FILE 3: api/deps.py ---

from storage.sqlite_store import SQLiteStore
from functools import lru_cache

@lru_cache(maxsize=1)
def get_db() -> SQLiteStore:
    """Returns singleton SQLiteStore instance. Thread-safe for reads."""

def get_config() -> dict:
    """Returns loaded app config (cached). Reads config/settings.yaml."""

def get_run_date(date_str: str | None = None) -> date:
    """
    Returns date object from string param, or today's date if None.
    Raises HTTP 422 if date_str is invalid format.
    """

--- UNIT TESTS ---

Create `tests/unit/test_api_auth.py`:
1. require_read_key with valid read_key → passes
2. require_read_key with valid admin_key → passes (admin can read)
3. require_read_key with invalid key → HTTP 401
4. require_admin_key with read_key → HTTP 403
5. require_admin_key with admin_key → passes
6. Auth disabled (empty env vars) → all requests pass
```


---

### PHASE 10 — STEP 3 of 5: `api/routers/health.py` + `api/routers/stocks.py`

#### Context files to attach
- `api/schemas/stock.py` (StockResultSchema, StockHistorySchema)
- `api/schemas/common.py` (APIResponse)
- `api/deps.py`
- `api/auth.py`
- `screener/results.py` (load_results, get_top_candidates)
- `storage/sqlite_store.py`

#### Prompt
```
You are building the stocks and health API routers for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `api/routers/health.py` and `api/routers/stocks.py`.

--- FILE 1: api/routers/health.py ---

router = APIRouter(prefix="/api/v1")

@router.get("/health")
async def health_check(db: SQLiteStore = Depends(get_db)):
    """
    Returns:
      { "status": "ok", "last_run": ISO timestamp | None,
        "last_run_status": "success" | "failed" | None,
        "version": "1.0.0" }
    Reads last_run from run_history table.
    """

@router.get("/meta")
async def get_meta(db: SQLiteStore = Depends(get_db)):
    """
    Returns:
      { "universe_size": int, "watchlist_size": int,
        "last_screen_date": str,
        "a_plus_count": int, "a_count": int,
        "pipeline_uptime_days": float }
    """

--- FILE 2: api/routers/stocks.py ---

router = APIRouter(prefix="/api/v1/stocks", dependencies=[Depends(require_read_key)])

@router.get("/top", response_model=APIResponse[list[StockResultSchema]])
async def get_top_stocks(
    quality: str | None = None,    # "A+" | "A" | "B" | "C"
    limit: int = 20,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
    run_date: date = Depends(get_run_date),
):
    """Returns today's top-ranked SEPA candidates sorted by score DESC."""

@router.get("/trend", response_model=APIResponse[list[StockResultSchema]])
async def get_trend_stocks(
    min_rs: int = 0,
    stage: int | None = None,
    limit: int = 50,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
):
    """All stocks that passed Trend Template on given date."""

@router.get("/vcp", response_model=APIResponse[list[StockResultSchema]])
async def get_vcp_stocks(
    min_quality: str = "B",
    limit: int = 30,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
):
    """Stocks with a qualified VCP pattern."""

@router.get("/{symbol}", response_model=APIResponse[StockResultSchema])
async def get_stock(
    symbol: str,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
):
    """Full SEPAResult for a single symbol. Returns 404 if not found."""

@router.get("/{symbol}/history", response_model=APIResponse[StockHistorySchema])
async def get_stock_history(
    symbol: str,
    days: int = 30,
    db: SQLiteStore = Depends(get_db),
):
    """Historical SEPA scores for a symbol over last N trading days."""

--- UNIT TESTS ---

Create `tests/unit/test_api_stocks.py`:
Use FastAPI TestClient with mocked SQLiteStore.

1. GET /api/v1/health → 200 with status="ok"
2. GET /api/v1/stocks/top → 200 with list of StockResultSchema
3. GET /api/v1/stocks/top?quality=A%2B → filtered to A+ only
4. GET /api/v1/stocks/top?limit=5 → max 5 results
5. GET /api/v1/stocks/RELIANCE → 200 with single stock
6. GET /api/v1/stocks/FAKE999 → 404 response
7. GET /api/v1/stocks/RELIANCE/history?days=30 → list of 30 dicts
8. Missing auth key → 401 (when auth enabled)
```


---

### PHASE 10 — STEP 4 of 5: `api/routers/watchlist.py` + `api/routers/portfolio.py`

#### Context files to attach
- `api/schemas/stock.py`, `api/schemas/portfolio.py`, `api/schemas/common.py`
- `api/deps.py`, `api/auth.py`
- `ingestion/universe_loader.py` (load_watchlist_file, validate_symbol)
- `storage/sqlite_store.py`
- `paper_trading/simulator.py`
- `paper_trading/portfolio.py`

#### Prompt
```
You are building the watchlist and portfolio API routers.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `api/routers/watchlist.py` and `api/routers/portfolio.py`.

--- FILE 1: api/routers/watchlist.py ---

router = APIRouter(prefix="/api/v1/watchlist")

@router.get("", dependencies=[Depends(require_read_key)])
async def get_watchlist(sort: str = "score", limit: int = 100, db=Depends(get_db)):
    """Returns all watchlist symbols with latest SEPA scores."""

@router.post("/{symbol}", dependencies=[Depends(require_admin_key)])
async def add_to_watchlist(symbol: str, note: str | None = None, db=Depends(get_db)):
    """Add single symbol. Returns 400 if invalid symbol. Returns 200 if already exists."""

@router.delete("/{symbol}", dependencies=[Depends(require_admin_key)])
async def remove_from_watchlist(symbol: str, db=Depends(get_db)):
    """Remove symbol. Returns 404 if not in watchlist."""

@router.post("/bulk", dependencies=[Depends(require_admin_key)])
async def add_bulk(body: dict, db=Depends(get_db)):
    """
    Body: { "symbols": ["RELIANCE", "TCS", "DIXON"] }
    Returns: { "added": int, "already_exists": int, "invalid": list[str], "watchlist": list }
    """

@router.post("/upload", dependencies=[Depends(require_admin_key)])
async def upload_watchlist(file: UploadFile, db=Depends(get_db)):
    """
    Upload .csv / .json / .xlsx / .txt file.
    Parses using load_watchlist_file() after saving to tmp file.
    Returns: { "added": int, "skipped": int, "invalid": list[str], "watchlist": list }
    Max file size: 1MB. Returns 400 if file too large or unparseable.
    """

@router.delete("", dependencies=[Depends(require_admin_key)])
async def clear_watchlist(db=Depends(get_db)):
    """Clear entire watchlist. Returns count of removed symbols."""

@router.post("/run", dependencies=[Depends(require_admin_key)])
async def trigger_run(body: dict = {}, config=Depends(get_config)):
    """
    Body: { "scope": "all" | "watchlist" | "universe" }
          { "symbols": ["RELIANCE", "TCS"] }
    Triggers pipeline/runner.run_daily() in background thread.
    Returns immediately with { "status": "started", "run_id": uuid }
    """

--- FILE 2: api/routers/portfolio.py ---

router = APIRouter(prefix="/api/v1/portfolio", dependencies=[Depends(require_read_key)])

@router.get("")
async def get_portfolio():
    """
    Returns current paper trading portfolio summary (PortfolioSummarySchema).
    Loads portfolio from data/paper_trading/portfolio.json.
    Returns 404 if paper trading not yet started.
    """

@router.get("/trades")
async def get_trades(status: str = "all"):
    """
    Returns paper trade history.
    status: "open" | "closed" | "all"
    """

--- UNIT TESTS ---

Create `tests/unit/test_api_watchlist.py`:
1. POST /watchlist/RELIANCE → 200, symbol in db
2. POST /watchlist/RELIANCE again → 200 (no duplicate), already_exists noted
3. POST /watchlist/FAKE@!#$ → 400 (invalid symbol)
4. DELETE /watchlist/RELIANCE → 200, symbol removed
5. DELETE /watchlist/MISSING → 404
6. POST /watchlist/bulk with 3 symbols → added=3
7. POST /watchlist/upload with CSV file → added count matches file symbols
8. DELETE /watchlist (clear all) → all symbols removed
9. GET /watchlist → returns list sorted by score

Create `tests/unit/test_api_portfolio.py`:
10. GET /portfolio with valid portfolio.json → PortfolioSummarySchema returned
11. GET /portfolio with missing file → 404
12. GET /portfolio/trades?status=closed → only closed trades returned
```


---

### PHASE 10 — STEP 5 of 5: `api/main.py` + Full API test suite

#### Context files to attach
- All routers + schemas built in steps 1-4
- `api/rate_limit.py`, `api/auth.py`, `api/deps.py`

#### Prompt
```
You are completing the FastAPI application.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `api/main.py` and write the full API test suite.

--- FILE 1: api/main.py ---

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from api.routers import health, stocks, watchlist, portfolio
from api.rate_limit import limiter

app = FastAPI(
    title="SEPA AI Stock Screener API",
    version="1.0.0",
    description="Minervini SEPA screening results API",
)

# CORS: allow all origins in dev, restrict in production via config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # TODO: restrict via config in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include routers
app.include_router(health.router)
app.include_router(stocks.router)
app.include_router(watchlist.router)
app.include_router(portfolio.router)

@app.on_event("startup")
async def startup():
    """Verify SQLite DB is accessible and tables exist. Log app version."""

@app.on_event("shutdown")
async def shutdown():
    """Close any open DB connections."""

# 404 handler — returns APIResponse format, not FastAPI default
# 422 handler — returns APIResponse format for validation errors

--- UNIT TESTS ---

Create `tests/unit/test_api_main.py`:
1. GET /api/v1/health → 200 (app starts without error)
2. GET /nonexistent → 404 in APIResponse format
3. POST /api/v1/stocks/top (wrong method) → 405
4. Rate limit: 101 requests in 1 min → 429 (mock the limiter)
5. CORS headers present on all responses
6. All routers registered: check app.routes for all expected paths

Write full test in `tests/integration/test_api_e2e.py`:
  def test_full_api_flow(tmp_path):
      # Load test DB with fixture data
      # Test: health → stocks/top → watchlist CRUD → portfolio
      # Verify each step uses APIResponse envelope
      # Verify auth gates are working
```




---
---

## PHASE 11 — Streamlit Dashboard MVP (Weeks 30–31)
**Goal:** Visual dashboard for daily monitoring, accessible without SSH.

### Dependency order within Phase 11
```
Step 1: dashboard/components/charts.py + tables.py + metrics.py
Step 2: dashboard/pages/01_Watchlist.py
Step 3: dashboard/pages/02_Screener.py + 03_Stock.py
Step 4: dashboard/pages/04_Portfolio.py + 05_Backtest.py
Step 5: dashboard/app.py (entry point + systemd service)
```

---

### PHASE 11 — STEP 1 of 5: `dashboard/components/`

#### Context files to attach
- `rules/scorer.py` (SEPAResult)
- `features/vcp.py` (VCPMetrics)
- `paper_trading/portfolio.py` (Portfolio, ClosedTrade)
- `utils/logger.py`

#### Prompt
```
You are building the Streamlit dashboard components for a Minervini SEPA system.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement THREE component files in `dashboard/components/`.

--- FILE 1: dashboard/components/charts.py ---

import mplfinance as mpf
import matplotlib.pyplot as plt
import streamlit as st

def render_ohlcv_chart(
    symbol: str,
    ohlcv_df: pd.DataFrame,
    result: dict,
    vcp_metrics: dict | None = None,
    n_days: int = 90,
) -> None:
    """
    Renders a candlestick chart inline in Streamlit.

    Chart elements:
      - Candlestick OHLCV (last n_days)
      - MA ribbons: SMA 50 (blue), SMA 150 (orange), SMA 200 (red)
      - Volume panel (bottom 20%)
      - Stage label annotation: top-right corner, colour-coded
      - Setup quality badge: "★ A+" in top-left
      - Entry price line (green dashed) if result["entry_price"]
      - Stop loss line (red dashed) if result["stop_loss"]
      - VCP contraction zones (yellow shaded) if vcp_metrics provided

    Use mplfinance with returnfig=True then st.pyplot(fig).
    Always call plt.close(fig) after st.pyplot().
    ohlcv_df must have DatetimeIndex + [open, high, low, close, volume] columns.
    MA columns: sma_50, sma_150, sma_200 (skip if missing).
    """

def render_equity_curve(equity_curve: list[dict]) -> None:
    """
    Renders paper trading equity curve using st.line_chart or matplotlib.
    equity_curve: list of {"date": str, "total_value": float}
    Shows initial_capital as a baseline horizontal line.
    """

--- FILE 2: dashboard/components/tables.py ---

import streamlit as st
import pandas as pd

def render_results_table(
    results: list[dict],
    watchlist_symbols: list[str] = None,
    show_columns: list[str] = None,
) -> str | None:
    """
    Renders a styled screener results table in Streamlit.

    Default columns: symbol, score, setup_quality, stage, conditions_met,
                     vcp_qualified, breakout_triggered, entry_price, stop_loss,
                     risk_pct, rs_rating

    Styling:
      - Quality badge colour: A+=gold bg, A=green bg, B=blue bg, C=grey, FAIL=red
      - Watchlist symbols: bold + ★ prefix
      - Breakout triggered: 🔴 label
      - Sortable (use st.dataframe with column_config)

    Returns the symbol the user clicked on (if selection enabled), or None.
    """

def render_trend_template_checklist(tt_details: dict) -> None:
    """
    Renders the 8 Trend Template conditions as a pass/fail checklist.
    Uses st.success / st.error / st.info per condition.
    Shows numeric values (e.g., "Close 145.2 > SMA200 132.1 ✅")
    """

def render_fundamental_scorecard(fund_details: dict | None) -> None:
    """
    Renders 7 fundamental conditions as a compact scorecard.
    If fund_details is None: shows "Fundamentals not available" info box.
    Columns: 2×4 grid layout using st.columns.
    """

--- FILE 3: dashboard/components/metrics.py ---

import streamlit as st

def render_score_card(score: int, quality: str, stage_label: str) -> None:
    """
    Renders a 3-column metric card at the top of the Stock deep-dive page:
      col1: Score gauge (0–100, colour-coded: <40=red, 40-70=yellow, 70+=green)
      col2: Quality badge (A+/A/B/C/FAIL with colour)
      col3: Stage label
    Use st.metric() + st.markdown for styling.
    """

def render_portfolio_summary_cards(summary: dict) -> None:
    """
    Renders key portfolio metrics as st.metric cards in a 4-column row:
      Total Return %, Realised P&L (₹), Win Rate %, Open Positions count
    """

def render_run_status_bar(last_run: dict | None) -> None:
    """
    Small status bar at top of Watchlist page:
      "Last run: 2024-01-15 15:35 IST | A+: 3 | A: 12 | Duration: 28s"
    Uses st.info() or st.caption().
    Shows "No run yet" if last_run is None.
    """

--- UNIT TESTS ---

Create `tests/unit/test_dashboard_components.py`:
1. render_results_table with 3 results — no Streamlit crash (mock st module)
2. render_trend_template_checklist with all True → no exception
3. render_fundamental_scorecard with None → shows N/A (no exception)
4. render_score_card with score=91 → no exception
5. render_equity_curve with empty list → shows empty chart (no exception)

Note: mock streamlit calls using unittest.mock.patch("streamlit.metric") etc.
Do NOT render actual Streamlit UI in tests.
```


---

### PHASE 11 — STEP 2 of 5: `dashboard/pages/01_Watchlist.py`

#### Context files to attach
- `dashboard/components/tables.py`
- `dashboard/components/metrics.py`
- `storage/sqlite_store.py`
- `screener/results.py`
- `ingestion/universe_loader.py` (load_watchlist_file, validate_symbol)
- `PROJECT_DESIGN.md` (section 13.2 — Watchlist page layout)

#### Prompt
```
You are building the Watchlist page for the Streamlit dashboard.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `dashboard/pages/01_Watchlist.py`.

--- PAGE LAYOUT ---

st.set_page_config(page_title="SEPA Watchlist", layout="wide")

Section 1: Status bar
  render_run_status_bar(last_run_from_db)

Section 2: Custom Watchlist Manager (collapsible st.expander)
  ── Manual entry ──────────────────────
  text_input: "Enter symbols (comma-separated): RELIANCE, TCS, DIXON"
  button: [➕ Add Symbols]
    → validates each symbol, adds to SQLite watchlist table
    → shows "Added: 2 | Already exists: 1 | Invalid: ['XYZ!']"

  ── File upload ───────────────────────
  file_uploader: accepts .csv, .json, .xlsx, .txt
    → on upload: call load_watchlist_file(tmp_path), add valid symbols to SQLite
    → shows upload result summary

  ── Current watchlist table ───────────
  Shows: symbol, last_score, last_quality, note, added_at, added_via
  [🗑 Remove] button per row (using st.data_editor with delete column)
  [🧹 Clear All] button (with st.warning confirmation dialog)

  [🚀 Run Watchlist Now] button
    → calls POST http://localhost:8000/api/v1/run {"scope":"watchlist"}
    → shows spinner, then shows result summary on completion
    → falls back to direct pipeline call if API not reachable

Section 3: Today's Results
  Tab 1: "★ Watchlist Results"
    → render_results_table(watchlist_results, highlight_watchlist=True)
  Tab 2: "Universe A+/A"
    → render_results_table(universe_top_results)

  Clicking a symbol row → navigate to Stock page (st.session_state["selected_symbol"])

--- DATA LOADING ---

@st.cache_data(ttl=60)
def load_today_results() -> tuple[list[dict], list[dict]]:
    """Returns (watchlist_results, universe_results) from SQLite."""

--- SIDEBAR ---

st.sidebar.selectbox("Date", recent_run_dates)
st.sidebar.number_input("Min Score", 0, 100, 40)
st.sidebar.selectbox("Min Quality", ["All", "B", "A", "A+"])
[🔄 Refresh] button

--- ANTI-PATTERNS ---
- Never call the pipeline directly from a Streamlit page — use the API endpoint
- Use st.session_state for symbol selection (not URL params)
- Cache expensive DB reads with @st.cache_data(ttl=60)
```


---

### PHASE 11 — STEP 3 of 5: `dashboard/pages/02_Screener.py` + `03_Stock.py`

#### Context files to attach
- `dashboard/components/tables.py`
- `dashboard/components/charts.py`
- `dashboard/components/metrics.py`
- `screener/results.py`
- `features/feature_store.py`

#### Prompt
```
You are building the Screener and Stock deep-dive pages for the Streamlit dashboard.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `dashboard/pages/02_Screener.py` and `dashboard/pages/03_Stock.py`.

--- FILE 1: dashboard/pages/02_Screener.py ---

PAGE LAYOUT:
  Title: "📊 Full Universe Screener"

  Filters row (st.columns):
    - Quality: multiselect ["A+","A","B","C","FAIL"]
    - Stage: selectbox [All, 1, 2, 3, 4]
    - Min RS: slider 0–99
    - Sector: selectbox from symbol_info
    - Min Price: number_input
    - Date: selectbox (recent run dates)

  Summary row:
    st.metric cards: "Total screened", "Stage 2", "Passed TT", "A+/A setups"

  Results table:
    render_results_table(filtered_results, watchlist_symbols)
    Click → session_state["selected_symbol"] + switch to Stock page

  Export row:
    [📥 Download CSV] button (st.download_button)

--- FILE 2: dashboard/pages/03_Stock.py ---

PAGE LAYOUT:
  Symbol selection: st.selectbox or read from session_state["selected_symbol"]
  Date selection: selectbox from available run dates for that symbol

  Row 1: render_score_card(score, quality, stage_label)

  Row 2: Chart tab
    render_ohlcv_chart(symbol, ohlcv_df, result, vcp_metrics)
    Chart options: n_days slider (30/60/90/180), MA toggle checkboxes

  Row 3: Analysis tabs
    Tab "📋 Trend Template":
      render_trend_template_checklist(result["trend_template_details"])
    Tab "🌀 VCP":
      VCP metrics: contraction_count, depths, vol_ratio, base_weeks, tightness
      Small VCP anatomy diagram (pre-rendered image or ASCII art)
    Tab "📈 Fundamentals":
      render_fundamental_scorecard(result["fundamental_details"])
      Show: EPS acceleration chart (last 4 quarters bar chart)
    Tab "💬 LLM Brief":
      Shows result["llm_brief"] in st.info() box
      If None: "LLM brief not generated (disabled or quality below threshold)"
    Tab "📅 History":
      Line chart of score over last 30 days
      Table of daily quality tags

  Row 4: Sidebar shortcut
    [⭐ Add to Watchlist] button
    [🔴 Remove from Watchlist] button (if already in watchlist)

--- DATA LOADING ---

@st.cache_data(ttl=300)
def load_stock_data(symbol: str, run_date: str) -> tuple[dict, pd.DataFrame, dict]:
    """Returns (sepa_result, ohlcv_df, vcp_metrics)."""

@st.cache_data(ttl=300)
def load_stock_history(symbol: str, days: int = 30) -> list[dict]:
    """Returns historical scores for the symbol."""
```


---

### PHASE 11 — STEP 4 of 5: `dashboard/pages/04_Portfolio.py` + `05_Backtest.py`

#### Context files to attach
- `dashboard/components/charts.py`
- `dashboard/components/metrics.py`
- `paper_trading/simulator.py`
- `paper_trading/report.py`
- `backtest/metrics.py`
- `backtest/report.py`

#### Prompt
```
You are building the Portfolio and Backtest pages for the Streamlit dashboard.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `dashboard/pages/04_Portfolio.py` and `dashboard/pages/05_Backtest.py`.

--- FILE 1: dashboard/pages/04_Portfolio.py ---

PAGE LAYOUT:
  Title: "💼 Paper Trading Portfolio"

  Summary cards row:
    render_portfolio_summary_cards(summary)  # Total Return, P&L, Win Rate, Open Positions

  Equity curve:
    render_equity_curve(portfolio.equity_curve)

  Tabs:
    Tab "📂 Open Positions":
      Table: symbol, entry_date, entry_price, current_price, unrealised_P&L%, 
             days_held, stop_loss, trailing_stop, quality
      Colour coding: green if unrealised_pnl_pct > 0, red if < 0
      [🚪 Close Position] button per row (manual exit at current price via API)

    Tab "📜 Closed Trades":
      Table: all closed trades sorted by exit_date DESC
      Colour coding: green rows for profitable, red for losses
      R-multiple column: bold if > 2.0

    Tab "📊 Statistics":
      Quality breakdown table: win rate + avg R by quality tag
      Monthly P&L bar chart
      Hold time histogram (matplotlib, st.pyplot)

  Warning banner if no trades yet:
    st.warning("No paper trades yet. Run the daily screener to generate signals.")

--- FILE 2: dashboard/pages/05_Backtest.py ---

PAGE LAYOUT:
  Title: "🔬 Backtest Results"

  If no backtest results exist yet:
    st.info("No backtest results yet.")
    [▶ Run Backtest] form:
      date_input: start_date (default 2019-01-01)
      date_input: end_date (default today - 1 year)
      selectbox: universe (nifty500 / nse_all)
      slider: trailing_stop_pct (0.05–0.20)
      checkbox: Compare with fixed stop
      [Run] button → triggers backtest_runner.py via subprocess
      Progress bar while running

  If backtest results exist (load from reports/backtest_*.html or SQLite):
    Summary metrics cards: CAGR, Sharpe, Max Drawdown, Win Rate, Profit Factor

    Tabs:
      Tab "📈 Equity Curve":
        Load equity_curve from backtest result, render_equity_curve()

      Tab "🌍 Regime Breakdown":
        Table: Regime | Trades | Win Rate | Avg R-Multiple
        Bar chart: win rate by regime

      Tab "🏷 Quality Breakdown":
        Table: A+/A/B/C trades with win rates

      Tab "⚖️ Stop Comparison" (only if --compare was used):
        Side-by-side: Trailing vs Fixed CAGR, Sharpe, Max Drawdown

      Tab "📋 All Trades":
        Full trades table with all BacktestTrade fields, sortable

  [📥 Download CSV] button for full trades export
```


---

### PHASE 11 — STEP 5 of 5: `dashboard/app.py` + systemd service

#### Context files to attach
- All dashboard pages and components built in steps 1–4
- `deploy/minervini-dashboard.service` (from Phase 9)

#### Prompt
```
You are completing the Streamlit dashboard entry point.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Implement `dashboard/app.py` and wire up navigation.

--- dashboard/app.py ---

import streamlit as st

st.set_page_config(
    page_title="SEPA AI — Minervini Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global sidebar navigation header
st.sidebar.title("📈 SEPA AI")
st.sidebar.caption("Minervini SEPA Screener v1.0")
st.sidebar.divider()

# Show current API status in sidebar
with st.sidebar:
    try:
        resp = requests.get("http://localhost:8000/api/v1/health", timeout=2)
        if resp.ok:
            st.sidebar.success("✅ API connected")
        else:
            st.sidebar.warning("⚠️ API error")
    except Exception:
        st.sidebar.error("❌ API offline")

# Landing page content (shown when no sub-page is selected)
st.title("📈 SEPA AI — Minervini Stock Screener")
st.markdown("""
Welcome to the SEPA AI dashboard. Navigate using the sidebar:
- **Watchlist** — Daily A+/A candidates + custom watchlist manager
- **Screener** — Full universe results with filters
- **Stock** — Single stock deep-dive with chart + analysis
- **Portfolio** — Paper trading portfolio
- **Backtest** — Historical strategy performance
""")

# Quick stats on landing page
db = get_db()
meta = db.get_meta()
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Last Run", meta.get("last_screen_date", "Never"))
with col2: st.metric("A+ Setups", meta.get("a_plus_count", 0))
with col3: st.metric("A Setups", meta.get("a_count", 0))
with col4: st.metric("Universe", meta.get("universe_size", 0))

--- SYSTEMD SERVICE VERIFICATION ---

Update `deploy/minervini-dashboard.service`:
  ExecStart: /home/ubuntu/projects/sepa_ai/.venv/bin/streamlit run
             dashboard/app.py --server.port 8501 --server.headless true
             --server.address 0.0.0.0

Add health check to `deploy/install.sh`:
  echo "Waiting for dashboard to start..."
  sleep 5
  curl -s http://localhost:8501/healthz | grep -q "ok" && echo "Dashboard OK" || echo "Dashboard may not be ready yet"

--- MAKEFILE UPDATE ---

Add to Makefile:
  dashboard-dev:
      streamlit run dashboard/app.py --server.port 8501

  test-dashboard:
      pytest tests/unit/test_dashboard_components.py -v

--- FINAL INTEGRATION CHECK ---

Run this sequence to verify everything works together:
  1. make api        → FastAPI starts on port 8000
  2. make daily      → daily run completes, results in SQLite
  3. make dashboard  → Streamlit starts on port 8501
  4. Open http://localhost:8501 → Watchlist page shows today's results
  5. Upload a CSV → symbols added to watchlist
  6. Click [Run Watchlist Now] → pipeline runs, results refresh
  7. Click a symbol → Stock deep-dive shows chart + analysis
```




---
---

## PHASE 12 — Next.js Production Frontend (Weeks 32–36)
**Goal:** Shareable, mobile-friendly web app backed by the FastAPI layer.

### Dependency order within Phase 12
```
Step 1: Project scaffold + lib/types.ts + lib/api.ts
Step 2: Screener table page + shared components (StockTable, ScoreGauge)
Step 3: Stock deep-dive page (CandlestickChart, TrendTemplateCard, VCPCard)
Step 4: Watchlist page + Portfolio page
Step 5: Deploy to Vercel + final polish
```

---

### PHASE 12 — STEP 1 of 5: Project Scaffold + API Client + Types

#### Context files to attach
- `api/schemas/stock.py` (Pydantic models — source of TypeScript types)
- `api/schemas/portfolio.py`
- `api/schemas/common.py`
- `PROJECT_DESIGN.md` (section 13.3 — Next.js technology choices)

#### Prompt
```
You are scaffolding the Next.js frontend for a Minervini SEPA stock screener.
Project root: /home/ubuntu/projects/sepa_ai/

TASK: Create `frontend/` Next.js project scaffold, TypeScript types, and API client.

--- SETUP ---

Create the following directory structure (files will be filled in per step):

frontend/
├── app/
│   ├── layout.tsx           ← root layout (Tailwind, global styles)
│   ├── page.tsx             ← dashboard home
│   ├── screener/
│   │   ├── page.tsx
│   │   └── [symbol]/
│   │       └── page.tsx
│   ├── watchlist/
│   │   └── page.tsx
│   └── portfolio/
│       └── page.tsx
├── components/
│   ├── StockTable.tsx
│   ├── CandlestickChart.tsx
│   ├── TrendTemplateCard.tsx
│   ├── VCPCard.tsx
│   ├── ScoreGauge.tsx
│   ├── PortfolioSummary.tsx
│   ├── QualityBadge.tsx      ← colour-coded A+/A/B/C/FAIL badge
│   └── NavBar.tsx
├── lib/
│   ├── api.ts
│   └── types.ts
├── public/
├── next.config.ts
├── tailwind.config.ts
└── package.json

--- FILE 1: frontend/lib/types.ts ---

Export TypeScript interfaces matching the Pydantic schemas exactly:

export interface TrendTemplate {
  passes: boolean;
  conditions_met: number;
  condition_1: boolean; condition_2: boolean; condition_3: boolean;
  condition_4: boolean; condition_5: boolean; condition_6: boolean;
  condition_7: boolean; condition_8: boolean;
}

export interface VCPDetails {
  qualified: boolean;
  contraction_count: number | null;
  max_depth_pct: number | null;
  final_depth_pct: number | null;
  vol_contraction_ratio: number | null;
  base_length_weeks: number | null;
  tightness_score: number | null;
}

export interface StockResult {
  symbol: string;
  run_date: string;
  score: number;
  setup_quality: "A+" | "A" | "B" | "C" | "FAIL";
  stage: number;
  stage_label: string;
  stage_confidence: number;
  trend_template_pass: boolean;
  conditions_met: number;
  vcp_qualified: boolean;
  breakout_triggered: boolean;
  entry_price: number | null;
  stop_loss: number | null;
  risk_pct: number | null;
  target_price: number | null;
  reward_risk_ratio: number | null;
  rs_rating: number;
  news_score: number | null;
  fundamental_pass: boolean;
  is_watchlist: boolean;
  trend_template_details: TrendTemplate | null;
  vcp_details: VCPDetails | null;
  llm_brief: string | null;
}

export interface APIResponse<T> {
  success: boolean;
  data: T;
  meta: Record<string, unknown> | null;
  error: string | null;
}

export interface Position {
  symbol: string; entry_date: string; entry_price: number;
  quantity: number; stop_loss: number; trailing_stop: number;
  days_held: number; unrealised_pnl: number; unrealised_pnl_pct: number;
  setup_quality: string;
}

export interface Trade {
  symbol: string; entry_date: string; exit_date: string;
  entry_price: number; exit_price: number; pnl: number;
  pnl_pct: number; r_multiple: number; exit_reason: string;
  setup_quality: string;
}

export interface PortfolioSummary {
  cash: number; open_value: number; total_value: number;
  initial_capital: number; total_return_pct: number;
  realised_pnl: number; unrealised_pnl: number;
  win_rate: number; total_trades: number;
  open_count: number; closed_count: number;
  profit_factor: number; avg_r_multiple: number;
  positions: Position[];
}

--- FILE 2: frontend/lib/api.ts ---

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<APIResponse<T>> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "X-API-Key": API_KEY, "Content-Type": "application/json", ...options?.headers },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json() as Promise<APIResponse<T>>;
}

// All typed API functions:
export const api = {
  getTopStocks: (params?: { quality?: string; limit?: number; date?: string }) =>
    apiFetch<StockResult[]>(`/api/v1/stocks/top?${new URLSearchParams(params as Record<string, string>)}`),

  getTrendStocks: (params?: { min_rs?: number; limit?: number }) =>
    apiFetch<StockResult[]>(`/api/v1/stocks/trend?${new URLSearchParams(params as Record<string, string>)}`),

  getVCPStocks: (params?: { min_quality?: string; limit?: number }) =>
    apiFetch<StockResult[]>(`/api/v1/stocks/vcp?${new URLSearchParams(params as Record<string, string>)}`),

  getStock: (symbol: string, date?: string) =>
    apiFetch<StockResult>(`/api/v1/stocks/${symbol}${date ? `?date=${date}` : ""}`),

  getStockHistory: (symbol: string, days?: number) =>
    apiFetch<{ symbol: string; history: Array<{ run_date: string; score: number; quality: string }> }>(
      `/api/v1/stocks/${symbol}/history${days ? `?days=${days}` : ""}`
    ),

  getWatchlist: () => apiFetch<StockResult[]>("/api/v1/watchlist"),

  addToWatchlist: (symbol: string) =>
    apiFetch(`/api/v1/watchlist/${symbol}`, { method: "POST" }),

  removeFromWatchlist: (symbol: string) =>
    apiFetch(`/api/v1/watchlist/${symbol}`, { method: "DELETE" }),

  getPortfolio: () => apiFetch<PortfolioSummary>("/api/v1/portfolio"),

  getTrades: (status?: "open" | "closed" | "all") =>
    apiFetch<Trade[]>(`/api/v1/portfolio/trades${status ? `?status=${status}` : ""}`),

  getHealth: () => apiFetch<{ status: string; last_run: string | null }>("/api/v1/health"),

  getMeta: () => apiFetch<Record<string, unknown>>("/api/v1/meta"),

  triggerRun: (scope: "all" | "watchlist" | "universe") =>
    apiFetch("/api/v1/run", { method: "POST", body: JSON.stringify({ scope }) }),
};

--- package.json ---

{
  "name": "sepa-ai-frontend",
  "version": "1.0.0",
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "14.x",
    "react": "18.x",
    "react-dom": "18.x",
    "swr": "^2.x",
    "lightweight-charts": "^4.x",
    "recharts": "^2.x",
    "lucide-react": "^0.x"
  },
  "devDependencies": {
    "typescript": "^5.x",
    "@types/react": "^18.x",
    "tailwindcss": "^3.x",
    "autoprefixer": "^10.x",
    "postcss": "^8.x"
  }
}
```


---

### PHASE 12 — STEP 2 of 5: Screener Table Page + Shared Components

#### Context files to attach
- `frontend/lib/types.ts`
- `frontend/lib/api.ts`

#### Prompt
```
You are building the screener page and shared components for the Next.js frontend.
Project root: /home/ubuntu/projects/sepa_ai/frontend/

TASK: Implement screener page + QualityBadge, ScoreGauge, StockTable components.

--- FILE 1: components/QualityBadge.tsx ---

const QUALITY_STYLES = {
  "A+": "bg-yellow-400 text-black font-bold",
  "A":  "bg-green-500 text-white font-bold",
  "B":  "bg-blue-500 text-white",
  "C":  "bg-gray-400 text-white",
  "FAIL": "bg-red-600 text-white",
};

export function QualityBadge({ quality }: { quality: string }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${QUALITY_STYLES[quality] ?? "bg-gray-300"}`}>
      {quality}
    </span>
  );
}

--- FILE 2: components/ScoreGauge.tsx ---

Visual score gauge (0–100). Use a simple SVG arc or Recharts RadialBar.
Colour: red (0–40), yellow (41–70), green (71–100).
Shows score number in centre.

export function ScoreGauge({ score }: { score: number }) { ... }

--- FILE 3: components/NavBar.tsx ---

Responsive top navigation bar with:
  - Logo: "📈 SEPA AI"
  - Nav links: Dashboard | Screener | Watchlist | Portfolio
  - API status indicator (green dot if /api/v1/health returns ok)
  - Mobile hamburger menu

--- FILE 4: components/StockTable.tsx ---

"use client";
import useSWR from "swr";

interface StockTableProps {
  initialData: StockResult[];
  showWatchlistBadge?: boolean;
  onRowClick?: (symbol: string) => void;
}

export function StockTable({ initialData, showWatchlistBadge, onRowClick }: StockTableProps) {
  // Client-side SWR polling every 60s for live updates
  // Sortable columns: score (default), symbol, rs_rating, conditions_met
  // Columns: Symbol | Score | Quality | Stage | TT | VCP | Breakout | Entry | Stop | Risk% | RS
  // Breakout: 🔴 label when breakout_triggered
  // Watchlist: ★ prefix when is_watchlist
  // Clicking a row calls onRowClick(symbol) → navigate to /screener/{symbol}
  // Mobile: hide Entry/Stop/Risk columns, show only Symbol, Score, Quality
}

--- FILE 5: app/screener/page.tsx ---

"use client";
import { useState } from "react";
import useSWR from "swr";

export default function ScreenerPage() {
  // Filter controls row: quality multiselect, min_rs slider, limit select, date select
  // Fetches from /api/v1/stocks/top with filter params (SWR polling 60s)
  // Renders <StockTable> with onRowClick → router.push(/screener/{symbol})
  // Export CSV button: triggers CSV download of current filtered results
  // Summary: "Showing {n} of {total} results | Last updated: {time}"
}

--- FILE 6: app/layout.tsx ---

Root layout with:
  - <NavBar /> at top
  - Tailwind dark/light mode class
  - Inter font (Google Fonts)
  - Global meta tags (title, description, viewport)
  - API status polling in NavBar

--- app/page.tsx (Dashboard Home) ---

Server component. Fetches:
  - /api/v1/meta → universe size, last run, A+ count
  - /api/v1/stocks/top?quality=A%2B&limit=5 → top 5 setups

Renders:
  - Hero row: 4 stat cards (Last Run, A+, A, Universe Size)
  - "Today's Top Setups" compact table (top 5 A+ only, no filters)
  - Link: "View all →" to /screener
```


---

### PHASE 12 — STEP 3 of 5: Stock Deep-Dive Page + Chart Components

#### Context files to attach
- `frontend/lib/types.ts`
- `frontend/lib/api.ts`
- `frontend/components/QualityBadge.tsx`
- `frontend/components/ScoreGauge.tsx`

#### Prompt
```
You are building the stock deep-dive page and chart components for the Next.js frontend.
Project root: /home/ubuntu/projects/sepa_ai/frontend/

TASK: Implement CandlestickChart, TrendTemplateCard, VCPCard, and the Stock page.

--- FILE 1: components/CandlestickChart.tsx ---

"use client";
import { createChart, CandlestickSeries, LineSeries } from "lightweight-charts";
import { useEffect, useRef } from "react";

interface OHLCVBar {
  time: string;    // YYYY-MM-DD
  open: number; high: number; low: number; close: number;
}

interface MALine { time: string; value: number; }

interface Props {
  ohlcv: OHLCVBar[];
  sma50?: MALine[];
  sma150?: MALine[];
  sma200?: MALine[];
  entryPrice?: number | null;
  stopLoss?: number | null;
  height?: number;
}

export function CandlestickChart({ ohlcv, sma50, sma150, sma200, entryPrice, stopLoss, height = 400 }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current || ohlcv.length === 0) return;

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height,
      layout: { background: { color: "#1a1a2e" }, textColor: "#e0e0e0" },
      grid: { vertLines: { color: "#2a2a3e" }, horzLines: { color: "#2a2a3e" } },
      timeScale: { timeVisible: true, borderColor: "#3a3a5e" },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#26a69a", downColor: "#ef5350",
      borderDownColor: "#ef5350", borderUpColor: "#26a69a",
      wickDownColor: "#ef5350", wickUpColor: "#26a69a",
    });
    candleSeries.setData(ohlcv);

    if (sma50)  { const s = chart.addLineSeries({ color: "#3a86ff", lineWidth: 1 }); s.setData(sma50); }
    if (sma150) { const s = chart.addLineSeries({ color: "#fb8500", lineWidth: 1 }); s.setData(sma150); }
    if (sma200) { const s = chart.addLineSeries({ color: "#e63946", lineWidth: 2 }); s.setData(sma200); }

    if (entryPrice) { /* add horizontal price line in green */ }
    if (stopLoss)   { /* add horizontal price line in red */ }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [ohlcv, sma50, sma150, sma200, entryPrice, stopLoss, height]);

  return <div ref={chartRef} className="w-full rounded-lg overflow-hidden" />;
}

--- FILE 2: components/TrendTemplateCard.tsx ---

Renders 8 conditions as a 2×4 grid of pass/fail badges.
Each cell: condition number + short label + ✅ or ❌.
Header: "Trend Template: {conditions_met}/8 ✅" with colour coding.
Also shows numeric details (close vs SMA values) in tooltips or expandable section.

--- FILE 3: components/VCPCard.tsx ---

Shows VCP metrics in a compact card:
  VCP Qualified: ✅ or ❌ (large badge)
  Contractions: {count} | Max Depth: {pct}% | Final Depth: {pct}%
  Volume Contraction: {ratio}× | Base: {weeks} weeks | Tightness: {pct}%
  Small ASCII VCP diagram (or SVG) illustrating the contraction pattern.

--- FILE 4: app/screener/[symbol]/page.tsx ---

Params: { symbol: string }
Data: Fetches /api/v1/stocks/{symbol} + /api/v1/stocks/{symbol}/history?days=30

Layout (3-column on desktop, stacked on mobile):
  Left col (2/3 width):
    - Symbol header: "{SYMBOL} — {setup_quality}" + QualityBadge + ScoreGauge
    - <CandlestickChart> with data from processed Parquet (served via new API endpoint or pre-computed)
    - Tabs: Trend Template | VCP | Fundamentals | LLM Brief | History

  Right col (1/3 width):
    - Score breakdown (weighted component scores as progress bars)
    - Key stats: Entry, Stop, Risk%, RS Rating, Stage
    - [⭐ Add to Watchlist] button (calls DELETE /api/v1/watchlist/{symbol})
    - History chart: score trend over 30 days (Recharts LineChart)

NOTE on OHLCV data for chart:
  The FastAPI layer does NOT currently serve raw OHLCV data.
  Add a new endpoint to api/routers/stocks.py:
    GET /api/v1/stocks/{symbol}/ohlcv?days=90
    Returns last N days of OHLCV + MA columns from feature Parquet
  Then use it here.
```


---

### PHASE 12 — STEP 4 of 5: Watchlist + Portfolio Pages

#### Context files to attach
- `frontend/lib/types.ts`
- `frontend/lib/api.ts`
- `frontend/components/StockTable.tsx`
- `frontend/components/QualityBadge.tsx`

#### Prompt
```
You are building the Watchlist and Portfolio pages for the Next.js frontend.
Project root: /home/ubuntu/projects/sepa_ai/frontend/

TASK: Implement app/watchlist/page.tsx and app/portfolio/page.tsx.

--- FILE 1: app/watchlist/page.tsx ---

"use client";

Layout:
  Header: "⭐ Watchlist" + [🚀 Run Now] button

  Watchlist management card:
    - Current symbols table: symbol, score, quality, added_at
    - [+ Add Symbol] input + button
    - [✕] remove button per row
    - "Powered by real-time API" note

  Results table:
    <StockTable initialData={watchlistResults} showWatchlistBadge />
    SWR polling every 30s for live updates

  [🚀 Run Watchlist Now]:
    Calls POST /api/v1/run {"scope":"watchlist"}
    Shows: "⏳ Running..." spinner → "✅ Done — {n} symbols screened" toast

--- FILE 2: app/portfolio/page.tsx ---

"use client";

Layout:
  Header: "💼 Paper Trading Portfolio"

  Summary cards row (4 columns, Recharts RadialBar for Return%):
    Total Return | Realised P&L | Win Rate | Open Positions

  Equity curve (Recharts AreaChart):
    X: date, Y: total_value
    Baseline: initial_capital as dashed line

  Tabs:
    Tab "Open Positions":
      Table: symbol, entry, current price (from API), unrealised P&L%, days held, stop
      P&L colour: green if > 0, red if < 0
      Refresh button (re-fetches current prices)

    Tab "Closed Trades":
      Table: symbol, entry, exit, P&L%, R-multiple, exit reason
      Colour rows: green if pnl > 0, red if pnl < 0
      R-multiple > 2.0: bold gold text

    Tab "Statistics":
      Win rate by quality (Recharts BarChart)
      Monthly P&L (Recharts BarChart, green/red bars)
      Hold time distribution (Recharts BarChart buckets)

  If no trades yet:
    Empty state card: "No paper trades yet. The pipeline creates trades automatically after each daily screen."

--- SHARED: PortfolioSummary.tsx ---

Reusable component for the 4-metric summary card row.
Props: summary: PortfolioSummary
Shows: Total Return %, Realised P&L (₹), Win Rate %, Open Count

--- MOBILE RESPONSIVENESS ---

All pages must work on 375px viewport (iPhone SE):
  - Tables scroll horizontally (overflow-x-auto wrapper)
  - Summary cards: 2×2 grid on mobile, 4×1 on desktop
  - NavBar: hamburger menu on mobile
  - Chart height reduced to 280px on mobile
  - Tab labels: icon-only on mobile, icon+text on desktop
```


---

### PHASE 12 — STEP 5 of 5: Vercel Deployment + Final Polish

#### Context files to attach
- `frontend/package.json`
- All pages and components from steps 1–4
- `api/routers/stocks.py` (need new OHLCV endpoint)

#### Prompt
```
You are finalising the Next.js frontend for production deployment.
Project root: /home/ubuntu/projects/sepa_ai/frontend/

TASK: Add OHLCV API endpoint, environment config, Vercel deployment config, and final polish.

--- FILE 1: Add to api/routers/stocks.py ---

@router.get("/{symbol}/ohlcv", response_model=APIResponse[list[dict]])
async def get_stock_ohlcv(
    symbol: str,
    days: int = 90,
    db: SQLiteStore = Depends(get_db),
    _: str = Depends(require_read_key),
):
    """
    Returns last N days of OHLCV + MA columns for chart rendering.
    Reads from data/features/{symbol}.parquet (last `days` rows).
    Returns list of dicts: {date, open, high, low, close, volume, sma_50, sma_150, sma_200, vol_ratio}
    Returns 404 if symbol not found.
    """

--- FILE 2: frontend/.env.example ---

NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_API_KEY=your_read_key_here

--- FILE 3: frontend/.env.production ---

NEXT_PUBLIC_API_URL=https://your-shreevault-domain-or-ip:8000
NEXT_PUBLIC_API_KEY=your_read_key_here

--- FILE 4: frontend/vercel.json ---

{
  "buildCommand": "npm run build",
  "outputDirectory": ".next",
  "framework": "nextjs",
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://your-api-server:8000/api/:path*"
    }
  ]
}

Note: The API rewrite in vercel.json proxies requests to ShreeVault.
This avoids CORS issues and hides the API key from the browser.
Update the destination URL to your actual server IP/domain.

--- FILE 5: frontend/next.config.ts ---

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL}/api/:path*`,
      },
    ];
  },
  images: { unoptimized: true },
};
export default nextConfig;

--- FINAL POLISH CHECKLIST ---

Implement these small improvements across all pages:

1. Loading skeletons (Tailwind animate-pulse) while SWR fetches
2. Error boundaries: if API unreachable, show "API offline" banner (not crash)
3. Empty state cards for all tables (no raw "no data" text)
4. Favicon: 📈 emoji favicon (public/favicon.ico)
5. Toast notifications for actions: "Added RELIANCE to watchlist ✅"
6. Keyboard navigation: Enter key submits "Add Symbol" input
7. Print-friendly CSS: @media print hides NavBar and buttons

--- DEPLOYMENT STEPS (document in frontend/README.md) ---

# Quick Deploy to Vercel

1. Push code to GitHub
2. Connect repo to Vercel (vercel.com → Import Project)
3. Set env vars in Vercel dashboard:
     NEXT_PUBLIC_API_URL = https://your-server:8000
     NEXT_PUBLIC_API_KEY = your_read_key
4. Deploy → get public URL

# Local Development

cd frontend
npm install
npm run dev   # starts at http://localhost:3000
# Make sure FastAPI is running at http://localhost:8000

# Production build test

npm run build && npm run start

--- MAKEFILE ADDITION ---

Add to root Makefile:
  frontend-dev:
      cd frontend && npm run dev

  frontend-build:
      cd frontend && npm run build

  frontend-deploy:
      cd frontend && npx vercel --prod
```


---
---

## APPENDIX — Quick Reference: Prompt Usage Guide

### How to use these prompts

Each prompt block is designed for a **fresh Claude session**. Follow this pattern:

```
1. Open a new Claude session
2. Attach the listed "Context files" (use filesystem read or paste content)
3. Paste the entire prompt block from the ``` code fence
4. Let Claude implement the files
5. Review, run tests: make test
6. Fix any issues in the same session
7. Commit: git add -A && git commit -m "Phase N Step M: description"
8. Move to next prompt
```

### Recommended session size per step

| Phase | Step | Estimated files | Session complexity |
|-------|------|-----------------|-------------------|
| 3     | 1–4  | 1–4 files each  | Low–Medium |
| 3     | 5    | 2 files         | Medium |
| 4     | 1–5  | 1–3 files each  | Medium |
| 5     | 1–4  | 1–4 files each  | Medium |
| 6     | 1–4  | 2–3 files each  | Medium |
| 7     | 1–4  | 1–2 files each  | Low–Medium |
| 8     | 1–5  | 2–3 files each  | Medium–High |
| 9     | 1–4  | mixed           | Medium |
| 10    | 1–5  | 2–3 files each  | Medium |
| 11    | 1–5  | 2–3 files each  | Medium |
| 12    | 1–5  | 2–4 files each  | Medium–High |

### After each phase completes

Run the full test suite to catch regressions:
```bash
make test          # all unit + integration tests
make lint          # ruff check
```

Update BUILD_STATUS.md to mark completed tasks.

### If a session runs out of context

Split the step into smaller sub-steps. For example, Phase 8 Step 2 (engine.py) can be split:
  - Sub-step A: BacktestTrade dataclass + simulate_trade()
  - Sub-step B: run_backtest() + BacktestResult

---

*PROMPTS.md — Minervini SEPA AI — Phases 3–12 build prompts*
*Generated: 2026-04-28 | Project: /home/ubuntu/projects/sepa_ai/*
