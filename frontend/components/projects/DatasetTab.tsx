"use client";

import { Database, Sparkles, Trash2 } from "lucide-react";
import { FileUploader } from "@/components/datasets/FileUploader";
import { useDataset, useDatasets, useDeleteDataset, useUploadDataset, useUploadSampleDataset } from "@/hooks/useDatasets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { StatCard } from "@/components/ui/Card";
import { Tbody, Td, Th, Thead, TableContainer, Tr } from "@/components/ui/Table";
import { Badge } from "@/components/ui/Badge";
import { useToast } from "@/lib/toast-context";
import { getErrorMessage } from "@/lib/api";
import { cn, formatBytes, formatDate } from "@/lib/utils";

export function DatasetTab({
  projectId,
  selectedDatasetId,
  onSelectDataset,
}: {
  projectId: string;
  selectedDatasetId: string | null;
  onSelectDataset: (id: string) => void;
}) {
  const { data: datasets, isLoading, isError, refetch } = useDatasets(projectId);
  const uploadDataset = useUploadDataset(projectId);
  const uploadSample = useUploadSampleDataset(projectId);
  const deleteDataset = useDeleteDataset(projectId);
  const { data: activeDataset } = useDataset(selectedDatasetId ?? undefined);
  const { showToast } = useToast();

  const handleUpload = async (file: File) => {
    try {
      const dataset = await uploadDataset.mutateAsync(file);
      showToast("Dataset uploaded and profiled successfully.", "success");
      onSelectDataset(dataset.id);
    } catch (error) {
      showToast(getErrorMessage(error, "We could not process this file."), "error");
    }
  };

  const handleSample = async () => {
    try {
      const dataset = await uploadSample.mutateAsync();
      showToast("Sample dataset loaded — try Auto Analyze next!", "success");
      onSelectDataset(dataset.id);
    } catch (error) {
      showToast(getErrorMessage(error, "We could not load the sample dataset."), "error");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteDataset.mutateAsync(id);
      showToast("Dataset deleted.", "success");
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Upload a dataset</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <FileUploader
            onUpload={handleUpload}
            isUploading={uploadDataset.isPending}
            error={uploadDataset.isError ? getErrorMessage(uploadDataset.error) : null}
          />
          {(datasets?.length ?? 0) === 0 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">New here?</span>
              <Button variant="ghost" size="sm" onClick={handleSample} isLoading={uploadSample.isPending}>
                <Sparkles className="h-3.5 w-3.5" /> Try with sample dataset
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {isLoading && <LoadingState label="Loading datasets..." />}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {!isLoading && (datasets?.length ?? 0) === 0 && (
        <EmptyState
          icon={<Database className="h-6 w-6" />}
          title="No datasets yet"
          description="Upload a CSV or Excel file above to get started."
        />
      )}

      {!isLoading && (datasets?.length ?? 0) > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Datasets</CardTitle>
          </CardHeader>
          <CardContent>
            <TableContainer>
              <Thead>
                <Tr>
                  <Th>File</Th>
                  <Th>Rows</Th>
                  <Th>Columns</Th>
                  <Th>Missing</Th>
                  <Th>Duplicates</Th>
                  <Th>Status</Th>
                  <Th>Uploaded</Th>
                  <Th></Th>
                </Tr>
              </Thead>
              <Tbody>
                {datasets!.map((d) => (
                  <Tr
                    key={d.id}
                    className={cn("cursor-pointer", selectedDatasetId === d.id && "bg-primary/5")}
                    onClick={() => onSelectDataset(d.id)}
                  >
                    <Td className="font-medium">{d.file_name}</Td>
                    <Td>{d.row_count ?? "—"}</Td>
                    <Td>{d.column_count ?? "—"}</Td>
                    <Td>{d.missing_values ?? "—"}</Td>
                    <Td>{d.duplicate_rows ?? "—"}</Td>
                    <Td>
                      <Badge variant={d.is_cleaned ? "success" : "default"}>{d.is_cleaned ? "Cleaned" : "Raw"}</Badge>
                    </Td>
                    <Td>{formatDate(d.created_at)}</Td>
                    <Td>
                      <button
                        aria-label={`Delete ${d.file_name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(d.id);
                        }}
                        className="rounded-lg p-1.5 text-muted hover:bg-red-50 hover:text-danger focus-ring"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      {activeDataset && (
        <Card>
          <CardHeader>
            <CardTitle>{activeDataset.file_name} — Profile</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard label="Rows" value={activeDataset.row_count ?? 0} />
              <StatCard label="Columns" value={activeDataset.column_count ?? 0} />
              <StatCard label="Missing values" value={activeDataset.missing_values ?? 0} />
              <StatCard label="Duplicate rows" value={activeDataset.duplicate_rows ?? 0} />
            </div>
            <TableContainer>
              <Thead>
                <Tr>
                  <Th>Column</Th>
                  <Th>Type</Th>
                  <Th>Missing</Th>
                  <Th>Unique</Th>
                  <Th></Th>
                </Tr>
              </Thead>
              <Tbody>
                {activeDataset.columns.map((c) => (
                  <Tr key={c.name}>
                    <Td className="font-medium">{c.name}</Td>
                    <Td>
                      <Badge variant="info">{c.semantic_type ?? c.dtype}</Badge>
                    </Td>
                    <Td>{c.missing_count}</Td>
                    <Td>{c.unique_count}</Td>
                    <Td>{c.is_id_like && <Badge variant="default">ID-like · excluded from training</Badge>}</Td>
                  </Tr>
                ))}
              </Tbody>
            </TableContainer>
            <p className="text-xs text-muted">File size: {formatBytes(activeDataset.file_size)}</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
