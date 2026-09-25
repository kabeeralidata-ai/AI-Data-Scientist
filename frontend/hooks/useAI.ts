import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AIConversation, AIStatus, InsightResponse } from "@/types";

interface ChatMessageResponse {
  conversation_id: string;
  answer: string;
  ai_available: boolean;
  ai_generated: boolean;
  provider?: string;
}

/** Passive/background status polling (navbar dot, AITab banner) — the backend caches
 * the underlying Gemini check for a few minutes, so this refetch interval does NOT mean
 * a real Gemini request every 60s; see gemini_service.check_connection. Use
 * useTestAIConnection for an explicit, always-live "Test Connection" check. */
export function useAIStatus() {
  return useQuery({
    queryKey: ["ai-status"],
    queryFn: async () => (await api.get<AIStatus>("/api/ai/status")).data,
    refetchInterval: 60_000,
  });
}

/** An explicit, always-live connection test (Settings "Test connection" button) —
 * force=true bypasses the server-side cache and always spends a real Gemini request. */
export function useTestAIConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.get<AIStatus>("/api/ai/status", { params: { force: true } })).data,
    onSuccess: (data) => queryClient.setQueryData(["ai-status"], data),
  });
}

export function useGenerateInsight() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { model_id?: string; dataset_id?: string; force?: boolean }) =>
      (await api.post<InsightResponse>("/api/ai/insights", payload)).data,
    onSuccess: (data, variables) =>
      queryClient.setQueryData(["cached-insight", variables.dataset_id ?? null, variables.model_id ?? null], data),
  });
}

/** Silently hydrates a previously-generated insight on mount/reload — a pure DB read
 * (no Gemini call), so it's safe to fire automatically. 404 (no cache yet) is treated
 * as "nothing generated", not an error. */
export function useCachedInsight(datasetId: string | undefined, modelId: string | undefined) {
  return useQuery({
    queryKey: ["cached-insight", datasetId ?? null, modelId ?? null],
    queryFn: async () => {
      try {
        return (
          await api.get<InsightResponse>("/api/ai/insights/cache", {
            params: { dataset_id: datasetId, model_id: modelId },
          })
        ).data;
      } catch (error: unknown) {
        if (typeof error === "object" && error && "response" in error) {
          const status = (error as { response?: { status?: number } }).response?.status;
          if (status === 404) return null;
        }
        throw error;
      }
    },
    enabled: !!datasetId || !!modelId,
    staleTime: Infinity,
    retry: false,
  });
}

export function useConversations(projectId: string | undefined) {
  return useQuery({
    queryKey: ["conversations", projectId],
    queryFn: async () => (await api.get<AIConversation[]>(`/api/ai/conversations/${projectId}`)).data,
    enabled: !!projectId,
  });
}

export function useSendChatMessage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { message: string; conversation_id?: string }) =>
      (
        await api.post<ChatMessageResponse>("/api/ai/chat", {
          project_id: projectId,
          ...payload,
        })
      ).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations", projectId] }),
  });
}
