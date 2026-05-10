"use client";

import { useState, useRef } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import StockTable from "@/components/StockTable";
import QualityBadge from "@/components/QualityBadge";
import {
  Star, Rocket, Plus, X, Loader2, CheckCircle2, AlertCircle,
  Upload, FileText, ChevronDown, ChevronUp,
} from "lucide-react";

type Toast = { type: "success" | "error"; msg: string } | null;

interface WatchlistEntry {
  id: number;
  symbol: string;
  note: string | null;
  added_at: string;
  added_via: string;
  last_score: number | null;
  last_quality: string | null;
  last_run_at: string | null;
}

export default function WatchlistPage() {
  const [newSymbol, setNewSymbol] = useState("");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState<Toast>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Upload state
  const [showUpload, setShowUpload]   = useState(false);
  const [dragOver, setDragOver]       = useState(false);
  const [uploading, setUploading]     = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data, isLoading, mutate } = useSWR(
    "watchlist",
    () => api.getWatchlist(),
    { refreshInterval: 30_000 },
  );
  const symbols: WatchlistEntry[] = (data?.data as unknown as WatchlistEntry[]) ?? [];
  const watchlistSet = new Set(symbols.map((s) => s.symbol));

  // Fetch full screener results and filter to watchlist symbols
  const { data: screenerData } = useSWR(
    symbols.length > 0 ? "watchlist-screener" : null,
    () => api.getTopStocks({ limit: 500 }),
    { refreshInterval: 60_000 },
  );
  const screenerResults = (screenerData?.data ?? []).filter((s) =>
    watchlistSet.has(s.symbol),
  );

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
      showToast({ type: "success", msg: "Watchlist run queued" });
    } catch {
      showToast({ type: "error", msg: "Pipeline run failed" });
    } finally { setRunning(false); }
  };

  const ACCEPTED = [".csv", ".json", ".xlsx", ".xls", ".txt"];

  const uploadFile = async (file: File) => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!ACCEPTED.includes(ext)) {
      showToast({ type: "error", msg: `Unsupported file type. Use: ${ACCEPTED.join(", ")}` });
      return;
    }
    setUploading(true);
    try {
      const res = await api.uploadWatchlist(file);
      await mutate();
      const { added, skipped, invalid } = res.data;
      const parts = [`${added} added`];
      if (skipped)          parts.push(`${skipped} already existed`);
      if (invalid?.length)  parts.push(`${invalid.length} invalid`);
      showToast({ type: "success", msg: parts.join(" · ") });
      setShowUpload(false);
    } catch {
      showToast({ type: "error", msg: "Upload failed — check file format" });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadFile(file);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) uploadFile(file);
  };

  return (
    <div className="space-y-6">
      {toast && (
        <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-xl shadow-2xl text-sm font-medium transition-all ${
          toast.type === "success"
            ? "bg-green-900/90 border border-green-700/50 text-green-200"
            : "bg-red-900/90 border border-red-700/50 text-red-200"
        }`}>
          {toast.type === "success"
            ? <CheckCircle2 size={16} className="text-green-400" />
            : <AlertCircle size={16} className="text-red-400" />}
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
          {running ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
          {running ? "Running…" : "Run Now"}
        </button>
      </div>

      {/* Manage symbols card */}
      <div className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-300">Manage Symbols</h2>
          <span className="text-xs text-slate-600">Click a symbol to open its detail page</span>
        </div>

        <div className="px-4 py-3 border-b border-slate-800">
          <div className="flex gap-2">
            <input
              ref={inputRef}
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && addSymbol()}
              placeholder="e.g. RELIANCE, INFY, TCS"
              maxLength={20}
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm placeholder:text-slate-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-colors"
            />
            <button
              onClick={addSymbol}
              disabled={adding || !newSymbol.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
            >
              {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
              Add Symbol
            </button>
          </div>
        </div>

        {/* Upload watchlist file — collapsible */}
        <div className="border-b border-slate-800">
          <button
            onClick={() => setShowUpload((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-2.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800/40 transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <Upload size={13} />
              Upload watchlist file
              <span className="text-slate-600 ml-1">CSV · JSON · XLSX · TXT</span>
            </span>
            {showUpload ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>

          {showUpload && (
            <div className="px-4 pb-4">
              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.json,.xlsx,.xls,.txt"
                onChange={onFileChange}
                className="hidden"
              />

              {/* Drop zone */}
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
                onClick={() => !uploading && fileInputRef.current?.click()}
                className={`relative flex flex-col items-center justify-center gap-2 border-2 border-dashed rounded-xl px-6 py-8 cursor-pointer transition-colors ${
                  dragOver
                    ? "border-blue-500 bg-blue-900/20 text-blue-300"
                    : "border-slate-700 hover:border-slate-500 hover:bg-slate-800/30 text-slate-500"
                } ${uploading ? "pointer-events-none opacity-60" : ""}`}
              >
                {uploading ? (
                  <>
                    <Loader2 size={24} className="animate-spin text-blue-400" />
                    <p className="text-sm text-slate-400">Uploading…</p>
                  </>
                ) : (
                  <>
                    <FileText size={24} className={dragOver ? "text-blue-400" : "text-slate-600"} />
                    <p className="text-sm font-medium">
                      {dragOver ? "Drop file to upload" : "Click or drag a file here"}
                    </p>
                    <p className="text-xs text-slate-600">
                      One symbol per row · .csv, .json, .xlsx, .xls, .txt · max 1 MB
                    </p>
                  </>
                )}
              </div>
            </div>
          )}
        </div>

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
                    <th key={h} className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                  ))}
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody>
                {symbols.map((s) => (
                  <tr key={s.symbol} className="border-t border-slate-800 hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-2.5 font-semibold">
                      <Link href={`/screener/${s.symbol}`} className="text-blue-300 hover:text-blue-200 hover:underline">
                        {s.symbol}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5">
                      <QualityBadge quality={(s.last_quality ?? undefined) as string | undefined} />
                    </td>
                    <td className="px-4 py-2.5 num text-slate-200">
                      {s.last_score != null ? s.last_score : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500 text-xs">
                      {s.added_at ? s.added_at.slice(0, 10) : "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <button
                        onClick={() => removeSymbol(s.symbol)}
                        disabled={removing === s.symbol}
                        className="text-slate-600 hover:text-red-400 transition-colors disabled:opacity-50"
                        title={`Remove ${s.symbol}`}
                      >
                        {removing === s.symbol ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Screener results for watchlist symbols */}
      {screenerResults.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">
            Screener Results
            <span className="text-sm font-normal text-slate-500 ml-2">
              ({screenerResults.length} of {symbols.length} screened)
            </span>
          </h2>
          <StockTable initialData={screenerResults} showWatchlistBadge onMutate={mutate} />
        </section>
      )}

      {symbols.length > 0 && screenerResults.length === 0 && !isLoading && (
        <div className="bg-slate-900 rounded-xl border border-slate-800 px-6 py-8 text-center text-slate-500 text-sm">
          No screener results yet for your watchlist symbols. Run the pipeline using{" "}
          <span className="text-blue-400 font-medium">Run Now</span> above.
        </div>
      )}
    </div>
  );
}
