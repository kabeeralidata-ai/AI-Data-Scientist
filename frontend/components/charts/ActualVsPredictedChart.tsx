"use client";

import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, axisColor, gridStroke, tooltipStyle } from "./chart-theme";

export function ActualVsPredictedChart({ data }: { data: { actual: number; predicted: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
        <XAxis type="number" dataKey="actual" name="Actual" tick={{ fontSize: 11, fill: axisColor }} />
        <YAxis type="number" dataKey="predicted" name="Predicted" tick={{ fontSize: 11, fill: axisColor }} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: "3 3" }} />
        <Scatter data={data} fill={CHART_COLORS[0]} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
