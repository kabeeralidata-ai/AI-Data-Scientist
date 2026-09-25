"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, FolderKanban, Database, BrainCircuit, FileText, Plus } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { useAuth } from "@/lib/auth-context";
import { useProjects } from "@/hooks/useProjects";
import { StatCard, Card, CardContent } from "@/components/ui/Card";
import { LoadingState, EmptyState, ErrorState } from "@/components/ui/States";
import { StatusBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { formatDate, titleCase } from "@/lib/utils";
import { getCompletedSteps, getLastProject, getLastTab, type WorkflowStep } from "@/lib/workflow-state";

const STEP_ORDER: WorkflowStep[] = ["dataset", "cleaning", "eda", "modeling", "predictions", "ai", "reports"];

function nextStepLabel(completed: WorkflowStep[]): string {
  const next = STEP_ORDER.find((s) => !completed.includes(s));
  return next ? titleCase(next) : "Review your results";
}

export default function DashboardPage() {
  const { user } = useAuth();
  const { data: projects, isLoading, isError, refetch } = useProjects();
  const [continueInfo, setContinueInfo] = useState<{ projectId: string; tab: string; nextLabel: string } | null>(null);

  useEffect(() => {
    const lastProjectId = getLastProject();
    if (!lastProjectId) return;
    const tab = getLastTab(lastProjectId) ?? "dataset";
    const completed = getCompletedSteps(lastProjectId);
    setContinueInfo({ projectId: lastProjectId, tab, nextLabel: nextStepLabel(completed) });
  }, []);

  const continueProject = projects?.find((p) => p.id === continueInfo?.projectId);

  const totals = (projects ?? []).reduce(
    (acc, p) => ({
      datasets: acc.datasets + p.dataset_count,
      models: acc.models + p.model_count,
      reports: acc.reports + p.report_count,
    }),
    { datasets: 0, models: 0, reports: 0 }
  );

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h1 className="text-xl sm:text-2xl font-semibold text-foreground">
              Welcome back, {user?.name?.split(" ")[0] ?? "there"}
            </h1>
            <p className="text-sm text-muted">Here&apos;s what&apos;s happening across your projects.</p>
          </div>
          <Link href="/projects">
            <Button>
              <Plus className="h-4 w-4" /> New project
            </Button>
          </Link>
        </div>

        {continueProject && continueInfo && (
          <Card className="border-primary/30 bg-gradient-to-br from-primary/5 to-transparent">
            <CardContent className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-6">
              <div>
                <p className="text-xs font-medium text-primary">Continue where you left off</p>
                <p className="mt-0.5 font-medium text-foreground">{continueProject.name}</p>
                <p className="text-sm text-muted">Next up: {continueInfo.nextLabel}</p>
              </div>
              <Link href={`/projects/${continueInfo.projectId}`}>
                <Button size="sm">
                  Continue <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            </CardContent>
          </Card>
        )}

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard label="Projects" value={projects?.length ?? 0} icon={<FolderKanban className="h-5 w-5" />} />
          <StatCard label="Datasets" value={totals.datasets} icon={<Database className="h-5 w-5" />} />
          <StatCard label="Models" value={totals.models} icon={<BrainCircuit className="h-5 w-5" />} />
          <StatCard label="Reports" value={totals.reports} icon={<FileText className="h-5 w-5" />} />
        </div>

        <div>
          <h2 className="mb-3 text-lg font-semibold text-foreground">Recent Projects</h2>
          {isLoading && <LoadingState label="Loading projects..." />}
          {isError && <ErrorState onRetry={() => refetch()} />}
          {!isLoading && !isError && (projects?.length ?? 0) === 0 && (
            <EmptyState
              title="No projects yet"
              description="Create your first project to begin analyzing data."
              action={
                <Link href="/projects">
                  <Button size="sm">Create a project</Button>
                </Link>
              }
            />
          )}
          {!isLoading && (projects?.length ?? 0) > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {projects!.slice(0, 6).map((p) => (
                <Link key={p.id} href={`/projects/${p.id}`}>
                  <div className="h-full rounded-xl border border-border bg-surface p-4 shadow-sm transition-shadow hover:shadow-md">
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-medium text-foreground line-clamp-1">{p.name}</p>
                      <StatusBadge status={p.status} />
                    </div>
                    <p className="mt-1 text-sm text-muted line-clamp-2">{p.description || "No description"}</p>
                    <div className="mt-3 flex items-center gap-4 text-xs text-muted">
                      <span>{p.dataset_count} datasets</span>
                      <span>{p.model_count} models</span>
                    </div>
                    <p className="mt-2 text-xs text-muted">Updated {formatDate(p.updated_at)}</p>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
