"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts";
import { api } from "@/lib/api";
import PortfolioSummary from "@/components/PortfolioSummary";
import QualityBadge from "@/components/QualityBadge";
import {
  Briefcase, TrendingUp, TrendingDown, RefreshCw, Loader2, Inbox,
} from "lucide-react";
import type { Trade } from "@/lib/types";

// ── Tab type ─────────────────────────────────────────────────────────────────
type Tab = "open" | "closed" | "stats";

// ── Equity curve from closed trades ─────────────────────────────────────────
function buildEquityCurve(trades: Trade[], initialCapital: number) {
  if (!trades.length) return [];
  const sorted = [...trades].sort(
    (a, b) => new Date(a.exit_date).getTime() - new Date(b.exit_date).getTime(),
  );
  let equity = initialCapital;
  const points = [{ date: sorted[0].entry_date.slice(0, 7), total_value: initialCapital }];
  for (const t of sorted) {
    equity += t.pnl;
    points.push({ date: t.exit_date.slice(0, 7), total_value: Math.round(equity) });
  }
  return points;
}

// ── Win-rate by quality ───────────────────────────────────────────────────────
function winRateByQuality(trades: Trade[]) {
  const map: Record<string, { wins: number; total: number }> = {};
  for (const t of trades) {
    const q = t.setup_quality;
    if (!map[q]) map[q] = { wins: 0, total: 0 };
    map[q].total++;
    if (t.pnl_pct > 0) map[q].wins++;
  }
  return Object.entries(map).map(([quality, { wins, total }]) => ({
    quality,
    winRate: Math.round((wins / total) * 100),
    total,
  }));
}

// ── Monthly P&L ───────────────────────────────────────────────────────────────
function monthlyPnl(trades: Trade[]) {
  const map: Record<string, number> = {};
  for (const t of trades) {
    const month = t.exit_date.slice(0, 7);
    map[month] = (map[month] ?? 0) + t.pnl;
  }
  return Object.entries(map)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, pnl]) => ({ month, pnl: Math.round(pnl) }));
}

// ── Hold-time distribution ────────────────────────────────────────────────────
function holdTimeDist(trades: Trade[]) {
  const buckets = [
    { label: "0–5d",  min: 0,  max: 5  },
    { label: "5–10d", min: 5,  max: 10 },
    { label: "10–20d",min: 10, max: 20 },
    { label: "20–40d",min: 20, max: 40 },
    { label: "40d+",  min: 40, max: Infinity },
  ];
  return buckets.map(({ label, min, max }) => ({
    label,
    count: trades.filter((t) => {
      const days = Math.round(
        (new Date(t.exit_date).getTime() - new Date(t.entry_date).getTime()) / 86_400_000,
      );
      return days >= min && days < max;
    }).length,
  }));
}

// ── Tab button component ─────────────────────────────────────────────────────
function TabBtn({
  id, active, onClick, mobileLabel, desktopLabel,
}: { id: Tab; active: boolean; onClick: () => void; mobileLabel: string; desktopLabel: string }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
        active
          ? "bg-blue-700/30 text-blue-300 border border-blue-600/40"
          : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
      }`}
    >
      <span className="sm:hidden">{mobileLabel}</span>
      <span className="hidden sm:inline">{desktopLabel}</span>
    </button>
  );
}

// ── Chart tooltip ─────────────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label, prefix = "₹" }: {
  active?: boolean; payload?: {value: number}[]; label?: string; prefix?: string;
}) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value;
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="text-slate-400 mb-1">{label}</p>
      <p className="font-semibold text-slate-100">{prefix}{v.toLocaleString("en-IN")}</p>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PortfolioPage() {
  const [tab, setTab]           = useState<Tab>("open");
  const [refreshing, setRefresh]= useState(false);

  const { data: portfolioRes, isLoading, mutate: mutatePf } =
    useSWR("portfolio", () => api.getPortfolio(), { refreshInterval: 60_000 });

  const { data: tradesRes, mutate: mutateTrades } =
    useSWR("trades-all", () => api.getTrades("all"), { refreshInterval: 60_000 });

  const summary      = portfolioRes?.data;
  const allTrades    = tradesRes?.data ?? [];
  const closedTrades = allTrades.filter((t) => t.exit_date);

  const equityCurve = useMemo(
    () => buildEquityCurve(closedTrades, summary?.initial_capital ?? 0),
    [closedTrades, summary?.initial_capital],
  );
  const winByQuality  = useMemo(() => winRateByQuality(closedTrades), [closedTrades]);
  const monthlyData   = useMemo(() => monthlyPnl(closedTrades),        [closedTrades]);
  const holdDistData  = useMemo(() => holdTimeDist(closedTrades),       [closedTrades]);

  const handleRefresh = async () => {
    setRefresh(true);
    try { await Promise.all([mutatePf(), mutateTrades()]); }
    finally { setRefresh(false); }
  };

  // ── Loading state ──────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-500 text-sm gap-2">
        <Loader2 size={16} className="animate-spin" />
        Loading portfolio…
      </div>
    );
  }

  // ── Empty state ────────────────────────────────────────────────────────────
  if (!summary || (summary.total_trades === 0 && summary.open_count === 0)) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-2.5">
          <Briefcase size={22} className="text-blue-400" />
          <h1 className="text-2xl font-bold tracking-tight">Paper Trading Portfolio</h1>
        </div>
        <div className="bg-slate-900 rounded-xl border border-slate-800 p-16 text-center">
          <Inbox size={36} className="text-slate-700 mx-auto mb-4" />
          <p className="text-slate-400 font-medium mb-2">No paper trades yet.</p>
          <p className="text-slate-600 text-sm max-w-md mx-auto">
            The pipeline creates trades automatically after each daily screen.
            Run the screener to get started.
          </p>
        </div>
      </div>
    );
  }

  // ── Full page render ───────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Briefcase size={22} className="text-blue-400" />
          <h1 className="text-2xl font-bold tracking-tight">💼 Paper Trading Portfolio</h1>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200
                     bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
          <span className="hidden sm:inline">Refresh</span>
        </button>
      </div>

      {/* 4-metric summary row */}
      <PortfolioSummary summary={summary} />

      {/* Equity curve */}
      {equityCurve.length >= 2 && (
        <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
          <h2 className="text-sm font-semibold text-slate-300 mb-4">Equity Curve</h2>
          <div className="h-[280px] sm:h-[340px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityCurve} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} axisLine={false}
                       tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                <Tooltip content={<ChartTooltip />} />
                <ReferenceLine y={summary.initial_capital} stroke="#334155" strokeDasharray="4 4"
                               label={{ value: "Initial", fill: "#64748b", fontSize: 10 }} />
                <Area type="monotone" dataKey="total_value" stroke="#3b82f6" strokeWidth={2}
                      fill="url(#equityGrad)" dot={false} activeDot={{ r: 4, fill: "#3b82f6" }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="space-y-4">
        <div className="flex gap-2">
          <TabBtn id="open"   active={tab === "open"}   onClick={() => setTab("open")}   mobileLabel="📂" desktopLabel="Open Positions" />
          <TabBtn id="closed" active={tab === "closed"} onClick={() => setTab("closed")} mobileLabel="📋" desktopLabel="Closed Trades"  />
          <TabBtn id="stats"  active={tab === "stats"}  onClick={() => setTab("stats")}  mobileLabel="📊" desktopLabel="Statistics"      />
        </div>

        {/* ── Tab: Open Positions ── */}
        {tab === "open" && (
          <div>
            {summary.positions.length === 0 ? (
              <div className="bg-slate-900 rounded-xl border border-slate-800 p-10 text-center text-slate-500 text-sm">
                No open positions.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full text-sm">
                  <thead className="bg-slate-900">
                    <tr>
                      {["Symbol", "Quality", "Entry ₹", "Current ₹", "Unrealised P&L", "Days Held", "Stop ₹"].map((h) => (
                        <th key={h} className="px-3 py-2 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {summary.positions.map((pos) => (
                      <tr key={pos.symbol} className="border-t border-slate-800 hover:bg-slate-800/30 transition-colors">
                        <td className="px-3 py-2.5 font-semibold text-blue-300">{pos.symbol}</td>
                        <td className="px-3 py-2.5"><QualityBadge quality={pos.quality} /></td>
                        <td className="px-3 py-2.5 num text-sm">₹{pos.entry_price.toFixed(2)}</td>
                        <td className="px-3 py-2.5 num text-sm">₹{pos.current_price.toFixed(2)}</td>
                        <td className={`px-3 py-2.5 num text-sm font-medium ${pos.unrealised_pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                          <span className="flex items-center gap-1">
                            {pos.unrealised_pnl_pct >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                            {pos.unrealised_pnl_pct >= 0 ? "+" : ""}{(pos.unrealised_pnl_pct * 100).toFixed(2)}%
                          </span>
                        </td>
                        <td className="px-3 py-2.5 num text-sm text-slate-400">{pos.days_held}d</td>
                        <td className="px-3 py-2.5 num text-sm text-slate-500">₹{pos.stop_loss.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── Tab: Closed Trades ── */}
        {tab === "closed" && (
          <div>
            {closedTrades.length === 0 ? (
              <div className="bg-slate-900 rounded-xl border border-slate-800 p-10 text-center text-slate-500 text-sm">
                No closed trades yet.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full text-sm">
                  <thead className="bg-slate-900">
                    <tr>
                      {["Symbol", "Entry", "Exit", "Entry ₹", "Exit ₹", "P&L %", "R-Multiple", "Reason"].map((h) => (
                        <th key={h} className="px-3 py-2 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {closedTrades.map((t, i) => (
                      <tr key={i} className={`border-t border-slate-800 hover:bg-slate-800/30 transition-colors
                        ${t.pnl_pct > 0 ? "bg-green-950/10" : t.pnl_pct < 0 ? "bg-red-950/10" : ""}`}>
                        <td className="px-3 py-2.5 font-semibold text-blue-300">{t.symbol}</td>
                        <td className="px-3 py-2.5 num text-slate-500 text-xs">{t.entry_date}</td>
                        <td className="px-3 py-2.5 num text-slate-500 text-xs">{t.exit_date}</td>
                        <td className="px-3 py-2.5 num">₹{t.entry_price.toFixed(2)}</td>
                        <td className="px-3 py-2.5 num">₹{t.exit_price.toFixed(2)}</td>
                        <td className={`px-3 py-2.5 num font-medium ${t.pnl_pct > 0 ? "text-green-400" : "text-red-400"}`}>
                          {t.pnl_pct > 0 ? "+" : ""}{(t.pnl_pct * 100).toFixed(2)}%
                        </td>
                        <td className={`px-3 py-2.5 num font-medium ${t.r_multiple > 2 ? "text-yellow-400 font-bold" : t.r_multiple >= 1 ? "text-green-400" : "text-red-400"}`}>
                          {t.r_multiple.toFixed(2)}R
                        </td>
                        <td className="px-3 py-2.5 text-xs text-slate-500">{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── Tab: Statistics ── */}
        {tab === "stats" && (
          <div className="space-y-6">
            {closedTrades.length === 0 ? (
              <div className="bg-slate-900 rounded-xl border border-slate-800 p-10 text-center text-slate-500 text-sm">
                Statistics will appear after your first closed trade.
              </div>
            ) : (
              <>
                {/* Win rate by quality */}
                <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
                  <h3 className="text-sm font-semibold text-slate-300 mb-4">Win Rate by Setup Quality</h3>
                  <div className="h-[240px] sm:h-[280px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={winByQuality} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="quality" tick={{ fill: "#94a3b8", fontSize: 12 }} tickLine={false} axisLine={false} />
                        <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} axisLine={false}
                               tickFormatter={(v) => `${v}%`} domain={[0, 100]} />
                        <Tooltip content={({ active, payload, label }) =>
                          active && payload?.length ? (
                            <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs shadow-xl">
                              <p className="text-slate-400 mb-1">Quality: {label}</p>
                              <p className="font-semibold text-slate-100">Win Rate: {payload[0].value}%</p>
                              <p className="text-slate-500">Trades: {payload[0].payload.total}</p>
                            </div>
                          ) : null
                        } />
                        <Bar dataKey="winRate" radius={[4, 4, 0, 0]}>
                          {winByQuality.map((e, i) => (
                            <Cell key={i} fill={e.winRate >= 60 ? "#4ade80" : e.winRate >= 40 ? "#60a5fa" : "#f87171"} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                {/* Monthly P&L */}
                {monthlyData.length > 0 && (
                  <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
                    <h3 className="text-sm font-semibold text-slate-300 mb-4">Monthly P&L (₹)</h3>
                    <div className="h-[240px] sm:h-[280px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={monthlyData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="month" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} />
                          <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} axisLine={false}
                                 tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                          <Tooltip content={<ChartTooltip />} />
                          <ReferenceLine y={0} stroke="#334155" />
                          <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                            {monthlyData.map((e, i) => (
                              <Cell key={i} fill={e.pnl >= 0 ? "#4ade80" : "#f87171"} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}

                {/* Hold time distribution */}
                <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
                  <h3 className="text-sm font-semibold text-slate-300 mb-4">Hold Time Distribution</h3>
                  <div className="h-[220px] sm:h-[260px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={holdDistData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} />
                        <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} axisLine={false}
                               allowDecimals={false} />
                        <Tooltip content={({ active, payload, label }) =>
                          active && payload?.length ? (
                            <div className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs shadow-xl">
                              <p className="text-slate-400 mb-1">{label}</p>
                              <p className="font-semibold text-slate-100">{payload[0].value} trades</p>
                            </div>
                          ) : null
                        } />
                        <Bar dataKey="count" fill="#60a5fa" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
