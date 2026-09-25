import { Star } from "lucide-react";
import { formatNumber } from "@/lib/utils";
import type { EdaRatingBreakdown } from "@/types";

export function RatingBreakdown({ breakdown, label }: { breakdown: EdaRatingBreakdown; label: string }) {
  const scale = breakdown.max > breakdown.min ? breakdown.max : Math.max(breakdown.max, 1);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="text-2xl font-semibold text-foreground tabular-nums">{breakdown.average.toFixed(1)}</span>
        <div className="flex items-center gap-0.5 text-amber-400">
          {Array.from({ length: 5 }).map((_, i) => {
            const filled = (breakdown.average / scale) * 5 >= i + 1;
            return <Star key={i} className="h-4 w-4" fill={filled ? "currentColor" : "none"} strokeWidth={1.5} />;
          })}
        </div>
        <span className="text-sm text-muted">{label}</span>
      </div>
      <div className="flex flex-col gap-2">
        {breakdown.breakdown.map((row) => (
          <div key={row.value} className="flex items-center gap-3 text-sm">
            <span className="w-10 shrink-0 text-muted tabular-nums">{row.value}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
              <div className="h-full rounded-full bg-amber-400" style={{ width: `${row.pct}%` }} />
            </div>
            <span className="w-14 shrink-0 text-right text-muted tabular-nums">{formatNumber(row.count)}</span>
            <span className="w-12 shrink-0 text-right text-muted tabular-nums">{row.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
