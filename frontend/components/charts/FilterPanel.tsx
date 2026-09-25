import { Button } from "@/components/ui/Button";
import { titleCase } from "@/lib/utils";
import type { EdaDateRangeFilter, EdaFilterOptions } from "@/types";

export function FilterPanel({
  filterOptions,
  categoricalFilters,
  dateFilters,
  onCategoricalChange,
  onDateChange,
  onReset,
  hasActiveFilters,
}: {
  filterOptions: EdaFilterOptions;
  categoricalFilters: Record<string, string[]>;
  dateFilters: Record<string, EdaDateRangeFilter>;
  onCategoricalChange: (column: string, values: string[]) => void;
  onDateChange: (column: string, range: EdaDateRangeFilter) => void;
  onReset: () => void;
  hasActiveFilters: boolean;
}) {
  const categoricalEntries = Object.entries(filterOptions.categorical);
  const dateEntries = Object.entries(filterOptions.date_ranges);

  const toggleValue = (column: string, value: string) => {
    const current = categoricalFilters[column] ?? [];
    const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
    onCategoricalChange(column, next);
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Filters</h3>
        <Button variant="ghost" size="sm" onClick={onReset} disabled={!hasActiveFilters}>
          Reset filters
        </Button>
      </div>

      {dateEntries.map(([column, range]) => {
        const active = dateFilters[column];
        return (
          <div key={column} className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted">{titleCase(column)}</label>
            <div className="flex items-center gap-2">
              <input
                type="date"
                aria-label={`${titleCase(column)} start date`}
                min={range.min}
                max={range.max}
                value={active?.start ?? ""}
                onChange={(e) => onDateChange(column, { ...active, start: e.target.value || undefined })}
                className="w-full rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-foreground focus-ring"
              />
              <span className="text-xs text-muted">to</span>
              <input
                type="date"
                aria-label={`${titleCase(column)} end date`}
                min={range.min}
                max={range.max}
                value={active?.end ?? ""}
                onChange={(e) => onDateChange(column, { ...active, end: e.target.value || undefined })}
                className="w-full rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-foreground focus-ring"
              />
            </div>
          </div>
        );
      })}

      {categoricalEntries.map(([column, options]) => {
        const selected = categoricalFilters[column] ?? [];
        return (
          <div key={column} className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted">{titleCase(column)}</label>
            <div className="max-h-40 overflow-y-auto rounded-lg border border-border">
              {options.map((option) => (
                <label
                  key={option}
                  className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-sm text-foreground hover:bg-neutral-50 dark:hover:bg-neutral-800"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(option)}
                    onChange={() => toggleValue(column, option)}
                    className="h-3.5 w-3.5 rounded border-border accent-primary"
                  />
                  <span className="truncate">{option}</span>
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
