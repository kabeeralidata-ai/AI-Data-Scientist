"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, axisColor, gridStroke, tooltipStyle } from "./chart-theme";

export function HorizontalBarChart({
  data,
  onBarClick,
  activeValue,
}: {
  data: { value: string; count: number; pct: number }[];
  onBarClick?: (value: string) => void;
  activeValue?: string | null;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 8, right: 16, left: 0, bottom: 8 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 11, fill: axisColor }} allowDecimals={false} />
        <YAxis
          type="category"
          dataKey="value"
          tick={{ fontSize: 11, fill: axisColor }}
          width={90}
          tickFormatter={(value: string) => (value.length > 14 ? `${value.slice(0, 13)}…` : value)}
        />
        <Tooltip contentStyle={tooltipStyle} />
        <Bar
          dataKey="count"
          radius={[0, 4, 4, 0]}
          onClick={onBarClick ? (entry: { value: string }) => onBarClick(entry.value) : undefined}
          cursor={onBarClick ? "pointer" : undefined}
        >
          {data.map((entry) => (
            <Cell
              key={entry.value}
              fill={CHART_COLORS[1]}
              opacity={activeValue && activeValue !== entry.value ? 0.35 : 1}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
