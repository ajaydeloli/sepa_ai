"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { StockResult } from "@/lib/types";
import QualityBadge from "./QualityBadge";
import { api } from "@/lib/api";
import { Bookmark, BookmarkCheck, ChevronDown, ChevronUp } from "lucide-react";

type SortKey = "score" | "rs_rating" | "reward_risk_ratio" | "symbol" | "conditions_met";

export interface StockTableProps {
  /** Seed data — also used as SWR fallback while polling. */
  initialData: StockResult[];
  /** Show read-only ★ badge for watchlisted stocks. */
  showWatchlistBadge?: boolean;
  /** Show interactive watchlist toggle button. */
  showWatchlistToggle?: boolean;
  /** Called when a row is clicked; defaults to router.push(/screener/{symbol}). */
  onRowClick?: (symbol: string) => void;
  /** SWR key used for polling — omit to disable live refresh. */
  swrKey?: string;
  /** Called after a watchlist mutation so the parent can revalidate. */
  onMutate?: () => void;
}

export default function StockTable({
  initialData,
  showWatchlistBadge,
  showWatchlistToggle,
  onRowClick,
  swrKey,
  onMutate,
}: StockTableProps) {
  const router = useRouter();
  const [sortKey, setSortKey]  = useState<SortKey>("score");
  const [sortAsc, setSortAsc]  = useState(false);
  const [toggling, setToggling] = useState<string | null>(null);

  // Live-poll via SWR when a key is provided; fall back to initialData
  const { data: live, mutate } = useSWR(
    swrKey ?? null,
    () => api.getTopStocks({ limit: 200 }),
    { refreshInterval: 60_000, fallbackData: undefined },
  );
  const stocks: StockResult[] = live?.data ?? initialData;

  // ── Sort ──────────────────────────────────────────────────────────────────
  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc((v) => !v);
    else { setSortKey(key); setSortAsc(false); }
  };

  const sorted = [...stocks].sort((a, b) => {
    const av = (a[sortKey as keyof StockResult] ?? -Infinity) as number;
    const bv = (b[sortKey as keyof StockResult] ?? -Infinity) as number;
    return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
  });

  // ── Watchlist toggle ──────────────────────────────────────────────────────
  const toggleWatch = async (s: StockResult, e: React.MouseEvent) => {
    e.stopPropagation();
    setToggling(s.symbol);
    try {
      if (s.is_watchlist) await api.removeFromWatchlist(s.symbol);
      else                await api.addToWatchlist(s.symbol);
      mutate();
      onMutate?.();
    } finally { setToggling(null); }
  };

  // ── Helpers ───────────────────────────────────────────────────────────────
  const handleRowClick = (symbol: string) => {
    if (onRowClick) onRowClick(symbol);
    else router.push(`/screener/${symbol}`);
  };

  const SortIcon = ({ k }: { k: SortKey }) =>
    sortKey !== k ? null : sortAsc
      ? <ChevronUp size={11} />
      : <ChevronDown size={11} />;

  const SortTh = ({ k, label, className = "" }: { k: SortKey; label: string; className?: string }) => (
    <th
      className={`px-3 py-2 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider cursor-pointer hover:text-slate-200 select-none ${className}`}
      onClick={() => handleSort(k)}
    >
      <span className="flex items-center gap-0.5">{label}<SortIcon k={k} /></span>
    </th>
  );

  const Th = ({ label, className = "" }: { label: string; className?: string }) => (
    <th className={`px-3 py-2 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider ${className}`}>
      {label}
    </th>
  );

  if (!sorted.length) return <p className="text-slate-500 text-sm py-8 text-center">No results.</p>;

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800">
      <table className="w-full text-sm">
        <thead className="bg-slate-900">
          <tr>
            <SortTh k="symbol"           label="Symbol"  />
            <Th                           label="Quality" />
            <SortTh k="score"            label="Score"   />
            <Th                           label="Stage"   />
            <SortTh k="conditions_met"   label="TT"      />
            <SortTh k="rs_rating"        label="RS"      />
            <Th label="VCP" className="hidden sm:table-cell" />
            <Th label="BO"  className="hidden sm:table-cell" />
            {/* Hide price columns on mobile */}
            <Th label="Entry ₹"  className="hidden md:table-cell" />
            <Th label="Stop ₹"   className="hidden md:table-cell" />
            <SortTh k="reward_risk_ratio" label="R/R" className="hidden md:table-cell" />
            {(showWatchlistBadge || showWatchlistToggle) && <th className="w-8" />}
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr
              key={s.symbol}
              onClick={() => handleRowClick(s.symbol)}
              className="border-t border-slate-800 hover:bg-slate-800/40 transition-colors cursor-pointer"
            >
              {/* Symbol — ★ prefix for watchlisted stocks */}
              <td className="px-3 py-2 font-semibold text-blue-300 whitespace-nowrap">
                {s.is_watchlist && (
                  <span className="text-yellow-400 mr-1" title="On watchlist">★</span>
                )}
                <Link
                  href={`/screener/${s.symbol}`}
                  onClick={(e) => e.stopPropagation()}
                  className="hover:text-blue-200"
                >
                  {s.symbol}
                </Link>
              </td>

              <td className="px-3 py-2"><QualityBadge quality={s.setup_quality} /></td>
              <td className="px-3 py-2 num font-medium">{s.score}</td>
              <td className="px-3 py-2 text-slate-400 whitespace-nowrap">
                {s.stage}
                <span className="text-xs hidden lg:inline ml-1 text-slate-500">{s.stage_label}</span>
              </td>

              {/* TT: conditions_met / 8 */}
              <td className="px-3 py-2 num">
                <span className={s.trend_template_pass ? "text-green-400" : "text-slate-400"}>
                  {s.conditions_met}/8
                </span>
              </td>

              <td className="px-3 py-2 num">{s.rs_rating}</td>

              {/* VCP */}
              <td className="px-3 py-2 text-center hidden sm:table-cell">
                {s.vcp_qualified
                  ? <span className="text-green-400 font-bold">✓</span>
                  : <span className="text-slate-700">·</span>}
              </td>

              {/* Breakout — 🔴 label */}
              <td className="px-3 py-2 text-center hidden sm:table-cell">
                {s.breakout_triggered
                  ? <span title="Breakout triggered">🔴</span>
                  : <span className="text-slate-700">·</span>}
              </td>

              {/* Price cols — hidden on mobile */}
              <td className="px-3 py-2 num hidden md:table-cell">
                {s.entry_price != null ? `₹${s.entry_price.toFixed(2)}` : "—"}
              </td>
              <td className="px-3 py-2 num text-slate-400 hidden md:table-cell">
                {s.stop_loss != null ? `₹${s.stop_loss.toFixed(2)}` : "—"}
              </td>
              <td className="px-3 py-2 num hidden md:table-cell">
                {s.reward_risk_ratio?.toFixed(1) ?? "—"}
              </td>

              {/* Watchlist controls */}
              {showWatchlistBadge && !showWatchlistToggle && (
                <td className="px-3 py-2">
                  {s.is_watchlist && (
                    <BookmarkCheck size={14} className="text-blue-400" />
                  )}
                </td>
              )}
              {showWatchlistToggle && (
                <td className="px-3 py-2">
                  <button
                    onClick={(e) => toggleWatch(s, e)}
                    disabled={toggling === s.symbol}
                    className="text-slate-500 hover:text-blue-400 transition-colors disabled:opacity-50"
                    title={s.is_watchlist ? "Remove from watchlist" : "Add to watchlist"}
                  >
                    {s.is_watchlist
                      ? <BookmarkCheck size={14} className="text-blue-400" />
                      : <Bookmark size={14} />}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
