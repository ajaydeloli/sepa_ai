/**
 * lib/api.ts
 * Typed API client — thin fetch wrappers for every FastAPI v1 endpoint.
 * Auth: X-API-Key header (set via NEXT_PUBLIC_API_KEY env var).
 */

import type {
  APIResponse,
  StockResult,
  StockHistory,
  OHLCVResponse,
  PortfolioSummary,
  Trade,
  HealthResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_KEY  = process.env.NEXT_PUBLIC_API_KEY  ?? "";

// ---------------------------------------------------------------------------
// Internal fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<APIResponse<T>> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${path}`);
  }

  return res.json() as Promise<APIResponse<T>>;
}

// ---------------------------------------------------------------------------
// Query-string helper — strips undefined/null values
// ---------------------------------------------------------------------------

function qs(params?: Record<string, string | number | boolean | undefined>): string {
  if (!params) return "";
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (!entries.length) return "";
  return "?" + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString();
}

// ---------------------------------------------------------------------------
// Public API surface
// ---------------------------------------------------------------------------

export const api = {
  // -- Stocks / Screener ----------------------------------------------------

  /** Top setups, optionally filtered by quality, date, and limit. */
  getTopStocks: (params?: { quality?: string; limit?: number; date?: string }) =>
    apiFetch<StockResult[]>(`/api/v1/stocks/top${qs(params)}`),

  /** All stocks passing the Trend Template, optionally filtered. */
  getTrendStocks: (params?: { min_rs?: number; limit?: number }) =>
    apiFetch<StockResult[]>(`/api/v1/stocks/trend${qs(params)}`),

  /** Stocks with a confirmed VCP pattern. */
  getVCPStocks: (params?: { min_quality?: string; limit?: number }) =>
    apiFetch<StockResult[]>(`/api/v1/stocks/vcp${qs(params)}`),

  /** Single symbol result for a given date (defaults to latest run). */
  getStock: (symbol: string, date?: string) =>
    apiFetch<StockResult>(
      `/api/v1/stocks/${symbol}${date ? `?date=${date}` : ""}`,
    ),

  /** Historical score / quality series for a symbol. */
  getStockHistory: (symbol: string, days?: number) =>
    apiFetch<StockHistory>(
      `/api/v1/stocks/${symbol}/history${days ? `?days=${days}` : ""}`,
    ),

  /** OHLCV bars + SMA lines from the feature Parquet. */
  getOHLCV: (symbol: string, days = 90) =>
    apiFetch<OHLCVResponse>(`/api/v1/stocks/${symbol}/ohlcv?days=${days}`),

  // -- Watchlist ------------------------------------------------------------

  /** All symbols currently on the user watchlist with latest results. */
  getWatchlist: () => apiFetch<StockResult[]>("/api/v1/watchlist"),

  /** Add a symbol to the watchlist. */
  addToWatchlist: (symbol: string) =>
    apiFetch(`/api/v1/watchlist/${symbol}`, { method: "POST" }),

  /** Remove a symbol from the watchlist. */
  removeFromWatchlist: (symbol: string) =>
    apiFetch(`/api/v1/watchlist/${symbol}`, { method: "DELETE" }),

  // -- Portfolio ------------------------------------------------------------

  /** Full portfolio summary including open positions. */
  getPortfolio: () => apiFetch<PortfolioSummary>("/api/v1/portfolio"),

  /** Trade history — open, closed, or all. */
  getTrades: (status?: "open" | "closed" | "all") =>
    apiFetch<Trade[]>(
      `/api/v1/portfolio/trades${status ? `?status=${status}` : ""}`,
    ),

  // -- Meta / ops -----------------------------------------------------------

  /** Liveness + last run timestamp. */
  getHealth: () => apiFetch<HealthResponse>("/api/v1/health"),

  /** System metadata (universe size, last run stats, etc.). */
  getMeta: () => apiFetch<Record<string, unknown>>("/api/v1/meta"),

  /**
   * Trigger a manual pipeline run (full scoring pipeline).
   * scope: "all" | "watchlist" | "universe"
   *
   * NOTE: Uses /api/v1/watchlist/run — NOT /api/v1/run.
   * /api/v1/run is a Phase-1 skeleton that only does OHLCV ingestion.
   * /api/v1/watchlist/run runs the full pipeline/runner.run_daily() with scoring.
   */
  triggerRun: (scope: "all" | "watchlist" | "universe") =>
    apiFetch("/api/v1/watchlist/run", {
      method: "POST",
      body: JSON.stringify({ scope }),
    }),
};
