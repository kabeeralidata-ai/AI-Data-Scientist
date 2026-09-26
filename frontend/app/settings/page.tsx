"use client";

import { RefreshCw } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { useAuth } from "@/lib/auth-context";
import { useAIStatus, useTestAIConnection } from "@/hooks/useAI";
import { useHealth } from "@/hooks/useHealth";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { formatDate, titleCase } from "@/lib/utils";
import { AI_REASON_LABELS } from "@/lib/ai-reasons";
import type { AIStatus } from "@/types";

function ProviderStatusBlock({ status, heading }: { status: AIStatus; heading: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-foreground">{heading}</p>
        <Badge variant={status.available ? "success" : "warning"}>{status.available ? "Connected" : "Unavailable"}</Badge>
        {status.reason && <span className="text-xs text-muted">{AI_REASON_LABELS[status.reason] ?? status.reason}</span>}
        {status.cached && <span className="text-xs text-muted">(cached — click Test connection for a live check)</span>}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-xs text-muted">URL</p>
          <p className="font-mono text-foreground break-all">{status.url ?? "—"}</p>
        </div>
        <div>
          <p className="text-xs text-muted">Model</p>
          <p className="font-mono text-foreground break-all">{status.model ?? "—"}</p>
        </div>
      </div>
      {status.detail && <p className="text-sm text-muted">{status.detail}</p>}
      {status.installed_models && status.installed_models.length > 0 && (
        <p className="text-xs text-muted">Installed models: {status.installed_models.join(", ")}</p>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { user, logout } = useAuth();
  const { data: passiveStatus } = useAIStatus();
  const testConnection = useTestAIConnection();
  const { data: health } = useHealth();
  // A just-run explicit test always wins over the passive/cached status underneath it.
  const aiStatus = testConnection.data ?? passiveStatus;

  return (
    <AppShell>
      <div className="flex flex-col gap-6 max-w-2xl">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold text-foreground">Settings</h1>
          <p className="text-sm text-muted">Manage your account.</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
            <CardDescription>Member since {user ? formatDate(user.created_at) : "—"}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Input label="Full name" value={user?.name ?? ""} disabled />
            <Input label="Email" value={user?.email ?? ""} disabled />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AI Status</CardTitle>
            <CardDescription>
              {aiStatus?.provider === "gemini"
                ? "Gemini is the primary AI provider; Ollama is used automatically as a fallback."
                : "Diagnose the connection to the Ollama AI service used for insights and chat."}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {aiStatus && (
              <ProviderStatusBlock
                status={aiStatus}
                heading={aiStatus.provider ? `${titleCase(aiStatus.provider)} (primary)` : "AI provider"}
              />
            )}
            {aiStatus?.fallback && (
              <ProviderStatusBlock status={aiStatus.fallback} heading={`${titleCase(aiStatus.fallback.provider ?? "Ollama")} (fallback)`} />
            )}
            <div>
              <Button variant="outline" size="sm" onClick={() => testConnection.mutate()} isLoading={testConnection.isPending}>
                <RefreshCw className="h-3.5 w-3.5" /> Test connection
              </Button>
              <p className="mt-1.5 text-xs text-muted">
                Performs a real, live request to Gemini (not just a config check).
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>System</CardTitle>
            <CardDescription>What the backend server is currently running.</CardDescription>
          </CardHeader>
          <CardContent>
            <div>
              <p className="text-xs text-muted">Backend commit</p>
              <p className="font-mono text-sm text-foreground break-all">
                {health?.git_commit ? health.git_commit.slice(0, 12) : "unavailable"}
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Session</CardTitle>
          </CardHeader>
          <CardContent>
            <Button variant="danger" onClick={logout}>
              Log out
            </Button>
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
