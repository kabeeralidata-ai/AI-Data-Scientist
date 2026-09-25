import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Sparkline } from "./Sparkline";
import { cn, formatNumber, formatPercent } from "@/lib/utils";
import type { EdaKpi } from "@/types";

function formatKpiValue(kpi: EdaKpi): string {
  if (kpi.format === "percent") return formatPercent(kpi.value);
  return formatNumber(kpi.value, kpi.value % 1 === 0 ? 0 : 2);
}

export function KpiCard({ kpi }: { kpi: EdaKpi }) {
  const trend = kpi.trend;
  const changePct = trend?.change_pct ?? null;
  const isPositive = changePct !== null && changePct >= 0;

  return (
    <Card className="p-4 sm:p-5">
      <p className="text-sm text-muted truncate" title={kpi.label}>
        {kpi.label}
      </p>
      <p className="mt-1 text-2xl font-semibold text-foreground tabular-nums">{formatKpiValue(kpi)}</p>
      <div className="mt-2 flex items-end justify-between gap-2">
        {changePct !== null ? (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 text-xs font-medium",
              isPositive ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"
            )}
          >
            {isPositive ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
            {Math.abs(changePct).toFixed(1)}%
          </span>
        ) : (
          <span />
        )}
        {trend && trend.sparkline.length >= 2 && (
          <div className="h-8 w-20">
            <Sparkline values={trend.sparkline} positive={isPositive} />
          </div>
        )}
      </div>
    </Card>
  );
}

export function KpiCardSkeleton() {
  return (
    <Card className="p-4 sm:p-5">
      <div className="h-4 w-24 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800" />
      <div className="mt-2 h-7 w-20 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800" />
      <div className="mt-3 h-3 w-12 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800" />
    </Card>
  );
}
