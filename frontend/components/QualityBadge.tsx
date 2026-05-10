import type { SetupQuality } from "@/lib/types";

interface Props {
  quality: SetupQuality | string | undefined;
  size?: "sm" | "md" | "lg";
}

const QUALITY_STYLES: Record<string, string> = {
  "A+":   "bg-yellow-400/20 text-yellow-300 border-yellow-500/40",
  "A":    "bg-green-500/20  text-green-300  border-green-500/40",
  "B":    "bg-blue-500/20   text-blue-300   border-blue-500/40",
  "C":    "bg-slate-400/20  text-slate-300  border-slate-500/40",
  "FAIL": "bg-red-600/20    text-red-400    border-red-600/40",
};

const SIZE_STYLES = {
  sm: "text-xs px-1.5 py-0.5",
  md: "text-xs px-2   py-0.5",
  lg: "text-sm px-3   py-1   font-semibold",
};

/** Named export — preferred. */
export function QualityBadge({ quality, size = "md" }: Props) {
  if (!quality) return <span className="text-slate-600 text-xs">—</span>;
  const style =
    QUALITY_STYLES[quality] ?? "bg-slate-700/40 text-slate-400 border-slate-600/40";
  return (
    <span
      className={`inline-flex items-center rounded border font-medium ${style} ${SIZE_STYLES[size]}`}
    >
      {quality}
    </span>
  );
}

/** Default export for backward-compat with existing imports. */
export default QualityBadge;
