"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Copy, Sparkles } from "lucide-react";
import { useCleanDataset, useDataset, useDatasetQuality } from "@/hooks/useDatasets";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Select, Input } from "@/components/ui/Input";
import { Checkbox } from "@/components/ui/Checkbox";
import { Button } from "@/components/ui/Button";
import { Collapsible } from "@/components/ui/Collapsible";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { useToast } from "@/lib/toast-context";
import { getErrorMessage } from "@/lib/api";
import type { QualityIssue } from "@/types";

const strategyOptions = [
  { label: "Auto (median for numbers, most common for categories)", value: "auto" },
  { label: "Fill with mean (numeric columns)", value: "mean" },
  { label: "Fill with median (numeric columns)", value: "median" },
  { label: "Fill with most frequent value (mode)", value: "mode" },
  { label: "Fill with a constant value", value: "constant" },
  { label: "Drop rows with missing values", value: "drop" },
];

const severityVariant = { low: "default", medium: "warning", high: "danger" } as const;

export function CleaningTab({ datasetId }: { datasetId: string | null }) {
  const { data: dataset } = useDataset(datasetId ?? undefined);
  const { data: issues, isLoading: issuesLoading, isError: issuesError, refetch } = useDatasetQuality(datasetId ?? undefined);
  const cleanDataset = useCleanDataset(datasetId ?? "");
  const { showToast } = useToast();

  const [strategy, setStrategy] = useState("auto");
  const [constantValue, setConstantValue] = useState("");
  const [removeDuplicates, setRemoveDuplicates] = useState(true);
  const [enabledFixes, setEnabledFixes] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (issues) {
      const next: Record<string, boolean> = {};
      for (const issue of issues) {
        if (issue.recommended_fix.action !== "review_only") next[issue.id] = true;
      }
      setEnabledFixes(next);
    }
  }, [issues]);

  if (!datasetId || !dataset) {
    return (
      <EmptyState
        title="Select a dataset first"
        description="Go to the Dataset tab and upload or select a dataset to clean."
      />
    );
  }

  const actionableIssues = (issues ?? []).filter((i) => i.recommended_fix.action !== "review_only");
  const reviewIssues = (issues ?? []).filter((i) => i.recommended_fix.action === "review_only");
  const allEnabled = actionableIssues.length > 0 && actionableIssues.every((i) => enabledFixes[i.id]);

  const toggleAll = (checked: boolean) => {
    const next: Record<string, boolean> = {};
    for (const issue of actionableIssues) next[issue.id] = checked;
    setEnabledFixes(next);
  };

  const handleApplyRecommended = async () => {
    const removeDupesEnabled = actionableIssues.some((i) => i.id === "duplicates" && enabledFixes[i.id]);
    const columnsToFix = actionableIssues
      .filter((i) => i.type === "missing_values" && enabledFixes[i.id] && i.column)
      .map((i) => i.column as string);

    try {
      await cleanDataset.mutateAsync({
        missing_strategy: "auto",
        remove_duplicates: removeDupesEnabled,
        columns: columnsToFix.length > 0 ? columnsToFix : undefined,
      });
      showToast("Recommended fixes applied.", "success");
    } catch (error) {
      showToast(getErrorMessage(error, "We could not clean this dataset."), "error");
    }
  };

  const handleManualClean = async () => {
    try {
      await cleanDataset.mutateAsync({
        missing_strategy: strategy,
        constant_value: strategy === "constant" ? constantValue : undefined,
        remove_duplicates: removeDuplicates,
      });
      showToast("Dataset cleaned successfully.", "success");
    } catch (error) {
      showToast(getErrorMessage(error, "We could not clean this dataset."), "error");
    }
  };

  const log = dataset.cleaning_log_json as
    | {
        rows_before: number;
        rows_after: number;
        duplicates_removed: number;
        missing_cells_before: number;
        missing_cells_after: number;
      }
    | null;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <div>
              <CardTitle>Data quality</CardTitle>
              <CardDescription>
                Real, computed issues in {dataset.file_name}. Cleaning always starts fresh from the original
                upload — the original file is never modified.
              </CardDescription>
            </div>
            {dataset.is_cleaned && <Badge variant="success">Cleaned · v{dataset.cleaning_version}</Badge>}
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {issuesLoading && <LoadingState label="Analyzing data quality..." />}
          {issuesError && <ErrorState onRetry={() => refetch()} />}

          {!issuesLoading && !issuesError && (issues?.length ?? 0) === 0 && (
            <Alert variant="success" title="No data quality issues detected">
              This dataset has no missing values or duplicate rows.
            </Alert>
          )}

          {!issuesLoading && actionableIssues.length > 0 && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Checkbox
                  id="toggle-all-fixes"
                  label={<span className="font-medium">Select all recommended fixes</span>}
                  checked={allEnabled}
                  onChange={(e) => toggleAll(e.target.checked)}
                />
              </div>
              <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
                {actionableIssues.map((issue) => (
                  <IssueRow
                    key={issue.id}
                    issue={issue}
                    checked={!!enabledFixes[issue.id]}
                    onChange={(checked) => setEnabledFixes((prev) => ({ ...prev, [issue.id]: checked }))}
                  />
                ))}
              </div>
            </div>
          )}

          {reviewIssues.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <p className="text-sm font-medium text-foreground">For your review (not auto-fixed)</p>
              {reviewIssues.map((issue) => (
                <div key={issue.id} className="flex items-start gap-2 text-sm text-muted">
                  <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                  <span>{issue.message}</span>
                </div>
              ))}
            </div>
          )}

          {actionableIssues.length > 0 && (
            <div>
              <Button onClick={handleApplyRecommended} isLoading={cleanDataset.isPending}>
                <Sparkles className="h-4 w-4" /> Apply Selected Fixes
              </Button>
            </div>
          )}

          <Collapsible title="Manual cleaning options" description="choose one strategy for all columns">
            <div className="flex flex-col gap-4">
              <Select
                label="Missing value strategy"
                options={strategyOptions}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
              />
              {strategy === "constant" && (
                <Input
                  label="Constant value"
                  value={constantValue}
                  onChange={(e) => setConstantValue(e.target.value)}
                  placeholder="e.g. 0 or Unknown"
                />
              )}
              <Checkbox
                id="manual-remove-duplicates"
                label="Remove duplicate rows"
                checked={removeDuplicates}
                onChange={(e) => setRemoveDuplicates(e.target.checked)}
              />
              <div>
                <Button variant="outline" onClick={handleManualClean} isLoading={cleanDataset.isPending}>
                  <Copy className="h-4 w-4" /> Clean with this strategy
                </Button>
              </div>
            </div>
          </Collapsible>
        </CardContent>
      </Card>

      {dataset.is_cleaned && log && (
        <Alert variant="success" title="Dataset cleaned">
          Rows: {log.rows_before} → {log.rows_after} · Duplicates removed: {log.duplicates_removed} · Missing
          cells: {log.missing_cells_before} → {log.missing_cells_after}
        </Alert>
      )}
    </div>
  );
}

function IssueRow({
  issue,
  checked,
  onChange,
}: {
  issue: QualityIssue;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3 p-3">
      <Checkbox
        id={`issue-${issue.id}`}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        label={
          <span>
            <span className="text-foreground">{issue.message}</span>
            {issue.recommended_fix.reason && (
              <span className="block text-xs text-muted">{issue.recommended_fix.reason}</span>
            )}
          </span>
        }
      />
      <Badge variant={severityVariant[issue.severity]} className="shrink-0">
        {issue.severity}
      </Badge>
    </div>
  );
}
