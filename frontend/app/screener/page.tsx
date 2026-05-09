"use client";

import { useState, useCallback } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import StockTable from "@/components/StockTable";
import ApiOfflineBanner from "@/components/ApiOfflineBanner";
import { SkeletonTable } from "@/components/Skeleton";
import type { StockResult } from "@/lib/types";
import { Download, RefreshCw, SlidersHorizontal } from "lucide-react";

const QUALITY_OPTIONS = ["", "A+", "A", "B", "C"] as const;
const LIMIT_OPTIONS   = [25, 50, 100, 200] as const;

// ── CSV export helper ─────────────────────────────────────────────────────
function exportCSV(stocks: StockResult[]) {
  const headers = [
    "symbol","run_date","score","setup_quality","stage","rs_rating",
    "trend_template_pass","conditions_met","vcp_qualified","breakout_triggered",
    "entry_price","stop_loss","risk_pct","reward_risk_ratio",
  ];
  const rows = stocks.map((s) =>
    headers.map((h) => {
      const v = s[h as keyof StockResult];
      return v === null || v === undefined ? "" : String(v);
    }).join(",")
  );
  const csv = [headers.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `sepa_screener_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ScreenerPage() {
  const router = useRouter();

  // ── Filter state ──────────────────────────────────────────────────────────
  const [quality, setQuality] = useState("");
  const [tab,     setTab]     = useState<"top" | "trend" | "vcp">("top");
  const [limit,   setLimit]   = useState<number>(50);
  const [minRs,   setMinRs]   = useState(70);
  const [date,    setDate]    = useState("");
  const [showFilters, setShowFilters] = useState(false);

  // ── SWR fetch (60s poll) ─────────────────────────────────────────────────
  const fetcher = useCallback(() => {
    if (tab === "top")
      return api.getTopStocks({ quality: quality || undefined, limit, date: date || undefined });
    if (tab === "trend")
      return api.getTrendStocks({ min_rs: minRs, limit });
    return api.getVCPStocks({ min_quality: quality || undefined, limit });
  }, [tab, quality, limit, minRs, date]);

  const { data, isLoading, mutate, isValidating } = useSWR(
    ["screener", tab, quality, limit, minRs, date],
    fetcher,
    { refreshInterval: 60_000 },
  );

  const stocks = data?.data ?? [];
  const lastUpdated = data
    ? new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div className="space-y-5">

      <ApiOfflineBanner />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Screener</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFilters((v) => !v)}
            className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors ${
              showFilters ? "border-blue-500 text-blue-300 bg-blue-900/20" : "border-slate-700 text-slate-400 hover:border-slate-500"
            }`}
          >
            <SlidersHorizontal size={13} /> Filters
          </button>
          <button
            onClick={() => stocks.length && exportCSV(stocks)}
            disabled={!stocks.length}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-700 hover:border-slate-500 transition-colors disabled:opacity-40"
          >
            <Download size={13} /> Export CSV
          </button>
          <button
            onClick={() => mutate()}
            disabled={isValidating}
            className="flex items-center gap-1.5 text-xs bg-blue-700 hover:bg-blue-600 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-60"
          >
            <RefreshCw size={13} className={isValidating ? "animate-spin" : ""} /> Refresh
          </button>
        </div>
      </div>

      {/* ── Tabs ────────────────────────────────────────────────────────── */}
      <div className="flex gap-1 bg-slate-900 p-1 rounded-lg w-fit">
        {(["top", "trend", "vcp"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
              tab === t ? "bg-blue-700 text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t === "top" ? "Top Setups" : t === "trend" ? "Trend Template" : "VCP"}
          </button>
        ))}
      </div>

      {/* ── Collapsible filter controls ──────────────────────────────────── */}
      {showFilters && (
        <div className="flex flex-wrap items-end gap-4 bg-slate-900/60 border border-slate-800 rounded-xl p-4">
          {/* Quality select */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 uppercase tracking-wider">Quality</label>
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="bg-slate-800 border border-slate-700 rounded-lg text-sm px-3 py-1.5 text-slate-100 focus:outline-none focus:border-blue-500"
            >
              <option value="">All</option>
              {QUALITY_OPTIONS.filter(Boolean).map((q) => (
                <option key={q} value={q}>{q}</option>
              ))}
            </select>
          </div>

          {/* Min RS slider — only for Trend tab */}
          {tab === "trend" && (
            <div className="flex flex-col gap-1 min-w-[160px]">
              <label className="text-xs text-slate-400 uppercase tracking-wider">
                Min RS Rating: <span className="text-slate-200 font-semibold">{minRs}</span>
              </label>
              <input
                type="range" min={0} max={99} step={1}
                value={minRs}
                onChange={(e) => setMinRs(Number(e.target.value))}
                className="accent-blue-500"
              />
            </div>
          )}

          {/* Limit select */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 uppercase tracking-wider">Limit</label>
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="bg-slate-800 border border-slate-700 rounded-lg text-sm px-3 py-1.5 text-slate-100 focus:outline-none focus:border-blue-500"
            >
              {LIMIT_OPTIONS.map((l) => (
                <option key={l} value={l}>{l} results</option>
              ))}
            </select>
          </div>

          {/* Date picker — only for Top tab */}
          {tab === "top" && (
            <div className="flex flex-col gap-1">
              <label className="text-xs text-slate-400 uppercase tracking-wider">Date</label>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className="bg-slate-800 border border-slate-700 rounded-lg text-sm px-3 py-1.5 text-slate-100 focus:outline-none focus:border-blue-500"
              />
            </div>
          )}
        </div>
      )}

      {/* ── Summary bar ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>
          Showing <span className="text-slate-300 font-medium">{stocks.length}</span> result{stocks.length !== 1 ? "s" : ""}
          {limit && stocks.length === limit && (
            <span className="ml-1 text-slate-600">(limit: {limit})</span>
          )}
        </span>
        {lastUpdated && (
          <span>Last updated: <span className="text-slate-400">{lastUpdated}</span></span>
        )}
      </div>

      {/* ── Table ───────────────────────────────────────────────────────── */}
      {isLoading ? (
        <SkeletonTable rows={8} />
      ) : (
        <StockTable
          initialData={stocks}
          showWatchlistToggle
          swrKey={`screener-live-${tab}-${quality}-${limit}-${minRs}-${date}`}
          onRowClick={(symbol) => router.push(`/screener/${symbol}`)}
        />
      )}

    </div>
  );
}
