"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Circle, Loader2, RotateCcw, Sparkles, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { AILabel } from "@/components/ui/AILabel";
import { Badge } from "@/components/ui/Badge";
import {
  useConfirmAutoAnalyzeTarget,
  useLatestAutoAnalyzeJob,
  useRetryAiInsights,
  useStartAutoAnalyze,
} from "@/hooks/useAutoAnalyze";
import { Markdown } from "@/components/ai/Markdown";
import { useToast } from "@/lib/toast-context";
import { getErrorMessage } from "@/lib/api";
import { cn, titleCase } from "@/lib/utils";
import { aiReasonLabel } from "@/lib/ai-reasons";
import type { AutoAnalyzeStep, StepStatus } from "@/types";

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "completed") return <CheckCircle2 className="h-5 w-5 text-success shrink-0" />;
  if (status === "warning" || status === "skipped") return <AlertTriangle className="h-5 w-5 text-warning shrink-0" />;
  if (status === "running") return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-primary" />;
  if (status === "failed") return <XCircle className="h-5 w-5 text-danger shrink-0" />;
  return <Circle className="h-5 w-5 text-muted shrink-0" />;
}

export function AutoAnalyzePanel({
  projectId,
  datasetId,
  onCompleted,
}: {
  projectId: string;
  datasetId: string | null;
  onCompleted?: (result: {
    targetColumn?: string;
    bestModelId?: string;
    reportId?: string;
    aiAvailable?: boolean;
    weakModel?: boolean;
  }) => void;
}) {
  const { data: latestJob } = useLatestAutoAnalyzeJob(projectId);
  const startAutoAnalyze = useStartAutoAnalyze(projectId);
  const confirmTarget = useConfirmAutoAnalyzeTarget(projectId);
  const retryAiInsights = useRetryAiInsights(projectId);
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [notifiedJobId, setNotifiedJobId] = useState<string | null>(null);
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);

  // The background job updates project.status server-side as it progresses (draft ->
  // analyzing -> completed/failed) — the project header's own query has no way to know
  // that happened on its own, so re-fetch it every time the job status we're already
  // polling changes.
  useEffect(() => {
    if (latestJob?.status) {
      queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
    }
  }, [latestJob?.status, projectId, queryClient]);

  const isRunningForThisDataset =
    latestJob &&
    (latestJob.status === "pending" || latestJob.status === "running") &&
    latestJob.dataset_id === datasetId;
  const isAwaitingConfirmation = latestJob && latestJob.status === "awaiting_target_confirmation" && latestJob.dataset_id === datasetId;
  const showResultsForThisDataset = latestJob && latestJob.dataset_id === datasetId;

  useEffect(() => {
    if (isAwaitingConfirmation && latestJob && !selectedTarget) {
      setSelectedTarget(latestJob.target_candidates_json[0]?.column ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAwaitingConfirmation, latestJob?.id]);

  useEffect(() => {
    if (latestJob?.status === "completed" && latestJob.id !== notifiedJobId) {
      setNotifiedJobId(latestJob.id);
      onCompleted?.({
        targetColumn: latestJob.result_json?.target_column,
        bestModelId: latestJob.result_json?.best_model_id,
        reportId: latestJob.result_json?.report_id,
        aiAvailable: latestJob.result_json?.ai_available,
        weakModel: latestJob.result_json?.baseline ? !latestJob.result_json.baseline.clearly_beats_baseline : false,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestJob?.status, latestJob?.id]);

  const handleStart = async () => {
    if (!datasetId) return;
    try {
      setSelectedTarget(null);
      await startAutoAnalyze.mutateAsync(datasetId);
      showToast("Auto Analyze started — this runs cleaning, EDA, and target detection first.", "info");
    } catch (error) {
      showToast(getErrorMessage(error, "Could not start Auto Analyze."), "error");
    }
  };

  const handleChangeTarget = async () => {
    if (!datasetId) return;
    try {
      setSelectedTarget(null);
      await startAutoAnalyze.mutateAsync({ datasetId, forceTargetReselection: true });
      showToast("Reopening target suggestions — pick a new target to re-run.", "info");
    } catch (error) {
      showToast(getErrorMessage(error, "Could not restart target selection."), "error");
    }
  };

  const handleConfirmTarget = async () => {
    if (!latestJob || !selectedTarget) return;
    try {
      await confirmTarget.mutateAsync({ jobId: latestJob.id, targetColumn: selectedTarget });
      showToast(`Target confirmed: ${selectedTarget}. Training will start shortly.`, "info");
    } catch (error) {
      showToast(getErrorMessage(error, "Could not confirm the target column."), "error");
    }
  };

  const handleRetryAiInsights = async () => {
    if (!latestJob) return;
    try {
      await retryAiInsights.mutateAsync(latestJob.id);
      showToast("Retrying AI insights...", "info");
    } catch (error) {
      showToast(getErrorMessage(error, "Could not retry AI insights."), "error");
    }
  };

  if (!datasetId) return null;

  return (
    <Card className="border-primary/30 bg-gradient-to-br from-primary/5 to-transparent">
      <CardHeader>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" /> Auto Analyze
            </CardTitle>
            <CardDescription>
              One click: clean, explore, detect the target, train &amp; compare models, generate AI insights, and build a report.
            </CardDescription>
          </div>
          <Button
            onClick={handleStart}
            isLoading={startAutoAnalyze.isPending || !!isRunningForThisDataset}
            disabled={!!isRunningForThisDataset || !!isAwaitingConfirmation}
          >
            {isRunningForThisDataset ? "Running..." : "Run Auto Analyze"}
          </Button>
        </div>
      </CardHeader>

      {showResultsForThisDataset && (
        <CardContent className="flex flex-col gap-4">
          <ol className="flex flex-col gap-2.5">
            {latestJob.steps_json.map((step: AutoAnalyzeStep) => (
              <li key={step.key} className="flex items-start gap-3">
                <StepIcon status={step.status} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p
                      className={cn(
                        "text-sm font-medium",
                        step.status === "completed" && "text-foreground",
                        (step.status === "warning" || step.status === "skipped") && "text-warning",
                        step.status === "running" && "text-primary",
                        step.status === "pending" && "text-muted",
                        step.status === "failed" && "text-danger"
                      )}
                    >
                      {step.label}
                    </p>
                    {step.key === "ai_insights" && step.status === "warning" && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={handleRetryAiInsights}
                        isLoading={retryAiInsights.isPending}
                        className="h-6 px-2 text-xs"
                      >
                        <RotateCcw className="h-3 w-3" /> Retry
                      </Button>
                    )}
                  </div>
                  {step.detail && <p className="text-xs text-muted break-words">{step.detail}</p>}
                </div>
              </li>
            ))}
          </ol>

          {isAwaitingConfirmation && latestJob.target_candidates_json.length > 0 && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-4">
              <p className="mb-3 text-sm font-medium text-foreground">
                Confirm the target column before training starts
              </p>
              <div className="flex flex-col gap-2">
                {latestJob.target_candidates_json.map((candidate) => (
                  <label
                    key={candidate.column}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors",
                      selectedTarget === candidate.column
                        ? "border-primary bg-primary/10"
                        : "border-border hover:border-primary/40"
                    )}
                  >
                    <input
                      type="radio"
                      name="target-candidate"
                      className="mt-1"
                      checked={selectedTarget === candidate.column}
                      onChange={() => setSelectedTarget(candidate.column)}
                    />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-foreground">{candidate.column}</span>
                        <Badge variant="info">{titleCase(candidate.problem_type_hint)}</Badge>
                      </div>
                      <p className="mt-0.5 text-xs text-muted break-words">{candidate.reason}</p>
                    </div>
                  </label>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-2">
                <Button onClick={handleConfirmTarget} isLoading={confirmTarget.isPending} disabled={!selectedTarget}>
                  Confirm &amp; continue
                </Button>
                <p className="text-xs text-muted">Or change the target manually in the Modeling tab.</p>
              </div>
            </div>
          )}

          {latestJob.status === "failed" && (
            <Alert variant="danger" title="Auto Analyze stopped">
              {latestJob.error_message || "An error occurred during automated analysis."}
            </Alert>
          )}

          {latestJob.status === "completed" && latestJob.result_json && (
            <div className="rounded-lg border border-border p-4">
              {latestJob.result_json.target_column ? (
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm flex-1">
                    <div>
                      <p className="text-xs text-muted">Target</p>
                      <p className="font-medium text-foreground">{latestJob.result_json.target_column}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted">Best model</p>
                      <p className="font-medium text-foreground">
                        {latestJob.result_json.best_model_type && titleCase(latestJob.result_json.best_model_type)}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted">Problem type</p>
                      <Badge variant="info">{latestJob.result_json.problem_type}</Badge>
                    </div>
                    <div>
                      <p className="text-xs text-muted">Score</p>
                      <p className="font-medium text-foreground">{latestJob.result_json.best_model_score}</p>
                    </div>
                  </div>
                  <Button size="sm" variant="outline" onClick={handleChangeTarget} isLoading={startAutoAnalyze.isPending}>
                    Change target
                  </Button>
                </div>
              ) : (
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="text-sm flex-1">
                    <p className="text-xs text-muted">Analysis type</p>
                    <p className="font-medium text-foreground">
                      {latestJob.result_json.analysis_type === "clustering" && latestJob.result_json.clusters
                        ? `Unsupervised clustering — ${latestJob.result_json.clusters.k} group(s) found`
                        : "General exploratory analysis — no target column was confidently detected"}
                    </p>
                    {latestJob.result_json.clusters && (
                      <p className="mt-1 text-xs text-muted">
                        Silhouette score: {latestJob.result_json.clusters.silhouette_score} &middot;{" "}
                        {latestJob.result_json.clusters.rows_clustered} row(s) clustered on{" "}
                        {latestJob.result_json.clusters.features_used.length} numeric feature(s)
                      </p>
                    )}
                  </div>
                  <Button size="sm" variant="outline" onClick={handleChangeTarget} isLoading={startAutoAnalyze.isPending}>
                    Pick a target manually
                  </Button>
                </div>
              )}

              {latestJob.result_json.clusters && (
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  {latestJob.result_json.clusters.clusters.map((c) => (
                    <div key={c.cluster} className="rounded-lg border border-border p-3 text-xs">
                      <p className="font-medium text-foreground">
                        Cluster {c.cluster} &mdash; {c.size} rows ({c.pct}%)
                      </p>
                      <ul className="mt-1 space-y-0.5 text-muted">
                        {Object.entries(c.feature_means).map(([feature, value]) => (
                          <li key={feature}>
                            {feature}: <span className="text-foreground">{value}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}

              {latestJob.result_json.is_weak_model && (
                <Alert variant="warning" title="This model is not much better than random guessing" className="mt-4">
                  The selected target may not be predictable from these features.{" "}
                  {latestJob.result_json.weak_model_reason}{" "}
                  <button type="button" onClick={handleChangeTarget} className="underline hover:no-underline">
                    Try a different target
                  </button>
                  .
                </Alert>
              )}

              {(latestJob.result_json.leakage_warnings?.length ?? 0) > 0 && (
                <Alert variant="warning" title="Excluded likely data leakage" className="mt-4">
                  <ul className="list-disc pl-4">
                    {latestJob.result_json.leakage_warnings!.map((w) => (
                      <li key={w.column}>
                        <strong>{w.column}</strong> — {w.reason}
                      </li>
                    ))}
                  </ul>
                </Alert>
              )}

              {(latestJob.result_json.post_outcome_excluded?.length ?? 0) > 0 && (
                <Alert variant="warning" title="Excluded by default — post-outcome-named features" className="mt-4">
                  <ul className="list-disc pl-4">
                    {latestJob.result_json.post_outcome_excluded!.map((w) => (
                      <li key={w.column}>
                        <strong>{w.column}</strong> — {w.reason}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs opacity-90">
                    Re-include a specific feature from the Modeling tab if it&apos;s actually known in advance.
                  </p>
                </Alert>
              )}

              {(latestJob.result_json.derived_date_features?.length ?? 0) > 0 && (
                <p className="mt-3 text-xs text-muted">
                  Extracted date features: {latestJob.result_json.derived_date_features!.join(", ")}
                </p>
              )}

              {latestJob.result_json.ai_insight && (
                <div className="mt-4 border-t border-border pt-3">
                  <div className="mb-1.5">
                    <AILabel
                      text={
                        latestJob.result_json.ai_provider
                          ? `AI-generated insight (${titleCase(latestJob.result_json.ai_provider)})`
                          : "AI-generated insight"
                      }
                    />
                  </div>
                  <Markdown>{latestJob.result_json.ai_insight}</Markdown>
                </div>
              )}
              {!latestJob.result_json.ai_available && (
                <p className="mt-3 text-xs text-muted">
                  AI narrative unavailable ({aiReasonLabel(latestJob.result_json.ai_reason)}) — all analytical results
                  above are still real and complete.
                </p>
              )}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
