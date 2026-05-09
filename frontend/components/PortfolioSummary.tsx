/**
 * PortfolioSummary.tsx
 * Reusable 4-metric summary card row for the paper-trading portfolio.
 * Shows: Total Return %, Realised P&L (₹), Win Rate %, Open Count
 * Uses Recharts RadialBarChart for the return % visual.
 */
"use client";

import { RadialBarChart, RadialBar, ResponsiveContainer } from "recharts";
import type { PortfolioSummary } from "@/lib/types";
import { TrendingUp, TrendingDown, IndianRupee, Trophy, Layers } from "lucide-react";

interface Props { summary: PortfolioSummary }

function inr(v: number) {
  return `₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}
function pct(v: number) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

// ── Return RadialBar card ────────────────────────────────────────────────────
function ReturnCard({ returnPct }: { returnPct: number }) {
  const clamped = Math.min(Math.abs(returnPct), 100);
  const color   = returnPct >= 0 ? "#4ade80" : "#f87171";
  const data    = [{ name: "return", value: clamped, fill: color }];
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 flex items-center gap-4">
      <div className="w-16 h-16 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart
            cx="50%" cy="50%"
            innerRadius="60%" outerRadius="100%"
            startAngle={90} endAngle={-270}
            data={data} barSize={8}
          >
            <RadialBar dataKey="value" background={{ fill: "#1e293b" }} />
          </RadialBarChart>
        </ResponsiveContainer>
      </div>
      <div className="min-w-0">
        <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Total Return</p>
        <p className={`text-xl font-semibold num ${returnPct >= 0 ? "text-green-400" : "text-red-400"}`}>
          {pct(returnPct)}
        </p>
        <p className="text-xs text-slate-600 mt-0.5">vs initial capital</p>
      </div>
    </div>
  );
}

// ── Simple metric card ───────────────────────────────────────────────────────
interface MetricCardProps {
  label: string;
  value: string;
  sub?: string;
  color?: "green" | "red" | "neutral" | "blue";
  icon: React.ReactNode;
}
function MetricCard({ label, value, sub, color = "neutral", icon }: MetricCardProps) {
  const textColor = {
    green: "text-green-400", red: "text-red-400",
    neutral: "text-slate-100", blue: "text-blue-400",
  }[color];
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 flex items-start gap-3">
      <div className="p-2 bg-slate-800 rounded-lg text-slate-400 mt-0.5 shrink-0">{icon}</div>
      <div className="min-w-0">
        <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
        <p className={`text-xl font-semibold num ${textColor}`}>{value}</p>
        {sub && <p className="text-xs text-slate-600 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

// ── Exported component ───────────────────────────────────────────────────────
export default function PortfolioSummary({ summary: s }: Props) {
  const realisedColor: "green" | "red" | "neutral" =
    s.realised_pnl > 0 ? "green" : s.realised_pnl < 0 ? "red" : "neutral";
  const winRateColor: "green" | "red" | "neutral" =
    s.win_rate >= 0.5 ? "green" : "red";
  const sign = s.realised_pnl >= 0 ? "+" : "−";

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {/* 1 — Total Return with RadialBar */}
      <ReturnCard returnPct={s.total_return_pct} />

      {/* 2 — Realised P&L */}
      <MetricCard
        label="Realised P&L"
        value={`${sign}${inr(s.realised_pnl)}`}
        sub={`${s.closed_count} closed trades`}
        color={realisedColor}
        icon={<IndianRupee size={14} />}
      />

      {/* 3 — Win Rate */}
      <MetricCard
        label="Win Rate"
        value={`${(s.win_rate * 100).toFixed(1)}%`}
        sub={`Avg ${s.avg_r_multiple.toFixed(2)}R per trade`}
        color={winRateColor}
        icon={<Trophy size={14} />}
      />

      {/* 4 — Open Positions */}
      <MetricCard
        label="Open Positions"
        value={`${s.open_count}`}
        sub={`₹${s.open_value.toLocaleString("en-IN", { maximumFractionDigits: 0 })} exposure`}
        color="blue"
        icon={<Layers size={14} />}
      />
    </div>
  );
}
