import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Dataset, DatasetDetail, DatasetRow, QualityIssue } from "@/types";

export function useDatasets(projectId: string | undefined) {
  return useQuery({
    queryKey: ["datasets", projectId],
    queryFn: async () => (await api.get<Dataset[]>(`/api/projects/${projectId}/datasets`)).data,
    enabled: !!projectId,
  });
}

export function useDataset(datasetId: string | undefined) {
  return useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: async () => (await api.get<DatasetDetail>(`/api/datasets/${datasetId}`)).data,
    enabled: !!datasetId,
  });
}

export function useUploadDataset(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return (
        await api.post<Dataset>(`/api/projects/${projectId}/datasets/upload`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useUploadSampleDataset(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<Dataset>(`/api/projects/${projectId}/datasets/sample`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useDeleteDataset(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (datasetId: string) => api.delete(`/api/datasets/${datasetId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets", projectId] }),
  });
}

export function useCleanDataset(datasetId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      missing_strategy: string;
      constant_value?: string;
      remove_duplicates: boolean;
      columns?: string[];
    }) => (await api.post<DatasetDetail>(`/api/analysis/${datasetId}/clean`, payload)).data,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["dataset", datasetId] });
      queryClient.invalidateQueries({ queryKey: ["datasets", data.project_id] });
      queryClient.invalidateQueries({ queryKey: ["dataset-quality", datasetId] });
    },
  });
}

export function useDatasetQuality(datasetId: string | undefined) {
  return useQuery({
    queryKey: ["dataset-quality", datasetId],
    queryFn: async () => (await api.get<QualityIssue[]>(`/api/datasets/${datasetId}/quality`)).data,
    enabled: !!datasetId,
  });
}

export function useDatasetRows(datasetId: string | undefined, search?: string) {
  return useQuery({
    queryKey: ["dataset-rows", datasetId, search],
    queryFn: async () =>
      (
        await api.get<DatasetRow[]>(`/api/datasets/${datasetId}/rows`, {
          params: { limit: 25, search: search || undefined },
        })
      ).data,
    enabled: !!datasetId,
  });
}
