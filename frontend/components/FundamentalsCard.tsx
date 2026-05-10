/**
 * FundamentalsCard.tsx
 * Renders Minervini's 7 Fundamental Template conditions as a 2×4 grid of
 * pass/fail badges — mirroring TrendTemplateCard for consistency.
 *
 * Shows per-condition booleans from FundamentalDetails so users can see
 * exactly which of the 7 conditions drove the partial score in the
 * Score Breakdown panel (e.g. 4/7 means 4 conditions passed even though
 * fundamental_pass = false, which requires all 7).
 */
import type { FundamentalDetails } from "@/lib/types";

interface Props {
  details: FundamentalDetails | null;
  passes: boolean;
  newsScore?: number | null;
}

interface Condition {
  key: keyof FundamentalDetails;
  short: string;
  label: string;
}

const CONDITIONS: Condition[] = [
  {
    key: "f1_eps_positive",
    short: "EPS > 0",
    label: "Latest EPS is positive",
  },
  {
    key: "f2_eps_accelerating",
    short: "EPS Accel.",
    label: "EPS growth accelerating (most recent QoQ > prior QoQ)",
  },
  {
    key: "f3_sales_growth",
    short: "Sales ≥ 10% YoY",
    label: "Annual sales growth ≥ 10%",
  },
  {
    key: "f4_roe",
    short: "ROE ≥ 15%",
    label: "Return on Equity ≥ 15%",
  },
  {
    key: "f5_de_ratio",
    short: "D/E ≤ 1.0",
    label: "Debt-to-Equity ratio ≤ 1.0",
  },
  {
    key: "f6_promoter_holding",
    short: "Promoter ≥ 35%",
    label: "Promoter holding ≥ 35%",
  },
  {
    key: "f7_profit_growth",
    short: "Profit ↑",
    label: "Profit growth is positive",
  },
];

function ConditionCell({
  index,
  label,
  short,
  met,
}: {
  index: number;
  label: string;
  short: string;
  met: boolean;
}) {
  return (
    <div
      title={label}
      className={`relative flex flex-col items-center justify-center gap-1 rounded-lg border p-3 text-center cursor-default select-none transition-colors ${
        met
          ? "bg-green-500/10 border-green-500/30 text-green-300"
          : "bg-slate-800/60 border-slate-700/50 text-slate-500"
      }`}
    >
      <span className={`text-lg ${met ? "opacity-100" : "opacity-40"}`}>
        {met ? "✅" : "❌"}
      </span>
      <span className="text-[10px] font-semibold tracking-wide leading-tight">
        <span className="text-slate-500 mr-1">F{index}</span>
        {short}
      </span>
    </div>
  );
}

export default function FundamentalsCard({ details, passes, newsScore }: Props) {
  const met = details?.conditions_met ?? 0;

  const headerColor = passes
    ? "text-green-400 border-green-600/40 bg-green-600/15"
    : met >= 5
    ? "text-yellow-400 border-yellow-600/40 bg-yellow-600/15"
    : "text-red-400 border-red-600/40 bg-red-600/15";

  // Raw values table — only rendered when detail values are present
  const vals = details?.values;
  const valueRows: { label: string; key: string; fmt: (v: number | boolean | null) => string }[] = [
    { label: "EPS",             key: "eps",              fmt: (v) => v != null ? `₹${(v as number).toFixed(2)}` : "—" },
    { label: "Sales Growth YoY",key: "sales_growth_yoy", fmt: (v) => v != null ? `${(v as number).toFixed(1)}%` : "—" },
    { label: "ROE",             key: "roe",              fmt: (v) => v != null ? `${(v as number).toFixed(1)}%` : "—" },
    { label: "D/E Ratio",       key: "de_ratio",         fmt: (v) => v != null ? (v as number).toFixed(2) : "—" },
    { label: "Promoter Holding",key: "promoter_holding", fmt: (v) => v != null ? `${(v as number).toFixed(1)}%` : "—" },
    { label: "Profit Growth",   key: "profit_growth",    fmt: (v) => v != null ? `${(v as number).toFixed(1)}%` : "—" },
  ];

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 space-y-4">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-300">Fundamentals</h3>
        <span className={`text-xs px-2.5 py-0.5 rounded border font-semibold ${headerColor}`}>
          {met}/7 &nbsp;{passes ? "✅ PASS" : "❌ FAIL"}
        </span>
      </div>

      {/* ── Condition grid ──────────────────────────────────────────────── */}
      {details ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {CONDITIONS.map((c, i) => (
            <ConditionCell
              key={c.key}
              index={i + 1}
              label={c.label}
              short={c.short}
              met={details[c.key] as boolean}
            />
          ))}
          {/* 7 conditions → one empty cell to balance the 2×4 grid */}
          <div className="rounded-lg border border-dashed border-slate-800/60 p-3" />
        </div>
      ) : (
        <p className="text-slate-600 text-sm text-center py-8">
          No fundamental data available.
        </p>
      )}

      {/* ── Raw values table ────────────────────────────────────────────── */}
      {vals && Object.keys(vals).length > 0 && (
        <div className="border-t border-slate-800 pt-3">
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
            Raw Values
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {valueRows.map(({ label, key, fmt }) => (
              <div key={key} className="flex justify-between text-xs py-0.5 border-b border-slate-800/50">
                <span className="text-slate-500">{label}</span>
                <span className="text-slate-300 tabular-nums font-medium">
                  {fmt(vals[key] ?? null)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── News sentiment ──────────────────────────────────────────────── */}
      {newsScore != null && (
        <div className="border-t border-slate-800 pt-3 flex justify-between text-xs">
          <span className="text-slate-500">News Sentiment Score</span>
          <span className={`font-medium tabular-nums ${
            newsScore > 20 ? "text-green-400" : newsScore < -20 ? "text-red-400" : "text-slate-300"
          }`}>
            {newsScore.toFixed(2)}
          </span>
        </div>
      )}
    </div>
  );
}
