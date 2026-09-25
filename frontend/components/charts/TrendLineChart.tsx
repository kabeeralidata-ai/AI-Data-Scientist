"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, axisColor, gridStroke, tooltipStyle } from "./chart-theme";

export function TrendLineChart({ labels, values }: { labels: string[]; values: number[] }) {
  const data = labels.map((label, i) => ({ label, value: values[i] }));

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: axisColor }} interval={Math.ceil(data.length / 6)} />
        <YAxis tick={{ fontSize: 11, fill: axisColor }} />
        <Tooltip contentStyle={tooltipStyle} />
        <Line type="monotone" dataKey="value" stroke={CHART_COLORS[0]} strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
