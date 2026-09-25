import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Report } from "@/types";

export function useReports(projectId: string | undefined) {
  return useQuery({
    queryKey: ["reports", projectId],
    queryFn: async () => (await api.get<Report[]>(`/api/reports/projects/${projectId}`)).data,
    enabled: !!projectId,
  });
}

export function useGenerateReport(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { dataset_id?: string; model_id?: string; title?: string }) =>
      (await api.post<Report>(`/api/reports/projects/${projectId}/generate`, payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", projectId] }),
  });
}

export function reportDownloadUrl(reportId: string): string {
  return `${api.defaults.baseURL}/api/reports/${reportId}/download`;
}

export function useReportPreview(reportId: string | null) {
  return useQuery({
    queryKey: ["report-preview", reportId],
    queryFn: async () =>
      (await api.get<string>(`/api/reports/${reportId}/preview`, { responseType: "text" })).data,
    enabled: !!reportId,
    staleTime: Infinity,
  });
}
