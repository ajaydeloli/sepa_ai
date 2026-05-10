"use client";

import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import StockTable from "@/components/StockTable";
import ApiOfflineBanner from "@/components/ApiOfflineBanner";
import { SkeletonCards, SkeletonTable } from "@/components/Skeleton";
import { AlertCircle, Activity, TrendingUp, ArrowRight } from "lucide-react";

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
      <p className="text-2xl font-semibold num">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

export default function Dashboard() {
  const { data: health } = useSWR("health", () => api.getHealth(), {
    refreshInterval: 60_000,
  });
  const { data: top, isLoading } = useSWR("top-stocks", () =>
    api.getTopStocks({ limit: 20 }),
  );
  const { data: vcpData }   = useSWR("vcp-count",   () => api.getVCPStocks({ limit: 200 }));
  const { data: trendData } = useSWR("trend-count", () => api.getTrendStocks({ limit: 200 }));

  const lastRun = health?.data?.last_run
    ? new Date(health.data.last_run).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })
    : "—";

  const aPlusCount  = top?.data?.filter((s) => s.setup_quality === "A+").length ?? 0;
  const aCount      = top?.data?.filter((s) => s.setup_quality === "A").length  ?? 0;
  const aPlusAndA   = top?.data ? aPlusCount + aCount : ("—" as string | number);

  return (
    <div className="space-y-6">

      <ApiOfflineBanner />

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-sm text-slate-500 mt-0.5">Minervini SEPA · Stage 2 Focus</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Activity
            size={14}
            className={health?.data?.status === "ok" ? "text-green-500" : "text-red-400"}
          />
          Last run: {lastRun}
        </div>
      </div>

      {/* Stat cards */}
      {isLoading ? (
        <SkeletonCards count={4} />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Last Run"      value={lastRun}                          sub="Pipeline timestamp"          />
          <StatCard label="A+ / A Setups" value={aPlusAndA}                        sub={`A+: ${aPlusCount}  ·  A: ${aCount}`} />
          <StatCard label="VCP Qualified" value={vcpData?.data?.length  ?? "—"}    sub="With base pattern"           />
          <StatCard label="Trend Pass"    value={trendData?.data?.length ?? "—"}   sub="8-condition template"        />
        </div>
      )}

      {/* Top 5 A+ setups */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <TrendingUp size={18} className="text-blue-400" />
            Today's Top Setups
          </h2>
          <Link
            href="/screener"
            className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            View all <ArrowRight size={12} />
          </Link>
        </div>

        {isLoading ? (
          <SkeletonTable rows={5} />
        ) : top?.data?.filter((s) => s.setup_quality === "A+" || s.setup_quality === "A").length ? (
          <StockTable
            initialData={top.data.filter((s) => s.setup_quality === "A+" || s.setup_quality === "A")}
            showWatchlistBadge
          />
        ) : (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
            <AlertCircle size={16} />
            No data — trigger a pipeline run or check API connection.
          </div>
        )}
      </section>
    </div>
  );
}
