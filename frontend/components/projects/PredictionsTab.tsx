"use client";

import { useEffect, useMemo, useState } from "react";
import { Database, Download, Gauge } from "lucide-react";
import { useModel, useProjectModels } from "@/hooks/useModels";
import { useBatchPredict, usePredict } from "@/hooks/usePredictions";
import { useDatasetRows } from "@/hooks/useDatasets";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Select, Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, LoadingState } from "@/components/ui/States";
import { TableContainer, Thead, Tr, Th, Tbody, Td } from "@/components/ui/Table";
import { FileUploader } from "@/components/datasets/FileUploader";
import { getErrorMessage } from "@/lib/api";
import { useToast } from "@/lib/toast-context";
import { titleCase } from "@/lib/utils";

function toCsv(rows: Record<string, unknown>[]): string {
  if (rows.length === 0) return "";
  const columns = Object.keys(rows[0]);
  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.join(",")];
  for (const row of rows) {
    lines.push(columns.map((c) => escape(row[c])).join(","));
  }
  return lines.join("\n");
}

function downloadCsv(filename: string, csv: string) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function PredictionsTab({
  projectId,
  selectedModelId,
  onSelectModel,
}: {
  projectId: string;
  selectedModelId: string | null;
  onSelectModel: (id: string) => void;
}) {
  const { data: models } = useProjectModels(projectId);
  const { data: model, isLoading: modelLoading } = useModel(selectedModelId ?? undefined);
  const predict = usePredict();
  const batchPredict = useBatchPredict();
  const { showToast } = useToast();

  const [values, setValues] = useState<Record<string, string>>({});
  const [loadModalOpen, setLoadModalOpen] = useState(false);
  const [rowSearch, setRowSearch] = useState("");
  const { data: sampleRows, isLoading: rowsLoading } = useDatasetRows(loadModalOpen ? model?.dataset_id : undefined, rowSearch);

  const schema = useMemo(() => model?.feature_schema_json ?? [], [model]);

  useEffect(() => {
    predict.reset();
    batchPredict.reset();
    if (schema.length > 0) {
      const defaults: Record<string, string> = {};
      for (const field of schema) {
        defaults[field.name] = String(field.default ?? "");
      }
      setValues(defaults);
    } else {
      setValues({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModelId, schema.length]);

  if (!models || models.length === 0) {
    return <EmptyState title="No trained models yet" description="Train a model in the Modeling tab before making predictions." />;
  }

  const handlePredict = async () => {
    if (!model) return;
    const features: Record<string, unknown> = {};
    for (const field of schema) {
      const raw = values[field.name];
      features[field.name] = field.type === "numeric" && raw !== undefined && raw !== "" ? Number(raw) : raw;
    }
    try {
      await predict.mutateAsync({ model_id: model.id, features });
    } catch (error) {
      showToast(getErrorMessage(error, "We could not generate a prediction."), "error");
    }
  };

  const handleLoadRow = (rowValues: Record<string, unknown>) => {
    const next: Record<string, string> = { ...values };
    for (const field of schema) {
      if (rowValues[field.name] !== undefined && rowValues[field.name] !== null) {
        next[field.name] = String(rowValues[field.name]);
      }
    }
    setValues(next);
    setLoadModalOpen(false);
    showToast("Row loaded into the prediction form — feel free to adjust values before predicting.", "success");
  };

  const handleBatchPredict = async (file: File) => {
    if (!model) return;
    try {
      await batchPredict.mutateAsync({ modelId: model.id, file });
    } catch (error) {
      showToast(getErrorMessage(error, "We could not process this batch."), "error");
    }
  };

  const handleDownloadBatch = () => {
    if (!batchPredict.data) return;
    downloadCsv(`batch_predictions_${model?.model_type ?? "model"}.csv`, toCsv(batchPredict.data.results));
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Choose a model</CardTitle>
        </CardHeader>
        <CardContent>
          <Select
            label="Trained model"
            value={selectedModelId ?? ""}
            onChange={(e) => onSelectModel(e.target.value)}
            options={models.map((m) => ({
              label: `${titleCase(m.model_type)} v${m.version}${m.is_best ? " (Best)" : ""}`,
              value: m.id,
            }))}
            placeholder="Select a model"
          />
        </CardContent>
      </Card>

      {modelLoading && <LoadingState label="Loading model..." />}

      {model && (
        <Card>
          <CardHeader>
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
              <div>
                <CardTitle>Enter feature values</CardTitle>
                <CardDescription>Target: {model.target_column} — fields are prefilled with typical values.</CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={() => setLoadModalOpen(true)}>
                <Database className="h-4 w-4" /> Load from Dataset
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {schema.map((field) =>
                field.type === "categorical" ? (
                  <Select
                    key={field.name}
                    label={field.name}
                    value={values[field.name] ?? ""}
                    onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                    options={(field.options ?? []).map((opt) => ({ label: opt, value: opt }))}
                  />
                ) : (
                  <Input
                    key={field.name}
                    label={field.name}
                    type="number"
                    value={values[field.name] ?? ""}
                    onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                    hint={field.min !== undefined && field.max !== undefined ? `Typical range: ${field.min} – ${field.max}` : undefined}
                  />
                )
              )}
            </div>
            <div>
              <Button onClick={handlePredict} isLoading={predict.isPending}>
                <Gauge className="h-4 w-4" /> Predict
              </Button>
            </div>

            {predict.data && (
              <Alert variant="info" title="Prediction result">
                <div className="mt-2 space-y-1">
                  {predict.data.output_json.predicted_class !== undefined && (
                    <p>
                      Predicted <strong>{model.target_column}</strong>:{" "}
                      <strong>{predict.data.output_json.predicted_class}</strong>
                      {predict.data.output_json.confidence !== undefined &&
                        ` (confidence ${(predict.data.output_json.confidence * 100).toFixed(1)}%)`}
                    </p>
                  )}
                  {predict.data.output_json.predicted_value !== undefined && (
                    <p>
                      Predicted <strong>{model.target_column}</strong>:{" "}
                      <strong>{predict.data.output_json.predicted_value}</strong>
                    </p>
                  )}
                  <p className="text-xs opacity-75">
                    This is a model output and may contain uncertainty. Use it as one input among others for
                    business decisions.
                  </p>
                </div>
              </Alert>
            )}

            {predict.data?.output_json.contributions && predict.data.output_json.contributions.items.length > 0 && (
              <div className="rounded-lg border border-border p-3">
                <p className="mb-2 text-sm font-medium text-foreground">
                  {predict.data.output_json.contributions.type === "instance_contribution"
                    ? "Why this prediction"
                    : "Most influential features"}
                </p>
                <p className="mb-2 text-xs text-muted">{predict.data.output_json.contributions.note}</p>
                <ul className="flex flex-col gap-1.5">
                  {predict.data.output_json.contributions.items.map((item) => {
                    const value = "contribution" in item ? item.contribution : item.importance;
                    return (
                      <li key={item.name} className="flex items-center justify-between gap-2 text-sm">
                        <span className="text-foreground">{item.name}</span>
                        <span className={value >= 0 ? "text-success" : "text-danger"}>
                          {value >= 0 ? "+" : ""}
                          {value.toFixed(3)}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {model && (
        <Card>
          <CardHeader>
            <CardTitle>Batch Prediction</CardTitle>
            <CardDescription>Upload a CSV with the same feature columns to predict many rows at once.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <FileUploader
              onUpload={handleBatchPredict}
              isUploading={batchPredict.isPending}
              error={batchPredict.isError ? getErrorMessage(batchPredict.error) : null}
              confirmLabel="Run batch prediction"
              loadingLabel="Predicting..."
            />

            {batchPredict.data && (
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted">{batchPredict.data.row_count} row(s) predicted.</p>
                  <Button variant="outline" size="sm" onClick={handleDownloadBatch}>
                    <Download className="h-4 w-4" /> Download results (CSV)
                  </Button>
                </div>
                <TableContainer>
                  <Thead>
                    <Tr>
                      {batchPredict.data.results[0] &&
                        Object.keys(batchPredict.data.results[0]).map((col) => <Th key={col}>{col}</Th>)}
                    </Tr>
                  </Thead>
                  <Tbody>
                    {batchPredict.data.results.slice(0, 50).map((row, i) => (
                      <Tr key={i}>
                        {Object.keys(row).map((col) => (
                          <Td key={col}>{String(row[col] ?? "—")}</Td>
                        ))}
                      </Tr>
                    ))}
                  </Tbody>
                </TableContainer>
                {batchPredict.data.results.length > 50 && (
                  <p className="text-xs text-muted">Showing the first 50 of {batchPredict.data.results.length} rows — download the CSV for the full result set.</p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Modal open={loadModalOpen} onClose={() => setLoadModalOpen(false)} title="Load from dataset">
        <div className="flex flex-col gap-3">
          <Input
            placeholder="Search rows..."
            value={rowSearch}
            onChange={(e) => setRowSearch(e.target.value)}
          />
          {rowsLoading && <LoadingState label="Loading rows..." />}
          {!rowsLoading && (sampleRows?.length ?? 0) === 0 && (
            <p className="py-6 text-center text-sm text-muted">No matching rows found.</p>
          )}
          {!rowsLoading && sampleRows && sampleRows.length > 0 && (
            <div className="max-h-80 overflow-y-auto rounded-lg border border-border divide-y divide-border">
              {sampleRows.map((row) => (
                <button
                  key={row.row_index}
                  onClick={() => handleLoadRow(row.values)}
                  className="block w-full px-3 py-2.5 text-left text-sm hover:bg-neutral-50 dark:hover:bg-neutral-900/40 focus-ring"
                >
                  {schema.slice(0, 4).map((f) => (
                    <span key={f.name} className="mr-3 text-foreground">
                      <span className="text-muted">{f.name}:</span> {String(row.values[f.name] ?? "—")}
                    </span>
                  ))}
                </button>
              ))}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
