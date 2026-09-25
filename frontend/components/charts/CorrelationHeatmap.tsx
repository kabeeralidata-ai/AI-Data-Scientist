"use client";

import { Fragment } from "react";

function colorFor(value: number): string {
  const clamped = Math.max(-1, Math.min(1, value));
  if (clamped >= 0) {
    const alpha = clamped;
    return `rgba(79, 70, 229, ${0.12 + alpha * 0.7})`;
  }
  const alpha = -clamped;
  return `rgba(239, 68, 68, ${0.12 + alpha * 0.7})`;
}

export function CorrelationHeatmap({ columns, matrix }: { columns: string[]; matrix: number[][] }) {
  if (!columns.length) return null;

  return (
    <div className="overflow-x-auto">
      <div
        className="inline-grid gap-[2px] text-[10px] sm:text-xs"
        style={{ gridTemplateColumns: `100px repeat(${columns.length}, minmax(52px, 1fr))` }}
      >
        <div />
        {columns.map((col) => (
          <div key={col} className="truncate px-1 py-1 text-center font-medium text-muted" title={col}>
            {col}
          </div>
        ))}
        {columns.map((rowCol, rowIdx) => (
          <Fragment key={`row-${rowCol}`}>
            <div className="truncate px-1 py-1 font-medium text-muted" title={rowCol}>
              {rowCol}
            </div>
            {columns.map((colCol, colIdx) => {
              const value = matrix[rowIdx]?.[colIdx] ?? 0;
              return (
                <div
                  key={`${rowCol}-${colCol}`}
                  className="flex aspect-square items-center justify-center rounded-sm text-foreground"
                  style={{ backgroundColor: colorFor(value) }}
                  title={`${rowCol} vs ${colCol}: ${value.toFixed(2)}`}
                >
                  {value.toFixed(2)}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
