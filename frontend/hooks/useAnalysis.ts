import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { EdaFilterRequest, EdaResult, FilteredEdaResult } from "@/types";

export function useRunEda(datasetId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<EdaResult>(`/api/analysis/${datasetId}/eda`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["eda", datasetId] });
    },
  });
}

export function useFilteredEda(datasetId: string | undefined, filters: EdaFilterRequest) {
  return useQuery({
    queryKey: ["filtered-eda", datasetId, filters],
    queryFn: async () =>
      (await api.post<FilteredEdaResult>(`/api/analysis/${datasetId}/eda/query`, filters)).data,
    enabled: !!datasetId,
    placeholderData: (previous) => previous,
  });
}
