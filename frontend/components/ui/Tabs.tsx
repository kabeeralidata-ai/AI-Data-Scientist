"use client";

import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

export type TabCompletionStatus = "success" | "warning";

export interface TabItem {
  key: string;
  label: string;
  icon?: React.ReactNode;
  disabled?: boolean;
}

function CompletionIcon({ status }: { status: TabCompletionStatus }) {
  if (status === "warning") return <AlertTriangle className="h-3.5 w-3.5 text-warning" />;
  return <CheckCircle2 className="h-3.5 w-3.5 text-success" />;
}

export function Tabs({
  items,
  active,
  onChange,
  completed,
}: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  /** Either a plain Set of completed tab keys (all shown as success), or a Map giving each
   * completed tab a specific status (success = green check, warning = yellow triangle —
   * e.g. an AI Insights tab whose last run degraded gracefully). */
  completed?: Set<string> | Map<string, TabCompletionStatus>;
}) {
  const statusFor = (key: string): TabCompletionStatus | undefined => {
    if (!completed) return undefined;
    if (completed instanceof Map) return completed.get(key);
    return completed.has(key) ? "success" : undefined;
  };

  return (
    <div className="border-b border-border overflow-x-auto">
      <nav className="flex gap-1 min-w-max px-1" role="tablist">
        {items.map((item) => {
          const status = statusFor(item.key);
          return (
            <button
              key={item.key}
              role="tab"
              aria-selected={active === item.key}
              disabled={item.disabled}
              onClick={() => onChange(item.key)}
              className={cn(
                "flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors focus-ring disabled:opacity-40 disabled:pointer-events-none",
                active === item.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted hover:text-foreground hover:border-neutral-300 dark:hover:border-neutral-700"
              )}
            >
              {item.icon}
              {item.label}
              {status && <CompletionIcon status={status} />}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
