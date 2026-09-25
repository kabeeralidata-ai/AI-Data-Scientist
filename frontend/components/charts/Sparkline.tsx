"use client";

import { useId } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";

export function Sparkline({ values, positive }: { values: number[]; positive: boolean }) {
  const reactId = useId();
  if (values.length < 2) return null;
  const data = values.map((value, i) => ({ i, value }));
  const color = positive ? "#10b981" : "#ef4444";
  const gradientId = `sparkline-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#${gradientId})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
