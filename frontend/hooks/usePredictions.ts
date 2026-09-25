import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BatchPredictionResult, Prediction } from "@/types";

export function usePredict() {
  return useMutation({
    mutationFn: async (payload: { model_id: string; features: Record<string, unknown> }) =>
      (await api.post<Prediction>("/api/predictions", payload)).data,
  });
}

export function useBatchPredict() {
  return useMutation({
    mutationFn: async ({ modelId, file }: { modelId: string; file: File }) => {
      const formData = new FormData();
      formData.append("model_id", modelId);
      formData.append("file", file);
      return (
        await api.post<BatchPredictionResult>("/api/predictions/batch", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
  });
}
