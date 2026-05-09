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

const CONDITIONS: { key: keyof TrendTemplate; label: string; short: string }[] = [
  { key: "condition_1", label: "Price above SMA150 & SMA200",  short: "P > MA150/200"  },
  { key: "condition_2", label: "SMA150 above SMA200",           short: "MA150 > MA200"  },
  { key: "condition_3", label: "SMA200 trending up (slope > 0)",short: "MA200 slope +"  },
  { key: "condition_4", label: "SMA50 above SMA150 & SMA200",  short: "MA50 > MA150/200"},
  { key: "condition_5", label: "Price above SMA50",             short: "P > MA50"       },
  { key: "condition_6", label: "Price ≥ 30% above 52w low",    short: "30% off low"    },
  { key: "condition_7", label: "Price within 25% of 52w high", short: "Near 52w high"  },
  { key: "condition_8", label: "RS Rating ≥ threshold",         short: "RS Rating ✓"    },
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
              label={c.label}
              short={c.short}
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
