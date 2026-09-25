"use client";

import { useState } from "react";
import { Sparkles, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { AILabel } from "@/components/ui/AILabel";
import { useGenerateInsight } from "@/hooks/useAI";
import { getErrorMessage } from "@/lib/api";
import { useToast } from "@/lib/toast-context";

export function ExplainButton({
  datasetId,
  modelId,
  label = "Explain this",
  size = "sm",
}: {
  datasetId?: string;
  modelId?: string;
  label?: string;
  size?: "sm" | "md";
}) {
  const generateInsight = useGenerateInsight();
  const { showToast } = useToast();
  const [open, setOpen] = useState(false);

  const handleClick = async () => {
    setOpen(true);
    if (generateInsight.data) return;
    try {
      await generateInsight.mutateAsync({ dataset_id: datasetId, model_id: modelId });
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    }
  };

  return (
    <div className="inline-block">
      <Button variant="outline" size={size} onClick={handleClick} isLoading={generateInsight.isPending}>
        <Sparkles className="h-3.5 w-3.5" /> {label}
      </Button>
      {open && generateInsight.data && (
        <div className="mt-2 max-w-md rounded-lg border border-border bg-surface p-3 text-sm shadow-sm">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <AILabel />
            {!generateInsight.data.ai_available && (
              <span className="flex items-center gap-1 text-xs text-muted">
                <WifiOff className="h-3 w-3" /> Fallback
              </span>
            )}
          </div>
          <p className="whitespace-pre-wrap text-foreground">{generateInsight.data.insight}</p>
        </div>
      )}
    </div>
  );
}
