/**
 * components/Skeleton.tsx
 * Reusable Tailwind animate-pulse skeleton blocks.
 *
 * Usage:
 *   <SkeletonRow />                     — single table-row placeholder
 *   <SkeletonTable rows={5} />          — full table skeleton
 *   <SkeletonCard />                    — stat / summary card placeholder
 *   <SkeletonCards count={4} />         — grid of stat cards
 */

function SkeletonLine({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse bg-slate-800 rounded ${className}`} />;
}

export function SkeletonRow() {
  return (
    <tr className="border-b border-slate-800">
      {[60, 40, 50, 50, 60, 55, 55].map((w, i) => (
        <td key={i} className="px-3 py-3">
          <SkeletonLine className={`h-3.5 w-${w > 55 ? "full" : `[${w}%]`}`} />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div className="rounded-xl border border-slate-800 overflow-hidden">
      <div className="bg-slate-900/60 px-3 py-2 border-b border-slate-800">
        <SkeletonLine className="h-3 w-1/3" />
      </div>
      <table className="w-full">
        <tbody>
          {Array.from({ length: rows }).map((_, i) => (
            <SkeletonRow key={i} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 space-y-2 animate-pulse">
      <SkeletonLine className="h-2.5 w-1/2" />
      <SkeletonLine className="h-7 w-3/4" />
      <SkeletonLine className="h-2 w-1/3" />
    </div>
  );
}

export function SkeletonCards({ count = 4 }: { count?: number }) {
  return (
    <div className={`grid grid-cols-2 sm:grid-cols-${count} gap-4`}>
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
