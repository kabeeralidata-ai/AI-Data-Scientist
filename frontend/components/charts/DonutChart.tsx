"use client";

import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { CHART_COLORS, tooltipStyle } from "./chart-theme";
import { formatNumber } from "@/lib/utils";

export function DonutChart({
  data,
  onSliceClick,
  activeValue,
}: {
  data: { value: string; count: number; pct: number }[];
  onSliceClick?: (value: string) => void;
  activeValue?: string | null;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart margin={{ top: 8, right: 8, left: 8, bottom: 8 }}>
        <Pie
          data={data}
          dataKey="count"
          nameKey="value"
          innerRadius="55%"
          outerRadius="80%"
          paddingAngle={2}
          onClick={onSliceClick ? (entry: { value: string }) => onSliceClick(entry.value) : undefined}
          cursor={onSliceClick ? "pointer" : undefined}
        >
          {data.map((entry, i) => (
            <Cell
              key={entry.value}
              fill={CHART_COLORS[i % CHART_COLORS.length]}
              opacity={activeValue && activeValue !== entry.value ? 0.35 : 1}
            />
          ))}
        </Pie>
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(value: number, _name, item) => [
            `${formatNumber(value)} (${item.payload.pct}%)`,
            item.payload.value,
          ]}
        />
        <Legend
          verticalAlign="bottom"
          height={36}
          iconSize={8}
          wrapperStyle={{ fontSize: 11 }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
