import { Fragment } from "react";

export function ConfusionMatrixTable({ labels, matrix }: { labels: string[]; matrix: number[][] }) {
  const maxValue = Math.max(1, ...matrix.flat());

  return (
    <div className="overflow-x-auto">
      <div className="inline-grid gap-[2px]" style={{ gridTemplateColumns: `110px repeat(${labels.length}, minmax(64px, 1fr))` }}>
        <div className="text-xs text-muted px-1 py-1">Actual \ Predicted</div>
        {labels.map((l) => (
          <div key={`h-${l}`} className="truncate px-1 py-1 text-center text-xs font-medium text-muted" title={l}>
            {l}
          </div>
        ))}
        {labels.map((rowLabel, rowIdx) => (
          <Fragment key={`row-${rowLabel}`}>
            <div className="truncate px-1 py-1 text-xs font-medium text-muted" title={rowLabel}>
              {rowLabel}
            </div>
            {matrix[rowIdx]?.map((value, colIdx) => {
              const intensity = value / maxValue;
              const isDiagonal = rowIdx === colIdx;
              return (
                <div
                  key={`${rowLabel}-${colIdx}`}
                  className="flex aspect-square items-center justify-center rounded-sm text-sm font-medium text-foreground"
                  style={{
                    backgroundColor: isDiagonal
                      ? `rgba(16, 185, 129, ${0.15 + intensity * 0.6})`
                      : `rgba(239, 68, 68, ${0.08 + intensity * 0.5})`,
                  }}
                >
                  {value}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
