/**
 * TrendTemplateCard.tsx
 * Renders Minervini's 8 Trend Template conditions as a 2×4 grid of pass/fail
 * badges with numeric tooltip details.
 */
import type { TrendTemplate } from "@/lib/types";

interface Props {
  details: TrendTemplate | null;
  passes: boolean;
}

interface ConditionDef {
  key: keyof TrendTemplate;
  getLabel: (d: TrendTemplate | null) => string;
  getShort: (d: TrendTemplate | null) => string;
}

const CONDITIONS: ConditionDef[] = [
  { key: "condition_1", getLabel: () => "Price above SMA150 & SMA200",  getShort: () => "P > MA150/200"  },
  { key: "condition_2", getLabel: () => "SMA150 above SMA200",         getShort: () => "MA150 > MA200"   },
  { key: "condition_3", getLabel: () => "SMA200 trending up (slope > 0)",getShort: () => "MA200 slope +"   },
  { key: "condition_4", getLabel: () => "SMA50 above SMA150 & SMA200",  getShort: () => "MA50 > MA150/200"},
  { key: "condition_5", getLabel: () => "Price above SMA50",            getShort: () => "P > MA50"        },
  {
    key: "condition_6",
    getLabel: (d) => `Price ≥ ${d?.pct_above_52w_low ?? 25}% above 52w low`,
    getShort: (d) => `${d?.pct_above_52w_low ?? 25}% off low`
  },
  {
    key: "condition_7",
    getLabel: (d) => `Price within ${d?.pct_below_52w_high ?? 25}% of 52w high`,
    getShort: (d) => `Near 52w high`
  },
  {
    key: "condition_8",
    getLabel: (d) => `RS Rating ≥ ${d?.min_rs_rating ?? 70}`,
    getShort: (d) => `RS Rating ≥ ${d?.min_rs_rating ?? 70}`
  },
];

function ConditionCell({ index, label, short, met }: {
  index: number; label: string; short: string; met: boolean;
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
        <span className="text-slate-500 mr-1">#{index}</span>{short}
      </span>
    </div>
  );
}

export default function TrendTemplateCard({ details, passes }: Props) {
  const met = details?.conditions_met ?? 0;
  const headerColor = passes
    ? "text-green-400 border-green-600/40 bg-green-600/15"
    : met >= 5
    ? "text-yellow-400 border-yellow-600/40 bg-yellow-600/15"
    : "text-red-400 border-red-600/40 bg-red-600/15";

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-slate-300">Trend Template</h3>
        <span className={`text-xs px-2.5 py-0.5 rounded border font-semibold ${headerColor}`}>
          {met}/8 &nbsp;{passes ? "✅ PASS" : "❌ FAIL"}
        </span>
      </div>

      {details ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {CONDITIONS.map((c, i) => (
            <ConditionCell
              key={c.key}
              index={i + 1}
              label={c.getLabel(details)}
              short={c.getShort(details)}
              met={details[c.key] as boolean}
            />
          ))}
        </div>
      ) : (
        <p className="text-slate-600 text-sm text-center py-8">
          No detail data available.
        </p>
      )}
    </div>
  );
}
