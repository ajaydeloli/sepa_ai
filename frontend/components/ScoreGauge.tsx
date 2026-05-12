/**
 * ScoreGauge.tsx
 * SVG semi-circle arc gauge displaying a 0–100 SEPA score.
 * Colour zones: red (0–40) · yellow (41–70) · green (71–100)
 * No external chart dependency — pure SVG + Tailwind.
 */

import type { SetupQuality } from "@/lib/types";

interface Props {
  score: number;        // 0–100
  quality: SetupQuality | string;
  size?: "sm" | "md" | "lg";
}

/** Score-range colour: red → yellow → green */
function scoreColor(score: number): string {
  if (score <= 40) return "#f87171"; // red-400
  if (score <= 70) return "#facc15"; // yellow-400
  return "#4ade80";                  // green-400
}

/** Convert a 0-1 fraction to an SVG arc path on a 200×100 viewbox semi-circle.
 *
 * The semicircle runs from the left end (angle=180°) clockwise through the
 * top to the right end (angle=360°/0°).  The end angle is therefore:
 *   endAngle = π + fraction × π  =  π × (1 + fraction)
 *
 * Previous code used  π × (1 − fraction)  which placed the endpoint *below*
 * the viewbox for any fractional value, causing the arc to wrap around 264°+
 * and appear fully filled regardless of the actual score.
 *
 * Because we always sweep ≤ 180°, largeArc is always 0.
 */
function arcPath(fraction: number, cx: number, cy: number, r: number): string {
  const startAngle = Math.PI;
  const endAngle   = Math.PI * (1 + fraction);   // FIX: was (1 - fraction)
  const x1 = cx + r * Math.cos(startAngle);
  const y1 = cy + r * Math.sin(startAngle);
  const x2 = cx + r * Math.cos(endAngle);
  const y2 = cy + r * Math.sin(endAngle);
  return `M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`;  // largeArc always 0
}

const SIZE_MAP = {
  sm: { maxW: "max-w-[140px]", fontSize: 20, subSize: 8  },
  md: { maxW: "max-w-[220px]", fontSize: 28, subSize: 10 },
  lg: { maxW: "max-w-[280px]", fontSize: 34, subSize: 11 },
};

/** Named export — preferred */
export function ScoreGauge({ score, quality, size = "md" }: Props) {
  const cx = 100, cy = 90, r = 72;
  const trackColor  = "#1e293b"; // slate-800
  const fillColor   = scoreColor(score);
  const fraction    = Math.max(0, Math.min(1, score / 100));
  const { maxW, fontSize, subSize } = SIZE_MAP[size];

  const trackPath = arcPath(1,        cx, cy, r);
  const fillPath  = arcPath(fraction, cx, cy, r);

  return (
    <div className="flex flex-col items-center gap-1">
      <svg
        viewBox="0 0 200 100"
        className={`w-full ${maxW}`}
        aria-label={`Score: ${score} out of 100`}
      >
        {/* Track */}
        <path d={trackPath} fill="none" stroke={trackColor} strokeWidth="14" strokeLinecap="round" />

        {/* Filled arc */}
        {fraction > 0 && (
          <path
            d={fillPath}
            fill="none"
            stroke={fillColor}
            strokeWidth="14"
            strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 6px ${fillColor}88)` }}
          />
        )}

        {/* Score number */}
        <text x={cx} y={cy - 10} textAnchor="middle" fontSize={fontSize} fontWeight="700"
          fill="#f1f5f9" fontFamily="JetBrains Mono, monospace">
          {score}
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize={subSize} fill="#64748b" letterSpacing="2">
          /100
        </text>
      </svg>

      {/* Quality label */}
      <span className="text-xs font-semibold tracking-widest uppercase" style={{ color: fillColor }}>
        {quality}
      </span>
    </div>
  );
}

/** Default export for backward-compat */
export default ScoreGauge;
