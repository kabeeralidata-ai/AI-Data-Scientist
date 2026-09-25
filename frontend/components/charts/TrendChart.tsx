"use client";

import { useState } from "react";
import { BarChart2, LineChart as LineChartIcon } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, axisColor, gridStroke, tooltipStyle } from "./chart-theme";
import { cn } from "@/lib/utils";
import type { EdaTimeTrend } from "@/types";

export function TrendChart({ trend }: { trend: EdaTimeTrend }) {
  const [mode, setMode] = useState<"line" | "bar">("line");
  const data = trend.labels.map((label, i) => {
    const row: Record<string, string | number> = { label };
    trend.series.forEach((s) => {
      row[s.name] = s.values[i] ?? 0;
    });
    return row;
  });

  return (
    <div className="flex h-full flex-col">
      <div className="mb-1 flex justify-end gap-1">
        <button
          type="button"
          aria-label="Line chart"
          aria-pressed={mode === "line"}
          onClick={() => setMode("line")}
          className={cn(
            "rounded-md p-1 text-muted hover:bg-neutral-100 dark:hover:bg-neutral-800",
            mode === "line" && "bg-neutral-100 text-primary dark:bg-neutral-800"
          )}
        >
          <LineChartIcon className="h-4 w-4" />
        </button>
        <button
          type="button"
          aria-label="Bar chart"
          aria-pressed={mode === "bar"}
          onClick={() => setMode("bar")}
          className={cn(
            "rounded-md p-1 text-muted hover:bg-neutral-100 dark:hover:bg-neutral-800",
            mode === "bar" && "bg-neutral-100 text-primary dark:bg-neutral-800"
          )}
        >
          <BarChart2 className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          {mode === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: axisColor }} interval={Math.ceil(data.length / 6)} />
              <YAxis tick={{ fontSize: 11, fill: axisColor }} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {trend.series.map((s, i) => (
                <Line
                  key={s.name}
                  type="monotone"
                  dataKey={s.name}
                  stroke={CHART_COLORS[i % CHART_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                />
              ))}
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: axisColor }} interval={Math.ceil(data.length / 6)} />
              <YAxis tick={{ fontSize: 11, fill: axisColor }} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {trend.series.map((s, i) => (
                <Bar key={s.name} dataKey={s.name} fill={CHART_COLORS[i % CHART_COLORS.length]} radius={[3, 3, 0, 0]} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
