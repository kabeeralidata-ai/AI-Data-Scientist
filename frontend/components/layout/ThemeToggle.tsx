"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun, Monitor } from "lucide-react";
import { cn } from "@/lib/utils";

const options = [
  { value: "light", icon: Sun, label: "Light mode" },
  { value: "system", icon: Monitor, label: "System theme" },
  { value: "dark", icon: Moon, label: "Dark mode" },
] as const;

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) return <div className="h-9 w-28" />;

  return (
    <div className="flex items-center rounded-lg border border-border p-0.5">
      {options.map((opt) => {
        const Icon = opt.icon;
        const active = theme === opt.value;
        return (
          <button
            key={opt.value}
            aria-label={opt.label}
            onClick={() => setTheme(opt.value)}
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-md transition-colors focus-ring",
              active ? "bg-primary text-primary-foreground" : "text-muted hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
          </button>
        );
      })}
    </div>
  );
}
