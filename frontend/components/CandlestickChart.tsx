/**
 * CandlestickChart.tsx
 * Real OHLCV candlestick chart using lightweight-charts v4.
 * Renders SMA 50 / 150 / 200 overlays and optional entry/stop price lines.
 */
"use client";

import { createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { OHLCVBar, MAPoint } from "@/lib/types";

interface Props {
  ohlcv: OHLCVBar[];
  sma50?: MAPoint[] | null;
  sma150?: MAPoint[] | null;
  sma200?: MAPoint[] | null;
  entryPrice?: number | null;
  stopLoss?: number | null;
  height?: number;
}

export function CandlestickChart({
  ohlcv,
  sma50,
  sma150,
  sma200,
  entryPrice,
  stopLoss,
  height = 380,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || ohlcv.length === 0) return;

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height,
      layout: {
        background: { color: "#0f172a" },
        textColor: "#94a3b8",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      crosshair: { mode: 1 },
      timeScale: {
        borderColor: "#334155",
        timeVisible: true,
      },
      rightPriceScale: { borderColor: "#334155" },
    });

    // ── Candlestick series ──────────────────────────────────────────────
    const candles = chart.addCandlestickSeries({
      upColor:        "#22c55e",
      downColor:      "#ef4444",
      borderUpColor:  "#22c55e",
      borderDownColor:"#ef4444",
      wickUpColor:    "#22c55e",
      wickDownColor:  "#ef4444",
    });
    candles.setData(ohlcv);

    // ── MA overlays ────────────────────────────────────────────────────
    if (sma50?.length) {
      const s = chart.addLineSeries({ color: "#3b82f6", lineWidth: 1, title: "SMA50" });
      s.setData(sma50);
    }
    if (sma150?.length) {
      const s = chart.addLineSeries({ color: "#f97316", lineWidth: 1, title: "SMA150" });
      s.setData(sma150);
    }
    if (sma200?.length) {
      const s = chart.addLineSeries({ color: "#ef4444", lineWidth: 2, title: "SMA200" });
      s.setData(sma200);
    }

    // ── Entry / stop price lines ───────────────────────────────────────
    if (entryPrice) {
      candles.createPriceLine({
        price: entryPrice, color: "#22c55e", lineWidth: 1,
        lineStyle: 2, axisLabelVisible: true, title: "Entry",
      });
    }
    if (stopLoss) {
      candles.createPriceLine({
        price: stopLoss, color: "#ef4444", lineWidth: 1,
        lineStyle: 2, axisLabelVisible: true, title: "Stop",
      });
    }

    chart.timeScale().fitContent();

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    if (containerRef.current) ro.observe(containerRef.current);

    return () => { ro.disconnect(); chart.remove(); };
  }, [ohlcv, sma50, sma150, sma200, entryPrice, stopLoss, height]);

  if (ohlcv.length === 0) {
    return (
      <div className="flex items-center justify-center text-slate-600 text-sm" style={{ height }}>
        No chart data available
      </div>
    );
  }

  return (
    <div>
      {/* Legend */}
      <div className="flex gap-4 text-xs text-slate-500 mb-2 px-1">
        <span className="flex items-center gap-1"><span className="inline-block w-6 h-0.5 bg-blue-500" />SMA50</span>
        <span className="flex items-center gap-1"><span className="inline-block w-6 h-0.5 bg-orange-500" />SMA150</span>
        <span className="flex items-center gap-1"><span className="inline-block w-6 h-0.5 bg-red-500" />SMA200</span>
      </div>
      <div ref={containerRef} className="w-full rounded-lg overflow-hidden" style={{ height }} />
    </div>
  );
}

export default CandlestickChart;
