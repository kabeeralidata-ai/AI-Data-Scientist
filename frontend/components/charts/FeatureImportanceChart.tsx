"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, axisColor, gridStroke, tooltipStyle } from "./chart-theme";
import type { FeatureImportance } from "@/types";

export function FeatureImportanceChart({ data }: { data: FeatureImportance[] }) {
  const chartData = [...data].slice(0, 10).reverse();
  const height = Math.max(240, chartData.length * 32);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={chartData} layout="vertical" margin={{ top: 8, right: 24, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 11, fill: axisColor }} />
        <YAxis
          type="category"
          dataKey="name"
          width={120}
          tick={{ fontSize: 11, fill: axisColor }}
        />
        <Tooltip contentStyle={tooltipStyle} />
        <Bar dataKey="importance" fill={CHART_COLORS[4]} radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
