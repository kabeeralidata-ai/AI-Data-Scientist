"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, axisColor, gridStroke, tooltipStyle } from "./chart-theme";

export function HistogramChart({ bins, counts }: { bins: number[]; counts: number[] }) {
  const data = counts.map((count, i) => ({
    range: `${bins[i]?.toFixed(1)}–${bins[i + 1]?.toFixed(1)}`,
    count,
  }));

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
        <XAxis
          dataKey="range"
          tick={{ fontSize: 10, fill: axisColor }}
          interval={Math.ceil(data.length / 6)}
        />
        <YAxis tick={{ fontSize: 11, fill: axisColor }} allowDecimals={false} />
        <Tooltip contentStyle={tooltipStyle} />
        <Bar dataKey="count" fill={CHART_COLORS[1]} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
