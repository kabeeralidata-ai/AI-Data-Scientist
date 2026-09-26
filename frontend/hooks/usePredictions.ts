import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BatchPredictionResult, Prediction, ReturnPredictionsResult } from "@/types";

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

/** Per-customer return predictions for a transaction-log project — this analysis type
 * never trains an MLModel row, so it has no model_id to key off; the backend reads it
 * from the latest completed Auto Analyze job for the project instead. */
export function useReturnPredictions(projectId: string | undefined) {
  return useQuery({
    queryKey: ["return-predictions", projectId],
    queryFn: async () => (await api.get<ReturnPredictionsResult>(`/api/predictions/return-predictions/${projectId}`)).data,
    enabled: !!projectId,
  });
}

const RETURN_PREDICTIONS_MEDIA_TYPE: Record<"csv" | "xlsx", string> = {
  csv: "text/csv",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

export async function downloadReturnPredictions(projectId: string, format: "csv" | "xlsx") {
  const response = await api.get(`/api/predictions/return-predictions/${projectId}/download`, {
    params: { format },
    responseType: "blob",
  });
  const url = window.URL.createObjectURL(new Blob([response.data], { type: RETURN_PREDICTIONS_MEDIA_TYPE[format] }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `return_predictions.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
