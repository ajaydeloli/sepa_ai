"use client";

import { useState, useRef } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import StockTable from "@/components/StockTable";
import QualityBadge from "@/components/QualityBadge";
import {
  Star, Rocket, Plus, X, Loader2, CheckCircle2, AlertCircle,
} from "lucide-react";

// ── Toast state type ─────────────────────────────────────────────────────────
type Toast = { type: "success" | "error"; msg: string } | null;

export default function WatchlistPage() {
  const [newSymbol, setNewSymbol]   = useState("");
  const [adding, setAdding]         = useState(false);
  const [removing, setRemoving]     = useState<string | null>(null);
  const [running, setRunning]       = useState(false);
  const [toast, setToast]           = useState<Toast>(null);
  const inputRef                    = useRef<HTMLInputElement>(null);

  // SWR — poll every 30 s
  const { data, isLoading, mutate } = useSWR(
    "watchlist",
    () => api.getWatchlist(),
    { refreshInterval: 30_000 },
  );
  const symbols = data?.data ?? [];

  // ── helpers ──────────────────────────────────────────────────────────────
  const showToast = (t: Toast) => {
    setToast(t);
    setTimeout(() => setToast(null), 4000);
  };

  const addSymbol = async () => {
    const sym = newSymbol.trim().toUpperCase();
    if (!sym) return;
    setAdding(true);
    try {
      await api.addToWatchlist(sym);
      setNewSymbol("");
      await mutate();
      showToast({ type: "success", msg: `${sym} added to watchlist` });
      inputRef.current?.focus();
    } catch {
      showToast({ type: "error", msg: `Failed to add ${sym}` });
    } finally { setAdding(false); }
  };

  const removeSymbol = async (sym: string) => {
    setRemoving(sym);
    try {
      await api.removeFromWatchlist(sym);
      await mutate();
      showToast({ type: "success", msg: `${sym} removed` });
    } catch {
      showToast({ type: "error", msg: `Failed to remove ${sym}` });
    } finally { setRemoving(null); }
  };

  const runWatchlist = async () => {
    setRunning(true);
    try {
      await api.triggerRun("watchlist");
      await mutate();
      showToast({ type: "success", msg: `✅ Done — ${symbols.length} symbol${symbols.length !== 1 ? "s" : ""} screened` });
    } catch {
      showToast({ type: "error", msg: "Pipeline run failed — check API connection" });
    } finally { setRunning(false); }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") addSymbol();
  };

  // ── render ───────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* Toast */}
      {toast && (
        <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-xl shadow-2xl text-sm font-medium transition-all
          ${toast.type === "success"
            ? "bg-green-900/90 border border-green-700/50 text-green-200"
            : "bg-red-900/90 border border-red-700/50 text-red-200"
          }`}>
          {toast.type === "success"
            ? <CheckCircle2 size={16} className="text-green-400" />
            : <AlertCircle  size={16} className="text-red-400" />}
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Star size={22} className="text-yellow-400 fill-yellow-400/30" />
          <h1 className="text-2xl font-bold tracking-tight">Watchlist</h1>
          <span className="text-sm text-slate-500 ml-1">
            {symbols.length} symbol{symbols.length !== 1 ? "s" : ""}
          </span>
        </div>
        <button
          onClick={runWatchlist}
          disabled={running || symbols.length === 0}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-semibold transition-colors"
        >
          {running
            ? <Loader2 size={14} className="animate-spin" />
            : <Rocket size={14} />}
          {running ? "Running…" : "Run Now"}
        </button>
      </div>

      {/* Watchlist management card */}
      <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-300">Manage Symbols</h2>
          <span className="text-xs text-slate-600">Powered by real-time API</span>
        </div>

        {/* Add symbol row */}
        <div className="px-4 py-3 border-b border-slate-800">
          <div className="flex gap-2">
            <input
              ref={inputRef}
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
              onKeyDown={handleKeyDown}
              placeholder="e.g. RELIANCE, INFY, TCS"
              maxLength={20}
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm
                         placeholder:text-slate-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
            />
            <button
              onClick={addSymbol}
              disabled={adding || !newSymbol.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-blue-700 hover:bg-blue-600
                         disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
            >
              {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
              Add Symbol
            </button>
          </div>
        </div>

        {/* Symbols table */}
        {isLoading ? (
          <div className="px-4 py-8 text-center text-slate-500 text-sm animate-pulse">Loading watchlist…</div>
        ) : symbols.length === 0 ? (
          <div className="px-4 py-10 text-center">
            <Star size={28} className="text-slate-700 mx-auto mb-2" />
            <p className="text-slate-500 text-sm">No symbols yet. Add one above.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-950/50">
                <tr>
                  {["Symbol", "Quality", "Score", "Added"].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody>
                {symbols.map((s) => (
                  <tr key={s.symbol} className="border-t border-slate-800 hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-2.5 font-semibold text-blue-300">{s.symbol}</td>
                    <td className="px-4 py-2.5"><QualityBadge quality={s.setup_quality} /></td>
                    <td className="px-4 py-2.5 num text-slate-200">{s.score}</td>
                    <td className="px-4 py-2.5 text-slate-500 text-xs">{s.run_date}</td>
                    <td className="px-4 py-2.5">
                      <button
                        onClick={() => removeSymbol(s.symbol)}
                        disabled={removing === s.symbol}
                        className="text-slate-600 hover:text-red-400 transition-colors disabled:opacity-50"
                        title={`Remove ${s.symbol}`}
                      >
                        {removing === s.symbol
                          ? <Loader2 size={13} className="animate-spin" />
                          : <X size={13} />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Results / screener output table */}
      {symbols.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Screener Results</h2>
          <StockTable
            initialData={symbols}
            showWatchlistBadge
            swrKey="watchlist-results"
            onMutate={mutate}
          />
        </section>
      )}
    </div>
  );
}
