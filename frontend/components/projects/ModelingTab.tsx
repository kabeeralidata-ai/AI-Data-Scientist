"use client";

import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, Sparkles } from "lucide-react";
import { useDataset } from "@/hooks/useDatasets";
import { useProjectModels, useTrainModels } from "@/hooks/useModels";
import { useGenerateInsight } from "@/hooks/useAI";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Select, Input } from "@/components/ui/Input";
import { Checkbox } from "@/components/ui/Checkbox";
import { Button } from "@/components/ui/Button";
import { Collapsible } from "@/components/ui/Collapsible";
import { Alert } from "@/components/ui/Alert";
import { AIInsightDisplay } from "@/components/ai/AIInsightDisplay";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { TableContainer, Thead, Tr, Th, Tbody, Td } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { FeatureImportanceChart } from "@/components/charts/FeatureImportanceChart";
import { ConfusionMatrixTable } from "@/components/charts/ConfusionMatrixTable";
import { ActualVsPredictedChart } from "@/components/charts/ActualVsPredictedChart";
import { ChartCard } from "@/components/charts/ChartCard";
import { useToast } from "@/lib/toast-context";
import { getErrorMessage } from "@/lib/api";
import { titleCase } from "@/lib/utils";
import { getPreferences, savePreferences } from "@/lib/workflow-state";
import type { MLModel } from "@/types";

const CLASSIFICATION_MODEL_KEYS = [
  "logistic_regression",
  "decision_tree",
  "random_forest",
  "extra_trees",
  "gradient_boosting",
  "xgboost",
  "knn",
  "svm",
];
const REGRESSION_MODEL_KEYS = [
  "linear_regression",
  "ridge_regression",
  "lasso_regression",
  "decision_tree",
  "random_forest",
  "extra_trees",
  "gradient_boosting",
  "xgboost",
  "svm",
];

// titleCase() would render "knn" as "Knn" and "svm" as "Svm" — these acronyms need an
// explicit override; everything else falls through to the normal Title Case rendering.
const MODEL_LABEL_OVERRIDES: Record<string, string> = { knn: "KNN", svm: "SVM" };
function modelLabel(key: string): string {
  return MODEL_LABEL_OVERRIDES[key] ?? titleCase(key);
}

export function ModelingTab({
  projectId,
  datasetId,
  onModelTrained,
}: {
  projectId: string;
  datasetId: string | null;
  onModelTrained: (modelId: string) => void;
}) {
  const { data: dataset } = useDataset(datasetId ?? undefined);
  const { data: models, isLoading: modelsLoading } = useProjectModels(projectId);
  const trainModels = useTrainModels(projectId);
  const generateInsight = useGenerateInsight();
  const { showToast } = useToast();

  const [target, setTarget] = useState("");
  const [testSize, setTestSize] = useState(() => getPreferences().testSize ?? 0.2);
  const [cvFolds, setCvFolds] = useState(() => getPreferences().cvFolds ?? 5);
  const [selectedModelKeys, setSelectedModelKeys] = useState<string[]>([]);
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  const [autoInsightModelId, setAutoInsightModelId] = useState<string | null>(null);
  const [includeSelection, setIncludeSelection] = useState<Set<string>>(new Set());

  useEffect(() => {
    savePreferences({ testSize, cvFolds });
  }, [testSize, cvFolds]);

  // Prefill the target from the backend's suggestion once, without overriding a user's choice.
  useEffect(() => {
    if (dataset?.suggested_target_column && !target) {
      setTarget(dataset.suggested_target_column);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset?.suggested_target_column]);

  const problemTypeGuess = useMemo(() => {
    if (!dataset || !target) return null;
    const col = dataset.columns.find((c) => c.name === target);
    if (!col) return null;
    return col.is_numeric && col.unique_count > 20 ? "regression" : "classification";
  }, [dataset, target]);

  const modelKeys = problemTypeGuess === "regression" ? REGRESSION_MODEL_KEYS : CLASSIFICATION_MODEL_KEYS;
  const idLikeColumns = useMemo(() => dataset?.columns.filter((c) => c.is_id_like) ?? [], [dataset]);

  useEffect(() => {
    const savedModels = (getPreferences().models ?? []).filter((k) => modelKeys.includes(k));
    setSelectedModelKeys(savedModels.length > 0 ? savedModels : modelKeys);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [problemTypeGuess]);

  useEffect(() => {
    if (selectedModelKeys.length > 0) savePreferences({ models: selectedModelKeys });
  }, [selectedModelKeys]);

  if (!datasetId || !dataset) {
    return <EmptyState title="Select a dataset first" description="Choose a dataset from the Dataset tab before training a model." />;
  }

  const handleTrain = async () => {
    if (!target) return;
    try {
      const trained = await trainModels.mutateAsync({
        dataset_id: datasetId,
        target_column: target,
        test_size: testSize,
        cv_folds: cvFolds,
        models: selectedModelKeys.length > 0 ? selectedModelKeys : undefined,
      });
      showToast(`Trained ${trained.length} model(s) successfully.`, "success");
      const best = trained.find((m) => m.is_best) ?? trained[0];
      if (best) {
        onModelTrained(best.id);
        setAutoInsightModelId(best.id);
        generateInsight.mutate({ model_id: best.id });
        const leaked = best.metrics_json?.leakage_warnings ?? [];
        if (leaked.length > 0) {
          showToast(
            `Excluded ${leaked.length} feature(s) as likely data leakage: ${leaked.map((w) => w.column).join(", ")}.`,
            "info"
          );
        }
        const excludedByDefault = best.metrics_json?.post_outcome_excluded ?? [];
        if (excludedByDefault.length > 0) {
          showToast(
            `Excluded ${excludedByDefault.length} feature(s) by default (post-outcome-named): ${excludedByDefault.map((w) => w.column).join(", ")}.`,
            "info"
          );
        }
      }
    } catch (error) {
      showToast(getErrorMessage(error, "We could not train a model on this dataset."), "error");
    }
  };

  const relevantModels = (models ?? []).filter((m) => m.dataset_id === datasetId);

  const handleRetrainIncluding = async (baseModel: MLModel) => {
    if (includeSelection.size === 0) return;
    try {
      const trained = await trainModels.mutateAsync({
        dataset_id: datasetId,
        target_column: baseModel.target_column,
        test_size: testSize,
        cv_folds: cvFolds,
        models: selectedModelKeys.length > 0 ? selectedModelKeys : undefined,
        include_post_outcome_features: Array.from(includeSelection),
      });
      showToast(`Retrained, including ${includeSelection.size} feature(s).`, "success");
      setIncludeSelection(new Set());
      const best = trained.find((m) => m.is_best) ?? trained[0];
      if (best) {
        onModelTrained(best.id);
        setAutoInsightModelId(best.id);
        generateInsight.mutate({ model_id: best.id });
      }
    } catch (error) {
      showToast(getErrorMessage(error, "Could not retrain including the selected feature(s)."), "error");
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Train models</CardTitle>
          <CardDescription>
            Select a target column. We&apos;ll detect whether this is a classification or regression problem and
            train several suitable models for comparison automatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div>
            <Select
              label="Target column"
              required
              placeholder="Select a column to predict"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              options={dataset.columns.filter((c) => !c.is_id_like).map((c) => ({ label: c.name, value: c.name }))}
            />
            {dataset.suggested_target_column && (
              <p className="mt-1.5 text-xs text-muted">
                Suggested target: <strong>{dataset.suggested_target_column}</strong>
                {target !== dataset.suggested_target_column && (
                  <button
                    type="button"
                    onClick={() => setTarget(dataset.suggested_target_column!)}
                    className="ml-1.5 text-primary hover:underline"
                  >
                    Use suggestion
                  </button>
                )}
              </p>
            )}
          </div>

          {problemTypeGuess && (
            <Badge variant="info" className="w-fit">
              Detected problem type: {titleCase(problemTypeGuess)}
            </Badge>
          )}

          {idLikeColumns.length > 0 && (
            <p className="text-xs text-muted">
              Automatically excluding {idLikeColumns.length} ID-like column(s) from training:{" "}
              {idLikeColumns.map((c) => c.name).join(", ")}
            </p>
          )}

          <Collapsible title="Advanced Settings" description="test size, cross-validation, models">
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <Input
                  label="Test set size"
                  type="number"
                  min={0.1}
                  max={0.5}
                  step={0.05}
                  value={testSize}
                  onChange={(e) => setTestSize(parseFloat(e.target.value))}
                  hint="Share of data held out for evaluation (0.1–0.5). Default 0.2."
                />
                <Input
                  label="Cross-validation folds"
                  type="number"
                  min={2}
                  max={10}
                  step={1}
                  value={cvFolds}
                  onChange={(e) => setCvFolds(parseInt(e.target.value, 10))}
                  hint="Number of CV folds. Default 5."
                />
              </div>
              <div>
                <p className="mb-2 text-sm font-medium text-foreground">Models to train</p>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {modelKeys.map((key) => (
                    <Checkbox
                      key={key}
                      id={`model-${key}`}
                      label={modelLabel(key)}
                      checked={selectedModelKeys.includes(key)}
                      onChange={(e) =>
                        setSelectedModelKeys((prev) =>
                          e.target.checked ? [...prev, key] : prev.filter((k) => k !== key)
                        )
                      }
                    />
                  ))}
                </div>
              </div>
            </div>
          </Collapsible>

          <div>
            <Button onClick={handleTrain} isLoading={trainModels.isPending} disabled={!target}>
              <BrainCircuit className="h-4 w-4" /> Train models
            </Button>
          </div>
        </CardContent>
      </Card>

      {modelsLoading && <LoadingState label="Loading models..." />}

      {(generateInsight.isPending || generateInsight.data) && autoInsightModelId && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" /> AI Insight
            </CardTitle>
            <CardDescription>Generated automatically for the best model.</CardDescription>
          </CardHeader>
          <CardContent>
            {generateInsight.isPending && <LoadingState label="Generating insight..." />}
            {generateInsight.data && (
              <AIInsightDisplay
                insight={generateInsight.data.insight}
                aiAvailable={generateInsight.data.ai_available}
                provider={generateInsight.data.provider}
              />
            )}
          </CardContent>
        </Card>
      )}

      {relevantModels.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Model Comparison</CardTitle>
            <CardDescription>Sorted by primary metric. The best model is highlighted.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {(() => {
              const best = relevantModels.find((m) => m.is_best) ?? relevantModels[0];
              return (
                best?.metrics_json?.is_weak && (
                  <Alert variant="warning" title="This model is not much better than random guessing">
                    The selected target may not be predictable from these features. {best.metrics_json.weak_reason}
                  </Alert>
                )
              );
            })()}
            {(() => {
              const leaked = relevantModels[0]?.metrics_json?.leakage_warnings ?? [];
              return (
                leaked.length > 0 && (
                  <Alert variant="warning" title="Excluded likely data leakage">
                    <ul className="list-disc pl-4">
                      {leaked.map((w) => (
                        <li key={w.column}>
                          <strong>{w.column}</strong> — {w.reason}
                        </li>
                      ))}
                    </ul>
                  </Alert>
                )
              );
            })()}
            {(() => {
              const best = relevantModels.find((m) => m.is_best) ?? relevantModels[0];
              const excludedByDefault = best?.metrics_json?.post_outcome_excluded ?? [];
              return (
                excludedByDefault.length > 0 &&
                best && (
                  <Alert variant="warning" title="Excluded by default — post-outcome-named features">
                    <div className="flex flex-col gap-2.5">
                      <ul className="flex flex-col gap-1.5">
                        {excludedByDefault.map((w) => (
                          <li key={w.column}>
                            <Checkbox
                              id={`include-${w.column}`}
                              checked={includeSelection.has(w.column)}
                              onChange={(e) =>
                                setIncludeSelection((prev) => {
                                  const next = new Set(prev);
                                  if (e.target.checked) next.add(w.column);
                                  else next.delete(w.column);
                                  return next;
                                })
                              }
                              label={
                                <span>
                                  <strong>{w.column}</strong> — {w.reason}
                                </span>
                              }
                            />
                          </li>
                        ))}
                      </ul>
                      <div>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleRetrainIncluding(best)}
                          isLoading={trainModels.isPending}
                          disabled={includeSelection.size === 0}
                        >
                          Include selected &amp; retrain
                        </Button>
                      </div>
                    </div>
                  </Alert>
                )
              );
            })()}
            <ModelComparisonTable
              models={relevantModels}
              expandedModel={expandedModel}
              onToggle={(id) => setExpandedModel((prev) => (prev === id ? null : id))}
              onSelectForPrediction={onModelTrained}
            />
          </CardContent>
        </Card>
      )}

      {expandedModel &&
        relevantModels
          .filter((m) => m.id === expandedModel)
          .map((m) => <ModelDetailPanel key={m.id} model={m} />)}
    </div>
  );
}

function ModelComparisonTable({
  models,
  expandedModel,
  onToggle,
  onSelectForPrediction,
}: {
  models: MLModel[];
  expandedModel: string | null;
  onToggle: (id: string) => void;
  onSelectForPrediction: (id: string) => void;
}) {
  const problemType = models[0]?.problem_type;
  const metricKeys =
    problemType === "classification"
      ? ["accuracy", "precision", "recall", "f1", "roc_auc"]
      : ["mae", "rmse", "r2", "mape"];

  return (
    <TableContainer>
      <Thead>
        <Tr>
          <Th>Model</Th>
          <Th>Version</Th>
          {metricKeys.map((k) => (
            <Th key={k}>{k.toUpperCase()}</Th>
          ))}
          <Th>Best</Th>
          <Th></Th>
        </Tr>
      </Thead>
      <Tbody>
        {models.map((m) => (
          <Tr key={m.id} className={m.is_best ? "bg-emerald-50/60 dark:bg-emerald-500/5" : undefined}>
            <Td className="font-medium cursor-pointer" onClick={() => onToggle(m.id)}>
              {titleCase(m.model_type)}
            </Td>
            <Td>v{m.version}</Td>
            {metricKeys.map((k) => (
              <Td key={k}>{formatMetric(m.metrics_json?.[k])}</Td>
            ))}
            <Td>{m.is_best && <Badge variant="success">Best</Badge>}</Td>
            <Td>
              <Button size="sm" variant="outline" onClick={() => onSelectForPrediction(m.id)}>
                Use for prediction
              </Button>
            </Td>
          </Tr>
        ))}
      </Tbody>
    </TableContainer>
  );
}

function formatMetric(value: unknown): string {
  if (typeof value === "number") return value.toFixed(3);
  return "—";
}

function ModelDetailPanel({ model }: { model: MLModel }) {
  const metrics = model.metrics_json;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{modelLabel(model.model_type)} — Details</CardTitle>
        <CardDescription>Target: {model.target_column}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {model.feature_columns_json && model.feature_columns_json.length > 0 && (
          <div>
            <h4 className="mb-2 text-sm font-medium text-foreground">
              Features used ({model.feature_columns_json.length})
            </h4>
            <div className="flex flex-wrap gap-1.5">
              {model.feature_columns_json.map((f) => (
                <Badge key={f} variant="default">
                  {f}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {metrics?.baseline && (
          <p className="text-sm text-muted">
            Baseline ({metrics.baseline.baseline_metric}): <strong className="text-foreground">{metrics.baseline.baseline_score}</strong>{" "}
            vs. model: <strong className="text-foreground">{metrics.baseline.model_score}</strong>{" "}
            {metrics.baseline.clearly_beats_baseline ? (
              <Badge variant="success">Clearly beats baseline</Badge>
            ) : (
              <Badge variant="warning">Barely beats baseline</Badge>
            )}
          </p>
        )}

        {model.feature_importance_json && model.feature_importance_json.length > 0 && (
          <div>
            <h4 className="mb-2 text-sm font-medium text-foreground">Feature Importance</h4>
            <FeatureImportanceChart data={model.feature_importance_json} />
          </div>
        )}

        {metrics?.confusion_matrix && (
          <div>
            <h4 className="mb-2 text-sm font-medium text-foreground">Confusion Matrix</h4>
            <ConfusionMatrixTable labels={metrics.confusion_matrix.labels} matrix={metrics.confusion_matrix.matrix} />
          </div>
        )}

        {metrics?.actual_vs_predicted && (
          <ChartCard title="Actual vs. Predicted" className="border-0 shadow-none p-0">
            <ActualVsPredictedChart data={metrics.actual_vs_predicted} />
          </ChartCard>
        )}

        {typeof metrics?.cv_mean === "number" && (
          <p className="text-sm text-muted">
            Cross-validation mean score: <strong className="text-foreground">{metrics.cv_mean.toFixed(3)}</strong>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
