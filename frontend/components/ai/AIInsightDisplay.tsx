import { CheckCircle2 } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { AILabel } from "@/components/ui/AILabel";
import { Markdown } from "@/components/ai/Markdown";
import { titleCase } from "@/lib/utils";

/**
 * Renders a generated insight, gated strictly on whether it actually came from an AI
 * provider. The "AI-generated insight" badge must never appear on the degraded fallback
 * message — that message is a deterministic string from the backend, not an AI output,
 * so it's rendered as a plain warning alert with no AI badge at all.
 */
export function AIInsightDisplay({
  insight,
  aiAvailable,
  provider,
  cached,
  generatedAt,
}: {
  insight: string;
  aiAvailable: boolean;
  provider?: string | null;
  /** True when this insight was loaded from cache rather than just freshly generated —
   * shown as a distinct "Cached" badge so the user knows no new Gemini call was made. */
  cached?: boolean;
  generatedAt?: string | null;
}) {
  if (!aiAvailable) {
    return (
      <Alert variant="warning" title="AI service unavailable">
        {insight}
      </Alert>
    );
  }
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        {cached ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400">
            <CheckCircle2 className="h-3 w-3" /> Cached
          </span>
        ) : (
          <AILabel text={provider ? `AI-generated insight (${titleCase(provider)})` : "AI-generated insight"} />
        )}
        {generatedAt && (
          <span className="text-xs text-muted">Generated {new Date(generatedAt).toLocaleString()}</span>
        )}
      </div>
      <Markdown>{insight}</Markdown>
    </div>
  );
}
