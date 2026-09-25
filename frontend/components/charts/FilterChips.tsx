import { X } from "lucide-react";
import { titleCase } from "@/lib/utils";
import type { EdaDateRangeFilter } from "@/types";

interface Chip {
  key: string;
  label: string;
  onRemove: () => void;
}

export function FilterChips({
  categoricalFilters,
  dateFilters,
  onRemoveCategoricalValue,
  onRemoveDateFilter,
}: {
  categoricalFilters: Record<string, string[]>;
  dateFilters: Record<string, EdaDateRangeFilter>;
  onRemoveCategoricalValue: (column: string, value: string) => void;
  onRemoveDateFilter: (column: string) => void;
}) {
  const chips: Chip[] = [];

  for (const [column, values] of Object.entries(categoricalFilters)) {
    for (const value of values) {
      chips.push({
        key: `${column}:${value}`,
        label: `${titleCase(column)}: ${value}`,
        onRemove: () => onRemoveCategoricalValue(column, value),
      });
    }
  }

  for (const [column, range] of Object.entries(dateFilters)) {
    if (!range.start && !range.end) continue;
    const parts = [range.start, range.end].filter(Boolean).join(" – ");
    chips.push({
      key: `date:${column}`,
      label: `${titleCase(column)}: ${parts}`,
      onRemove: () => onRemoveDateFilter(column),
    });
  }

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {chips.map((chip) => (
        <button
          key={chip.key}
          type="button"
          onClick={chip.onRemove}
          className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/20"
        >
          {chip.label}
          <X className="h-3 w-3" />
        </button>
      ))}
    </div>
  );
}
