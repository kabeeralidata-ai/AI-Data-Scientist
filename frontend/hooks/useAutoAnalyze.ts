import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AutoAnalyzeJob } from "@/types";

export function useStartAutoAnalyze(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: string | { datasetId: string; forceTargetReselection?: boolean }) => {
      const { datasetId, forceTargetReselection } =
        typeof payload === "string" ? { datasetId: payload, forceTargetReselection: false } : payload;
      return (
        await api.post<AutoAnalyzeJob>(`/api/projects/${projectId}/auto-analyze`, {
          dataset_id: datasetId,
          force_target_reselection: forceTargetReselection ?? false,
        })
      ).data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auto-analyze-latest", projectId] });
    },
  });
}

// "awaiting_target_confirmation" keeps polling too: once the user confirms a target, the
// background task flips the status asynchronously, and continuing to poll while paused
// avoids a race where the UI gets stuck showing the pre-confirmation state.
const ACTIVE_STATUSES = new Set(["pending", "running", "awaiting_target_confirmation"]);

export function useAutoAnalyzeJob(jobId: string | undefined) {
  return useQuery({
    queryKey: ["auto-analyze-job", jobId],
    queryFn: async () => (await api.get<AutoAnalyzeJob>(`/api/auto-analyze/${jobId}`)).data,
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data && ACTIVE_STATUSES.has(query.state.data.status) ? 1500 : false),
  });
}

export function useLatestAutoAnalyzeJob(projectId: string | undefined) {
  return useQuery({
    queryKey: ["auto-analyze-latest", projectId],
    queryFn: async () => (await api.get<AutoAnalyzeJob | null>(`/api/projects/${projectId}/auto-analyze/latest`)).data,
    enabled: !!projectId,
    refetchInterval: (query) => (query.state.data && ACTIVE_STATUSES.has(query.state.data.status) ? 1500 : false),
  });
}

export function useConfirmAutoAnalyzeTarget(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ jobId, targetColumn }: { jobId: string; targetColumn: string }) =>
      (await api.post<AutoAnalyzeJob>(`/api/auto-analyze/${jobId}/confirm-target`, { target_column: targetColumn })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auto-analyze-latest", projectId] });
    },
  });
}

export function useRetryAiInsights(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => (await api.post<AutoAnalyzeJob>(`/api/auto-analyze/${jobId}/retry-ai-insights`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auto-analyze-latest", projectId] });
    },
  });
}
