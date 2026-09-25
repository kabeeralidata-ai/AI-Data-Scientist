import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MLModel, ModelDetail } from "@/types";

export function useProjectModels(projectId: string | undefined) {
  return useQuery({
    queryKey: ["models", projectId],
    queryFn: async () => (await api.get<MLModel[]>(`/api/models/project/${projectId}`)).data,
    enabled: !!projectId,
  });
}

export function useModel(modelId: string | undefined) {
  return useQuery({
    queryKey: ["model", modelId],
    queryFn: async () => (await api.get<ModelDetail>(`/api/models/${modelId}`)).data,
    enabled: !!modelId,
  });
}

export interface TrainPayload {
  dataset_id: string;
  target_column: string;
  feature_columns?: string[];
  test_size?: number;
  cv_folds?: number;
  random_seed?: number;
  models?: string[];
  /** Post-outcome-named features are excluded by default; list a column here to
   * explicitly re-include it despite the suspicion. */
  include_post_outcome_features?: string[];
}

export function useTrainModels(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: TrainPayload) => (await api.post<MLModel[]>("/api/models/train", payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
    },
  });
}
