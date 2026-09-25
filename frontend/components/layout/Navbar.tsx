"use client";

import { useState } from "react";
import Link from "next/link";
import { Menu, LogOut, User as UserIcon, BrainCircuit } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useAIStatus } from "@/hooks/useAI";
import { ThemeToggle } from "./ThemeToggle";
import { Drawer } from "@/components/ui/Drawer";
import { SidebarNav } from "./Sidebar";
import { cn, titleCase } from "@/lib/utils";

export function Navbar() {
  const { user, logout } = useAuth();
  const { data: aiStatus } = useAIStatus();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-surface/90 backdrop-blur px-4 sm:px-6">
        <button
          className="lg:hidden rounded-lg p-2 text-muted hover:bg-neutral-100 dark:hover:bg-neutral-800 focus-ring"
          aria-label="Open menu"
          onClick={() => setDrawerOpen(true)}
        >
          <Menu className="h-5 w-5" />
        </button>

        <div className="flex items-center gap-2 lg:hidden">
          <BrainCircuit className="h-5 w-5 text-primary" />
          <span className="font-semibold text-foreground text-sm">AI Data Scientist</span>
        </div>

        <div className="ml-auto flex items-center gap-3">
          <Link
            href="/settings"
            className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-muted hover:bg-neutral-100 dark:hover:bg-neutral-800 focus-ring"
            title={
              aiStatus
                ? `${aiStatus.provider ? titleCase(aiStatus.provider) : "AI service"} ${aiStatus.available ? "connected" : "unavailable — see Settings"}`
                : "AI service — checking..."
            }
          >
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                aiStatus?.available ? "bg-success" : aiStatus ? "bg-warning" : "bg-neutral-300 dark:bg-neutral-700"
              )}
            />
            <span className="hidden sm:inline">AI</span>
          </Link>
          <ThemeToggle />
          <div className="relative">
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-neutral-100 dark:hover:bg-neutral-800 focus-ring"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-primary">
                <UserIcon className="h-4 w-4" />
              </span>
              <span className="hidden sm:inline text-foreground font-medium">{user?.name ?? "Account"}</span>
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 mt-2 w-48 rounded-lg border border-border bg-surface shadow-lg py-1"
                onMouseLeave={() => setMenuOpen(false)}
              >
                <div className="px-3 py-2 text-xs text-muted truncate">{user?.email}</div>
                <button
                  role="menuitem"
                  onClick={logout}
                  className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger hover:bg-neutral-100 dark:hover:bg-neutral-800"
                >
                  <LogOut className="h-4 w-4" />
                  Log out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Menu">
        <SidebarNav onNavigate={() => setDrawerOpen(false)} />
      </Drawer>
    </>
  );
}
