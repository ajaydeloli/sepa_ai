/**
 * VCPCard.tsx
 * Volatility Contraction Pattern metrics card with inline SVG diagram.
 */
import type { VCPDetails } from "@/lib/types";

interface Props { details: VCPDetails | null }

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-slate-800 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-sm font-medium tabular-nums">{value}</span>
    </div>
  );
}

/**
 * Minimalist SVG showing a series of narrowing price swings (VCP schematic).
 * Each "contraction" is an up-down zigzag whose amplitude shrinks by ~35% each time.
 */
function VCPDiagram({ count = 3 }: { count: number }) {
  const W = 200, H = 60;
  const cx = 8;                   // left margin
  const baseAmp = 22;             // first contraction amplitude
  const decay = 0.60;             // each contraction is ~60% of previous
  const segments = Math.max(2, Math.min(count, 5));

  // Build path points: start at mid, alternate high/low with shrinking amplitude
  const points: [number, number][] = [];
  const midY = H / 2;
  const segW = (W - cx * 2) / segments;

  points.push([cx, midY]);
  for (let i = 0; i < segments; i++) {
    const amp = baseAmp * Math.pow(decay, i);
    const x1 = cx + segW * (i + 0.5);
    const x2 = cx + segW * (i + 1);
    const dir = i % 2 === 0 ? -1 : 1;   // even = peak, odd = trough
    points.push([x1, midY + dir * amp]);
    points.push([x2, midY]);
  }

  const d = points
    .map(([x, y], idx) => `${idx === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");

  // Breakout arrow at the right
  const lastX = points[points.length - 1][0];
  const arrowY = midY;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[200px] opacity-70" aria-hidden>
      {/* Base line */}
      <line x1={cx} y1={midY} x2={W - cx} y2={midY} stroke="#334155" strokeWidth="0.8" />

      {/* Contraction zigzag */}
      <path d={d} fill="none" stroke="#3b82f6" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />

      {/* Breakout arrow */}
      <line x1={lastX} y1={arrowY} x2={lastX + 12} y2={arrowY - 14}
        stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" />
      <polygon
        points={`${lastX + 12},${arrowY - 14} ${lastX + 7},${arrowY - 13} ${lastX + 11},${arrowY - 8}`}
        fill="#22c55e"
      />

      {/* Labels */}
      <text x={cx} y={H - 2} fontSize="7" fill="#475569">Base</text>
      <text x={lastX + 14} y={arrowY - 12} fontSize="7" fill="#22c55e">BO</text>
    </svg>
  );
}

export default function VCPCard({ details }: Props) {
  const fmt = (v: number | null, suffix = "") =>
    v != null ? `${v.toFixed(2)}${suffix}` : "—";

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-300">VCP Pattern</h3>
        {details && (
          <span className={`text-xs px-2 py-0.5 rounded border font-medium ${
            details.qualified
              ? "bg-green-600/20 text-green-400 border-green-600/40"
              : "bg-slate-700/40 text-slate-500 border-slate-700"
          }`}>
            {details.qualified ? "✅ QUALIFIED" : "❌ NOT QUALIFIED"}
          </span>
        )}
      </div>

      {details ? (
        <>
          {/* Schematic diagram */}
          <div className="flex justify-center mb-3">
            <VCPDiagram count={details.contraction_count ?? 3} />
          </div>

          <Row label="Contractions"       value={details.contraction_count?.toString() ?? "—"} />
          <Row label="Max Depth"          value={fmt(details.max_depth_pct, "%")} />
          <Row label="Final Depth"        value={fmt(details.final_depth_pct, "%")} />
          <Row label="Volume Contraction" value={fmt(details.vol_contraction_ratio, "×")} />
          <Row label="Base Length"        value={details.base_length_weeks != null ? `${details.base_length_weeks}w` : "—"} />
          {/* Tightness — ATR₁₀/ATR₅₀ compression ratio; must be < 0.75 to qualify.
               Colour: green < 0.50 (strong), amber 0.50–0.75 (acceptable), red ≥ 0.75 (fail). */}
          <div className="flex justify-between items-center py-1.5 border-b border-slate-800 last:border-0">
            <span className="text-xs text-slate-500">
              Tightness
              <span className="ml-1 text-[10px] text-slate-600 font-normal">ATR₁₀/ATR₅₀</span>
            </span>
            <span className="flex items-center gap-1.5 text-sm font-medium tabular-nums">
              {details.tightness_score != null ? details.tightness_score.toFixed(2) : "—"}
              {details.tightness_score != null && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded border font-normal leading-none ${
                  details.tightness_score < 0.50
                    ? "bg-green-600/20 text-green-400 border-green-600/30"
                    : details.tightness_score < 0.75
                    ? "bg-amber-600/20 text-amber-400 border-amber-600/30"
                    : "bg-red-600/20 text-red-400 border-red-600/30"
                }`}>
                  {details.tightness_score < 0.50 ? "strong" : details.tightness_score < 0.75 ? "ok" : "fail"}
                </span>
              )}
            </span>
          </div>
          {/* Climax days — shown with a penalty badge when non-zero so users can
              see why an unqualified VCP lost up to −30 score points */}
          <div className="flex justify-between items-center py-1.5 border-b border-slate-800 last:border-0">
            <span className="text-xs text-slate-500">Climax Days in Base</span>
            <span className="flex items-center gap-1.5 text-sm font-medium tabular-nums">
              {details.climax_days_in_base ?? "—"}
              {details.climax_days_in_base != null && details.climax_days_in_base > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-600/20 text-red-400 border border-red-600/30 font-normal leading-none">
                  −{Math.min(30, details.climax_days_in_base * 10)}pts
                </span>
              )}
            </span>
          </div>
        </>
      ) : (
        <p className="text-slate-600 text-sm text-center py-8">No VCP data available.</p>
      )}
    </div>
  );
}
