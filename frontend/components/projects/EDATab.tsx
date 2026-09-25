"use client";

import { useEffect, useState } from "react";
import { Filter, RefreshCw } from "lucide-react";
import { useFilteredEda } from "@/hooks/useAnalysis";
import { useDataset } from "@/hooks/useDatasets";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui/States";
import { ChartCard } from "@/components/charts/ChartCard";
import { DonutChart } from "@/components/charts/DonutChart";
import { HorizontalBarChart } from "@/components/charts/HorizontalBarChart";
import { TrendChart } from "@/components/charts/TrendChart";
import { RatingBreakdown } from "@/components/charts/RatingBreakdown";
import { KpiCard, KpiCardSkeleton } from "@/components/charts/KpiCard";
import { FilterPanel } from "@/components/charts/FilterPanel";
import { FilterChips } from "@/components/charts/FilterChips";
import { ExplainButton } from "@/components/ai/ExplainButton";
import { getErrorMessage } from "@/lib/api";
import { formatNumber } from "@/lib/utils";
import type { EdaDateRangeFilter, EdaFilterRequest } from "@/types";

const EMPTY_FILTERS: EdaFilterRequest = { categorical_filters: {}, date_filters: {} };

export function EDATab({ datasetId, onRun }: { datasetId: string | null; onRun?: () => void }) {
  const { data: dataset } = useDataset(datasetId ?? undefined);
  const [filters, setFilters] = useState<EdaFilterRequest>(EMPTY_FILTERS);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const debouncedFilters = useDebouncedValue(filters, 350);
  const { data, isLoading, isFetching, isError, error, refetch, dataUpdatedAt } = useFilteredEda(
    datasetId ?? undefined,
    debouncedFilters
  );

  const [prevDatasetId, setPrevDatasetId] = useState(datasetId);
  const [onRunFiredFor, setOnRunFiredFor] = useState<string | null>(null);
  if (datasetId !== prevDatasetId) {
    setPrevDatasetId(datasetId);
    setFilters(EMPTY_FILTERS);
  }
  useEffect(() => {
    if (data && datasetId && onRunFiredFor !== datasetId) {
      setOnRunFiredFor(datasetId);
      onRun?.();
    }
  }, [data, datasetId, onRunFiredFor, onRun]);

  if (!datasetId) {
    return <EmptyState title="Select a dataset first" description="Choose a dataset from the Dataset tab to explore it." />;
  }

  if (isError) {
    return <ErrorState description={getErrorMessage(error)} onRetry={() => refetch()} />;
  }

  if (isLoading || !data) {
    return <LoadingState label="Building dashboard..." />;
  }

  const isRefreshing = isFetching;
  const hasActiveFilters =
    Object.keys(filters.categorical_filters).length > 0 || Object.keys(filters.date_filters).length > 0;

  const setCategoricalFilter = (column: string, values: string[]) => {
    setFilters((prev) => {
      const next = { ...prev.categorical_filters };
      if (values.length === 0) delete next[column];
      else next[column] = values;
      return { ...prev, categorical_filters: next };
    });
  };

  const setDateFilter = (column: string, range: EdaDateRangeFilter) => {
    setFilters((prev) => {
      const next = { ...prev.date_filters };
      if (!range.start && !range.end) delete next[column];
      else next[column] = range;
      return { ...prev, date_filters: next };
    });
  };

  const removeCategoricalValue = (column: string, value: string) => {
    const current = filters.categorical_filters[column] ?? [];
    setCategoricalFilter(column, current.filter((v) => v !== value));
  };

  const removeDateFilter = (column: string) => {
    setFilters((prev) => {
      const next = { ...prev.date_filters };
      delete next[column];
      return { ...prev, date_filters: next };
    });
  };

  const resetFilters = () => setFilters(EMPTY_FILTERS);

  const handleCrossFilterClick = (column: string, value: string) => {
    const current = filters.categorical_filters[column] ?? [];
    if (current.length === 1 && current[0] === value) {
      setCategoricalFilter(column, []);
    } else {
      setCategoricalFilter(column, [value]);
    }
  };

  const activeValueFor = (column: string): string | null => {
    const values = filters.categorical_filters[column];
    return values && values.length === 1 ? values[0] : null;
  };

  const filterPanelProps = {
    filterOptions: data.filter_options,
    categoricalFilters: filters.categorical_filters,
    dateFilters: filters.date_filters,
    onCategoricalChange: setCategoricalFilter,
    onDateChange: setDateFilter,
    onReset: resetFilters,
    hasActiveFilters,
  };

  const donutAndBarCharts = [...data.donut_charts, ...data.bar_charts];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">{dataset?.file_name ?? "Dataset"}</h2>
          <p className="text-xs text-muted">
            {formatNumber(data.summary.total_rows)} rows &middot; {data.summary.columns} columns
            {dataUpdatedAt > 0 && (
              <> &middot; Last analyzed {new Date(dataUpdatedAt).toLocaleTimeString()}</>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isRefreshing && <Spinner className="h-4 w-4" />}
          <ExplainButton datasetId={datasetId} label="Explain these findings" />
          <Button variant="outline" size="sm" className="lg:hidden" onClick={() => setDrawerOpen(true)}>
            <Filter className="h-4 w-4" /> Filters
          </Button>
          <Button variant="outline" size="sm" onClick={() => refetch()} isLoading={isRefreshing}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <FilterChips
        categoricalFilters={filters.categorical_filters}
        dateFilters={filters.date_filters}
        onRemoveCategoricalValue={removeCategoricalValue}
        onRemoveDateFilter={removeDateFilter}
      />

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Filters">
        <div className="p-4">
          <FilterPanel {...filterPanelProps} />
        </div>
      </Drawer>

      <div className="flex flex-col gap-5 lg:flex-row">
        <aside className="hidden lg:block lg:w-64 shrink-0">
          <Card className="p-4">
            <FilterPanel {...filterPanelProps} />
          </Card>
        </aside>

        <div className="min-w-0 flex-1 flex flex-col gap-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {data.kpis.map((kpi) =>
              isRefreshing ? <KpiCardSkeleton key={kpi.key} /> : <KpiCard key={kpi.key} kpi={kpi} />
            )}
          </div>

          {data.summary.filtered_rows === 0 ? (
            <EmptyState
              title="No data matches these filters"
              description="Try removing or adjusting a filter to see results."
              action={
                <Button variant="outline" size="sm" onClick={resetFilters}>
                  Reset filters
                </Button>
              }
            />
          ) : (
            <>
              {donutAndBarCharts.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                  {data.donut_charts.map((chart) => (
                    <ChartCard key={chart.column} title={chart.label}>
                      {isRefreshing ? (
                        <Skeleton className="h-full w-full rounded-lg" />
                      ) : (
                        <DonutChart
                          data={chart.items}
                          activeValue={activeValueFor(chart.column)}
                          onSliceClick={(value) => handleCrossFilterClick(chart.column, value)}
                        />
                      )}
                    </ChartCard>
                  ))}
                  {data.bar_charts.map((chart) => (
                    <ChartCard key={chart.column} title={chart.label}>
                      {isRefreshing ? (
                        <Skeleton className="h-full w-full rounded-lg" />
                      ) : (
                        <HorizontalBarChart
                          data={chart.items}
                          activeValue={activeValueFor(chart.column)}
                          onBarClick={(value) => handleCrossFilterClick(chart.column, value)}
                        />
                      )}
                    </ChartCard>
                  ))}
                </div>
              )}

              {(data.time_trend || data.rating_breakdown) && (
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                  {data.time_trend && (
                    <ChartCard title="Trend Over Time" className="xl:col-span-2">
                      {isRefreshing ? <Skeleton className="h-full w-full rounded-lg" /> : <TrendChart trend={data.time_trend} />}
                    </ChartCard>
                  )}
                  {data.rating_breakdown && (
                    <Card>
                      <CardHeader>
                        <CardTitle>{data.rating_breakdown.label} Breakdown</CardTitle>
                      </CardHeader>
                      <CardContent>
                        {isRefreshing ? (
                          <div className="flex flex-col gap-2">
                            {Array.from({ length: 5 }).map((_, i) => (
                              <Skeleton key={i} className="h-4 w-full" />
                            ))}
                          </div>
                        ) : (
                          <RatingBreakdown breakdown={data.rating_breakdown} label={data.rating_breakdown.label} />
                        )}
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
