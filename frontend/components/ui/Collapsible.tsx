"use client";

import { useState } from "react";
import { ChevronDown, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Collapsible({
  title,
  description,
  defaultOpen = false,
  icon,
  children,
}: {
  title: string;
  description?: string;
  defaultOpen?: boolean;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left focus-ring min-h-[44px]"
      >
        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
          {icon ?? <Settings2 className="h-4 w-4 text-muted" />}
          {title}
          {description && <span className="hidden sm:inline text-xs font-normal text-muted">— {description}</span>}
        </span>
        <ChevronDown className={cn("h-4 w-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-border p-4">{children}</div>}
    </div>
  );
}
