/** Maps an AI failure `reason` (see backend GeminiUnavailableError/AIUnavailableError
 * classification) to a short, human-readable label — shared by every UI surface that
 * shows AI status (Settings, Auto Analyze, AITab) so the same failure always reads the
 * same way, and a rate limit or a missing model is never collapsed into a generic
 * "unreachable". */
export const AI_REASON_LABELS: Record<string, string> = {
  ok: "Reachable — model responded successfully",
  unreachable: "Server unreachable",
  timeout: "Connection timed out",
  server_error: "Server returned an error",
  model_not_found: "Configured model not found (check GEMINI_MODEL)",
  not_configured: "Not configured — no API key set",
  invalid_key: "Invalid or unauthorized API key",
  rate_limited: "Rate limit or daily quota reached",
  blocked: "Request blocked by the provider",
  empty_response: "Provider returned an empty response",
  error: "Unexpected error",
};

export function aiReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "unavailable";
  return AI_REASON_LABELS[reason] ?? reason;
}
