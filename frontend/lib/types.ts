/**
 * lib/types.ts
 * TypeScript interfaces mirroring the Pydantic schemas exactly.
 * Source of truth: api/schemas/stock.py, portfolio.py, common.py
 */

// ---------------------------------------------------------------------------
// Stock / Screener
// ---------------------------------------------------------------------------

export interface TrendTemplate {
  passes: boolean;
  conditions_met: number;
  /** price > SMA_150 AND price > SMA_200 */
  condition_1: boolean;
  /** SMA_150 > SMA_200 */
  condition_2: boolean;
  /** SMA_200 slope > 0 */
  condition_3: boolean;
  /** SMA_50 > SMA_150 AND SMA_50 > SMA_200 */
  condition_4: boolean;
  /** price > SMA_50 */
  condition_5: boolean;
  /** price >= N% above 52-week low */
  condition_6: boolean;
  /** price within N% of 52-week high */
  condition_7: boolean;
  /** RS Rating >= threshold */
  condition_8: boolean;
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

export type SetupQuality = "A+" | "A" | "B" | "C" | "FAIL";

export interface StockResult {
  symbol: string;
  /** ISO-8601 date string e.g. "2026-05-08" */
  run_date: string;
  score: number;
  setup_quality: SetupQuality;
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
  /** 0–100 score from FundamentalResult.score; 50 = neutral (not evaluated) */
  fundamental_score: number;
  is_watchlist: boolean;
  trend_template_details: TrendTemplate | null;
  vcp_details: VCPDetails | null;
  llm_brief: string | null;
}

export interface StockHistoryPoint {
  run_date: string;
  score: number;
  quality: SetupQuality;
  stage?: number;
}

export interface StockHistory {
  symbol: string;
  history: StockHistoryPoint[];
}

// ---------------------------------------------------------------------------
// OHLCV / chart data
// ---------------------------------------------------------------------------

export interface OHLCVBar {
  time: string;   // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface MAPoint {
  time: string;
  value: number;
}

export interface OHLCVResponse {
  symbol: string;
  ohlcv: OHLCVBar[];
  sma50: MAPoint[] | null;
  sma150: MAPoint[] | null;
  sma200: MAPoint[] | null;
}

// ---------------------------------------------------------------------------
// Portfolio / Paper trading
// ---------------------------------------------------------------------------

/**
 * Slim position view returned inside PortfolioSummary.positions.
 * Matches SummaryPositionSchema (uses `current_price` + `quality`).
 */
export interface SummaryPosition {
  symbol: string;
  entry_price: number;
  current_price: number;
  unrealised_pnl_pct: number;
  days_held: number;
  stop_loss: number;
  trailing_stop: number;
  /** Maps to setup_quality on the underlying Position dataclass */
  quality: string;
}

/** Full position record — used by dedicated position endpoints */
export interface Position {
  symbol: string;
  entry_date: string;
  entry_price: number;
  quantity: number;
  stop_loss: number;
  trailing_stop: number;
  target_price?: number | null;
  days_held: number;
  unrealised_pnl: number;
  unrealised_pnl_pct: number;
  setup_quality: string;
}

export interface Trade {
  symbol: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  pnl_pct: number;
  r_multiple: number;
  exit_reason: string;
  setup_quality: string;
}

export interface PortfolioSummary {
  cash: number;
  open_value: number;
  total_value: number;
  initial_capital: number;
  total_return_pct: number;
  realised_pnl: number;
  unrealised_pnl: number;
  /** Fraction 0–1 */
  win_rate: number;
  total_trades: number;
  open_count: number;
  closed_count: number;
  profit_factor: number;
  avg_r_multiple: number;
  best_trade_pct: number;
  worst_trade_pct: number;
  avg_hold_days: number;
  positions: SummaryPosition[];
}

// ---------------------------------------------------------------------------
// API envelope
// ---------------------------------------------------------------------------

export interface APIResponse<T> {
  success: boolean;
  data: T;
  meta: Record<string, unknown> | null;
  error: string | null;
}

export interface HealthResponse {
  status: string;
  last_run: string | null;
}
