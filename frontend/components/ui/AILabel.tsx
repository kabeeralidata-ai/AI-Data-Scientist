import { Sparkles } from "lucide-react";

export function AILabel({ text = "AI-generated insight" }: { text?: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-indigo-500/10 to-purple-500/10 px-2.5 py-1 text-xs font-medium text-indigo-600 dark:text-indigo-400">
      <Sparkles className="h-3.5 w-3.5" />
      {text}
    </span>
  );
}
