"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Send, Sparkles } from "lucide-react";
import { useAIStatus, useCachedInsight, useConversations, useGenerateInsight, useSendChatMessage } from "@/hooks/useAI";
import { useDataset } from "@/hooks/useDatasets";
import { useModel } from "@/hooks/useModels";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { AILabel } from "@/components/ui/AILabel";
import { AIInsightDisplay } from "@/components/ai/AIInsightDisplay";
import { Markdown } from "@/components/ai/Markdown";
import { Spinner } from "@/components/ui/States";
import { cn, titleCase } from "@/lib/utils";
import { getErrorMessage } from "@/lib/api";
import { useToast } from "@/lib/toast-context";

const FALLBACK_QUESTIONS = ["Explain the best model", "What are the most important features?"];

export function AITab({
  projectId,
  datasetId,
  modelId,
  onUsed,
}: {
  projectId: string;
  datasetId: string | null;
  modelId: string | null;
  /** Called after a real AI attempt completes, with whether it actually succeeded — the
   * caller uses this to mark the workflow step "success" (green check) vs "warning"
   * (yellow triangle), so a gracefully-degraded "AI unavailable" response (still a 200
   * from the backend) never renders as a false green check. */
  onUsed?: (aiAvailable: boolean) => void;
}) {
  const { data: status } = useAIStatus();
  const generateInsight = useGenerateInsight();
  const cachedInsight = useCachedInsight(datasetId ?? undefined, modelId ?? undefined);
  const { data: conversations } = useConversations(projectId);
  const sendMessage = useSendChatMessage(projectId);
  const { data: dataset } = useDataset(datasetId ?? undefined);
  const { data: model } = useModel(modelId ?? undefined);
  const { showToast } = useToast();

  // Built from this project's real columns/target/top feature instead of generic
  // placeholders, so every chip is guaranteed to be answerable from real data (and
  // never triggers the "this column doesn't exist" grounding response).
  const suggestedQuestions = useMemo(() => {
    const questions: string[] = [];
    const target = model?.target_column ?? dataset?.suggested_target_column;
    const topFeature = model?.feature_importance_json?.[0]?.name;

    if (model) questions.push("Explain the best model");
    if (topFeature) questions.push(`How does ${topFeature} affect ${target ?? "the outcome"}?`);
    if (target) questions.push(`What drives ${target} the most?`);
    questions.push("What are the most important features?");
    if (dataset?.columns && dataset.columns.length > 0) {
      const otherColumn = dataset.columns.find((c) => c.name !== target && !c.is_id_like)?.name;
      if (otherColumn) questions.push(`What should I investigate about ${otherColumn}?`);
    }

    const unique = Array.from(new Set(questions));
    return unique.length > 0 ? unique.slice(0, 4) : FALLBACK_QUESTIONS;
  }, [dataset, model]);

  const [conversationId, setConversationId] = useState<string | undefined>(undefined);
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; content: string; ai_generated: boolean; provider?: string | null }[]
  >([]);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // A freshly-generated insight (from clicking Generate/Regenerate) must not keep
  // showing once the user switches to a different dataset/model — clear it during
  // render (the React-documented "adjusting state when a prop changes" pattern) so the
  // newly-selected dataset's own cached insight (or empty state) takes over instead.
  const [insightSelection, setInsightSelection] = useState({ datasetId, modelId });
  if (insightSelection.datasetId !== datasetId || insightSelection.modelId !== modelId) {
    setInsightSelection({ datasetId, modelId });
    generateInsight.reset();
  }

  useEffect(() => {
    if (conversations && conversations.length > 0 && !conversationId) {
      const latest = conversations[0];
      setConversationId(latest.id);
      setMessages(latest.messages.map((m) => ({ role: m.role, content: m.content, ai_generated: m.ai_generated })));
    }
  }, [conversations, conversationId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Prefer a just-generated result over the hydrated cache; fall back to the cached
  // insight (loaded silently on mount, no Gemini call) when nothing's been generated
  // in this session yet.
  const displayedInsight = generateInsight.data ?? cachedInsight.data ?? null;
  const hasExistingInsight = !!displayedInsight;

  const handleGenerateInsight = async (force: boolean) => {
    try {
      const result = await generateInsight.mutateAsync({
        dataset_id: datasetId ?? undefined,
        model_id: modelId ?? undefined,
        force,
      });
      onUsed?.(result.ai_available);
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    }
  };

  const sendQuestion = async (question: string) => {
    if (!question.trim()) return;
    setMessages((prev) => [...prev, { role: "user", content: question, ai_generated: false }]);
    setInput("");
    try {
      const res = await sendMessage.mutateAsync({ message: question, conversation_id: conversationId });
      setConversationId(res.conversation_id);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.answer, ai_generated: res.ai_generated, provider: res.provider },
      ]);
      onUsed?.(res.ai_available);
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    }
  };

  const handleSend = () => sendQuestion(input.trim());

  // Only shown when the ACTIVE provider's own health check actually failed — never a
  // hard-coded provider name, and never shown just because a query happens to be stale.
  const providerLabel = status?.provider ? titleCase(status.provider) : "the AI service";

  return (
    <div className="flex flex-col gap-6">
      {status && !status.available && (
        <Alert variant="warning" title="AI service is currently unavailable">
          Your analytical results are still available. AI Insights and Chat will resume once {providerLabel} is reachable.
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>AI Business Insights</CardTitle>
          <CardDescription>
            Generate a business-friendly explanation of your dataset and/or best model, grounded strictly in
            verified numbers.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <Button
              onClick={() => handleGenerateInsight(hasExistingInsight)}
              isLoading={generateInsight.isPending}
              disabled={!datasetId && !modelId}
            >
              <Sparkles className="h-4 w-4" /> {hasExistingInsight ? "Regenerate" : "Generate insight"}
            </Button>
            {cachedInsight.isLoading && (datasetId || modelId) && <Spinner className="h-4 w-4" />}
            {!datasetId && !modelId && (
              <p className="text-xs text-muted">Select a dataset or trained model first.</p>
            )}
          </div>

          {displayedInsight && (
            <AIInsightDisplay
              insight={displayedInsight.insight}
              aiAvailable={displayedInsight.ai_available}
              provider={displayedInsight.provider}
              cached={displayedInsight.cached}
              generatedAt={displayedInsight.generated_at}
            />
          )}
        </CardContent>
      </Card>

      <Card className="flex flex-col">
        <CardHeader>
          <CardTitle>Ask your data</CardTitle>
          <CardDescription>Chat with an AI analyst grounded in this project&apos;s verified results.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div ref={scrollRef} className="flex h-80 flex-col gap-3 overflow-y-auto rounded-lg border border-border p-3 sm:p-4">
            {messages.length === 0 && (
              <p className="m-auto text-sm text-muted">Ask a question like &quot;What are the most important features?&quot;</p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                <div
                  className={cn(
                    "max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm",
                    m.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-neutral-100 text-foreground dark:bg-neutral-800"
                  )}
                >
                  {m.role === "assistant" && m.ai_generated && (
                    <div className="mb-1">
                      <AILabel text={m.provider ? `AI-generated (${titleCase(m.provider)})` : "AI-generated"} />
                    </div>
                  )}
                  {m.role === "assistant" ? (
                    <Markdown>{m.content}</Markdown>
                  ) : (
                    <p className="whitespace-pre-wrap">{m.content}</p>
                  )}
                </div>
              </div>
            ))}
            {sendMessage.isPending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-2 rounded-2xl bg-neutral-100 px-3.5 py-2.5 text-sm dark:bg-neutral-800">
                  <Spinner className="h-4 w-4" /> Thinking...
                </div>
              </div>
            )}
          </div>
          {messages.length === 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestedQuestions.map((q) => (
                <button
                  key={q}
                  onClick={() => sendQuestion(q)}
                  disabled={sendMessage.isPending}
                  className="rounded-full border border-border px-3 py-1.5 text-xs text-foreground hover:border-primary hover:text-primary focus-ring disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder="Ask a question..."
              className="min-h-[44px] flex-1 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm focus-ring"
            />
            <Button onClick={handleSend} isLoading={sendMessage.isPending} disabled={!input.trim()} size="icon" aria-label="Send message">
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
