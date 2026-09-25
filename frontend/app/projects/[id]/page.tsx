"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Database, Sparkles as SparklesIcon, BrainCircuit, Gauge, MessageSquare, FileText, ListChecks } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { useProject } from "@/hooks/useProjects";
import { useDataset } from "@/hooks/useDatasets";
import { useProjectModels } from "@/hooks/useModels";
import { useReports } from "@/hooks/useReports";
import { Tabs } from "@/components/ui/Tabs";
import { StatusBadge } from "@/components/ui/Badge";
import { NextStepButton } from "@/components/ui/NextStepButton";
import { LoadingState, ErrorState } from "@/components/ui/States";
import { DatasetTab } from "@/components/projects/DatasetTab";
import { CleaningTab } from "@/components/projects/CleaningTab";
import { EDATab } from "@/components/projects/EDATab";
import { ModelingTab } from "@/components/projects/ModelingTab";
import { PredictionsTab } from "@/components/projects/PredictionsTab";
import { AITab } from "@/components/projects/AITab";
import { ReportsTab } from "@/components/projects/ReportsTab";
import { AutoAnalyzePanel } from "@/components/projects/AutoAnalyzePanel";
import {
  getCompletedStepsMap,
  getLastTab,
  markStepComplete,
  setLastProject,
  setLastTab,
  type StepCompletionStatus,
  type WorkflowStep,
} from "@/lib/workflow-state";

const tabItems: { key: WorkflowStep; label: string; icon: React.ReactNode }[] = [
  { key: "dataset", label: "Dataset", icon: <Database className="h-4 w-4" /> },
  { key: "cleaning", label: "Data Quality", icon: <ListChecks className="h-4 w-4" /> },
  { key: "eda", label: "EDA", icon: <SparklesIcon className="h-4 w-4" /> },
  { key: "modeling", label: "Modeling", icon: <BrainCircuit className="h-4 w-4" /> },
  { key: "predictions", label: "Predictions", icon: <Gauge className="h-4 w-4" /> },
  { key: "ai", label: "AI Insights", icon: <MessageSquare className="h-4 w-4" /> },
  { key: "reports", label: "Reports", icon: <FileText className="h-4 w-4" /> },
];

export default function ProjectWorkspacePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: project, isLoading, isError, refetch } = useProject(id);

  const [activeTab, setActiveTab] = useState<WorkflowStep>("dataset");
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [completedSteps, setCompletedSteps] = useState<Map<WorkflowStep, StepCompletionStatus>>(new Map());

  const { data: dataset } = useDataset(selectedDatasetId ?? undefined);
  const { data: models } = useProjectModels(id);
  const { data: reports } = useReports(id);

  // Restore where the user left off in this project (per-browser, best-effort).
  useEffect(() => {
    setLastProject(id);
    const savedTab = getLastTab(id);
    if (savedTab) setActiveTab(savedTab as WorkflowStep);
    setCompletedSteps(getCompletedStepsMap(id));
  }, [id]);

  useEffect(() => {
    setLastTab(id, activeTab);
  }, [id, activeTab]);

  const complete = (step: WorkflowStep, status: StepCompletionStatus = "success") => {
    markStepComplete(id, step, status);
    setCompletedSteps((prev) => new Map(prev).set(step, status));
  };

  // Objective completion signals derived from real data (not just "visited").
  useEffect(() => {
    if (selectedDatasetId) complete("dataset");
    if (dataset?.is_cleaned) complete("cleaning");
    if ((models ?? []).some((m) => m.dataset_id === selectedDatasetId)) complete("modeling");
    if ((reports?.length ?? 0) > 0) complete("reports");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDatasetId, dataset?.is_cleaned, models, reports]);

  const currentIndex = tabItems.findIndex((t) => t.key === activeTab);
  const nextTab = tabItems[currentIndex + 1];

  const goNext = () => {
    if (nextTab) setActiveTab(nextTab.key);
  };

  const handleAutoAnalyzeCompleted = (result: {
    targetColumn?: string;
    bestModelId?: string;
    reportId?: string;
    aiAvailable?: boolean;
    weakModel?: boolean;
  }) => {
    complete("cleaning");
    complete("eda");
    complete("modeling", result.weakModel ? "warning" : "success");
    complete("ai", result.aiAvailable === false ? "warning" : "success");
    complete("reports");
    if (result.bestModelId) setSelectedModelId(result.bestModelId);
  };

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <div>
          <Link href="/projects" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground">
            <ArrowLeft className="h-4 w-4" /> Back to projects
          </Link>
        </div>

        {isLoading && <LoadingState label="Loading project..." />}
        {isError && <ErrorState onRetry={() => refetch()} />}

        {project && (
          <>
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-xl sm:text-2xl font-semibold text-foreground">{project.name}</h1>
                  <StatusBadge status={project.status} />
                </div>
                {project.description && <p className="text-sm text-muted mt-1">{project.description}</p>}
              </div>
            </div>

            {selectedDatasetId && (
              <AutoAnalyzePanel projectId={id} datasetId={selectedDatasetId} onCompleted={handleAutoAnalyzeCompleted} />
            )}

            <Tabs items={tabItems} active={activeTab} onChange={(k) => setActiveTab(k as WorkflowStep)} completed={completedSteps} />

            <div className="flex flex-col gap-4">
              {activeTab === "dataset" && (
                <>
                  <DatasetTab projectId={id} selectedDatasetId={selectedDatasetId} onSelectDataset={setSelectedDatasetId} />
                  {selectedDatasetId && <NextStepButton label="Data Quality" onClick={goNext} />}
                </>
              )}
              {activeTab === "cleaning" && (
                <>
                  <CleaningTab datasetId={selectedDatasetId} />
                  {selectedDatasetId && <NextStepButton label="EDA" onClick={goNext} />}
                </>
              )}
              {activeTab === "eda" && (
                <>
                  <EDATab datasetId={selectedDatasetId} onRun={() => complete("eda")} />
                  {selectedDatasetId && <NextStepButton label="Modeling" onClick={goNext} />}
                </>
              )}
              {activeTab === "modeling" && (
                <>
                  <ModelingTab
                    projectId={id}
                    datasetId={selectedDatasetId}
                    onModelTrained={(modelId) => {
                      setSelectedModelId(modelId);
                      complete("modeling");
                    }}
                  />
                  {(models ?? []).some((m) => m.dataset_id === selectedDatasetId) && (
                    <NextStepButton label="Predictions" onClick={goNext} />
                  )}
                </>
              )}
              {activeTab === "predictions" && (
                <>
                  <PredictionsTab
                    projectId={id}
                    selectedModelId={selectedModelId}
                    onSelectModel={(modelId) => {
                      setSelectedModelId(modelId);
                      complete("predictions");
                    }}
                  />
                  <NextStepButton label="AI Insights" onClick={goNext} />
                </>
              )}
              {activeTab === "ai" && (
                <>
                  <AITab
                    projectId={id}
                    datasetId={selectedDatasetId}
                    modelId={selectedModelId}
                    onUsed={(aiAvailable) => complete("ai", aiAvailable ? "success" : "warning")}
                  />
                  <NextStepButton label="Reports" onClick={goNext} />
                </>
              )}
              {activeTab === "reports" && (
                <ReportsTab projectId={id} datasetId={selectedDatasetId} modelId={selectedModelId} onGenerated={() => complete("reports")} />
              )}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
