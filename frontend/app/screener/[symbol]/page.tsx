/**
 * app/screener/[symbol]/page.tsx
 * Full stock deep-dive page.
 * Layout: 2/3 left (chart + tabs) | 1/3 right (score panel + history)
 */
"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ArrowLeft, Bookmark, BookmarkCheck, Star } from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

import { api } from "@/lib/api";
import type { StockResult, StockHistoryPoint } from "@/lib/types";
import QualityBadge from "@/components/QualityBadge";
import ScoreGauge from "@/components/ScoreGauge";
import TrendTemplateCard from "@/components/TrendTemplateCard";
import VCPCard from "@/components/VCPCard";
import { CandlestickChart } from "@/components/CandlestickChart";

// ---------------------------------------------------------------------------
// Tab types
// ---------------------------------------------------------------------------
type Tab = "trend" | "vcp" | "fundamentals" | "llm";

const TABS: { id: Tab; label: string }[] = [
  { id: "trend",        label: "Trend Template" },
  { id: "vcp",          label: "VCP" },
  { id: "fundamentals", label: "Fundamentals" },
  { id: "llm",          label: "AI Brief" },
];

// ---------------------------------------------------------------------------
// Score breakdown config (weights from scorer.py)
// ---------------------------------------------------------------------------
interface ScoreRow { label: string; value: number; max: number; color: string }

function scoreBreakdown(s: StockResult): ScoreRow[] {
  return [
    { label: "Trend Template",  value: s.trend_template_pass ? 30 : 0,         max: 30, color: "bg-blue-500" },
    { label: "VCP Pattern",     value: s.vcp_qualified        ? 25 : 0,         max: 25, color: "bg-purple-500" },
    { label: "RS Rating",       value: Math.round((s.rs_rating / 100) * 20),   max: 20, color: "bg-yellow-500" },
    { label: "Breakout Trigger",value: s.breakout_triggered   ? 15 : 0,         max: 15, color: "bg-green-500" },
    { label: "Fundamentals",    value: s.fundamental_pass     ? 10 : 0,         max: 10, color: "bg-teal-500" },
  ];
}

// ---------------------------------------------------------------------------
// Score progress bars
// ---------------------------------------------------------------------------
function ScoreBreakdownPanel({ stock }: { stock: StockResult }) {
  const rows = scoreBreakdown(stock);
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Score Breakdown</h3>
      <div className="space-y-2.5">
        {rows.map(({ label, value, max, color }) => (
          <div key={label}>
            <div className="flex justify-between text-xs mb-1">
              <span className="text-slate-400">{label}</span>
              <span className="tabular-nums text-slate-300">{value}<span className="text-slate-600">/{max}</span></span>
            </div>
            <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${color}`}
                style={{ width: `${(value / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 pt-3 border-t border-slate-800 flex justify-between text-xs">
        <span className="text-slate-500">Total</span>
        <span className="font-semibold text-slate-200 tabular-nums">{stock.score}<span className="text-slate-600">/100</span></span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Key stats panel
// ---------------------------------------------------------------------------
function KeyStatsPanel({ stock }: { stock: StockResult }) {
  const stat = (label: string, value: string) => (
    <div key={label} className="flex justify-between items-center py-1.5 border-b border-slate-800 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-sm font-medium tabular-nums">{value}</span>
    </div>
  );
  const fmt = (v: number | null, prefix = "₹") =>
    v != null ? `${prefix}${v.toFixed(2)}` : "—";

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Key Stats</h3>
      {stat("Entry Price",   fmt(stock.entry_price))}
      {stat("Stop Loss",     fmt(stock.stop_loss))}
      {stat("Risk %",        stock.risk_pct != null ? `${stock.risk_pct.toFixed(2)}%` : "—")}
      {stat("Target",        fmt(stock.target_price))}
      {stat("R/R Ratio",     stock.reward_risk_ratio?.toFixed(2) ?? "—")}
      {stat("RS Rating",     stock.rs_rating.toString())}
      {stat("Stage",         `${stock.stage} — ${stock.stage_label}`)}
    </div>
  );
}

// ---------------------------------------------------------------------------
// History spark chart (Recharts)
// ---------------------------------------------------------------------------
const QUALITY_COLOR: Record<string, string> = {
  "A+": "#facc15", "A": "#4ade80", "B": "#60a5fa", "C": "#94a3b8", "FAIL": "#f87171",
};

function HistoryChart({ history }: { history: StockHistoryPoint[] }) {
  if (!history.length) return null;

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Score Trend (90d)</h3>
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={history} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis
            dataKey="run_date"
            tick={{ fontSize: 9, fill: "#475569" }}
            tickFormatter={(d: string) => d.slice(5)}   // MM-DD
            interval="preserveStartEnd"
          />
          <YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "#475569" }} />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 11 }}
            formatter={(v: number, _: string, entry: { payload?: StockHistoryPoint }) => [
              <span key="v">
                <span style={{ fontWeight: 600 }}>{v}</span>
                {entry.payload?.quality && (
                  <span style={{ marginLeft: 6, color: QUALITY_COLOR[entry.payload.quality] ?? "#94a3b8" }}>
                    {entry.payload.quality}
                  </span>
                )}
              </span>,
              "Score",
            ]}
            labelFormatter={(l: string) => l}
          />
          <Line
            type="monotone" dataKey="score"
            stroke="#3b82f6" strokeWidth={1.5} dot={false} activeDot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab content
// ---------------------------------------------------------------------------
function TabContent({ tab, stock }: { tab: Tab; stock: StockResult }) {
  if (tab === "trend") {
    return <TrendTemplateCard details={stock.trend_template_details} passes={stock.trend_template_pass} />;
  }
  if (tab === "vcp") {
    return <VCPCard details={stock.vcp_details} />;
  }
  if (tab === "fundamentals") {
    return (
      <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
        <h3 className="text-sm font-semibold text-slate-300 mb-2">Fundamentals</h3>
        <p className={`text-sm font-medium ${stock.fundamental_pass ? "text-green-400" : "text-slate-500"}`}>
          {stock.fundamental_pass ? "✅ Passes fundamental template" : "❌ Does not pass fundamental template"}
        </p>
        {stock.news_score != null && (
          <p className="text-xs text-slate-500 mt-2">
            News sentiment score: <span className="text-slate-300">{stock.news_score.toFixed(2)}</span>
          </p>
        )}
      </div>
    );
  }
  // llm
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-6">
      <h3 className="text-sm font-semibold text-slate-300 mb-3">AI Brief</h3>
      {stock.llm_brief ? (
        <p className="text-sm text-slate-400 leading-relaxed whitespace-pre-wrap">{stock.llm_brief}</p>
      ) : (
        <p className="text-slate-600 text-sm">No AI brief available for this symbol.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function StockDetailPage({
  params,
}: {
  params: { symbol: string };
}) {
  const { symbol } = params;
  const [activeTab, setActiveTab] = useState<Tab>("trend");

  const { data: stockData, error: stockError, mutate } = useSWR(
    ["stock", symbol],
    () => api.getStock(symbol),
  );
  const { data: histData } = useSWR(
    ["history", symbol],
    () => api.getStockHistory(symbol, 90),
  );
  const { data: ohlcvData } = useSWR(
    ["ohlcv", symbol],
    () => api.getOHLCV(symbol, 90),
  );

  const stock   = stockData?.data;
  const history = histData?.data?.history ?? [];
  const ohlcv   = ohlcvData?.data;

  if (stockError) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
        <p className="text-red-400 font-semibold text-sm">Failed to load {symbol}</p>
        <p className="text-slate-500 text-xs max-w-xs">
          {stockError?.message ?? "No screening result found. Run the pipeline first."}
        </p>
        <Link href="/watchlist" className="text-xs text-blue-400 hover:underline">
          ← Back to Watchlist
        </Link>
      </div>
    );
  }

  const toggleWatchlist = async () => {
    if (!stock) return;
    if (stock.is_watchlist) await api.removeFromWatchlist(symbol);
    else                    await api.addToWatchlist(symbol);
    mutate();
  };

  if (!stock) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-500 text-sm animate-pulse">
        Loading {symbol}…
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* ── Back link ─────────────────────────────────────────────────── */}
      <Link
        href="/screener"
        className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
      >
        <ArrowLeft size={12} /> Back to screener
      </Link>

      {/* ── Symbol header ─────────────────────────────────────────────── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-3xl font-bold tracking-tight">{symbol}</h1>
          <QualityBadge quality={stock.setup_quality} size="lg" />
          <span className="text-sm text-slate-500">
            Stage {stock.stage} · {stock.stage_label} · RS {stock.rs_rating}
          </span>
        </div>
        <button
          onClick={toggleWatchlist}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-slate-700 hover:border-blue-500/60 hover:text-blue-400 transition-colors"
        >
          {stock.is_watchlist
            ? <><BookmarkCheck size={15} className="text-blue-400" /> Watching</>
            : <><Star size={15} /> Add to Watchlist</>}
        </button>
      </div>

      {/* ── 2-column layout ───────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left col — 2/3 width */}
        <div className="lg:col-span-2 space-y-4">
          {/* Chart */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                {symbol} · 90-day Chart
              </span>
              <div className="flex gap-3 text-[10px] text-slate-500">
                <span className="flex items-center gap-1"><span className="inline-block w-5 h-0.5 bg-blue-500" />SMA50</span>
                <span className="flex items-center gap-1"><span className="inline-block w-5 h-0.5 bg-orange-500" />SMA150</span>
                <span className="flex items-center gap-1"><span className="inline-block w-5 h-0.5 bg-red-500 opacity-80" />SMA200</span>
              </div>
            </div>
            <CandlestickChart
              ohlcv={ohlcv?.ohlcv ?? []}
              sma50={ohlcv?.sma50}
              sma150={ohlcv?.sma150}
              sma200={ohlcv?.sma200}
              entryPrice={stock.entry_price}
              stopLoss={stock.stop_loss}
              height={360}
            />
          </div>

          {/* Tabs */}
          <div>
            <div className="flex gap-1 border-b border-slate-800 mb-4">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setActiveTab(t.id)}
                  className={`px-3 py-2 text-xs font-medium rounded-t transition-colors ${
                    activeTab === t.id
                      ? "text-blue-400 border-b-2 border-blue-500 -mb-px"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <TabContent tab={activeTab} stock={stock} />
          </div>
        </div>

        {/* Right col — 1/3 width */}
        <div className="space-y-4">
          {/* Score gauge */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 flex justify-center">
            <ScoreGauge score={stock.score} quality={stock.setup_quality} size="md" />
          </div>

          <ScoreBreakdownPanel stock={stock} />
          <KeyStatsPanel stock={stock} />
          <HistoryChart history={history} />
        </div>
      </div>
    </div>
  );
}
