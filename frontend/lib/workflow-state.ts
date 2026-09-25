"use client";

// Lightweight, per-viewer localStorage helpers for workflow continuity: which tab a
// project was last on, which steps have been completed in this browser, and a few
// remembered training preferences. None of this is authoritative — it's UX-only and
// always falls back gracefully if storage is unavailable (private browsing, etc.).

const LAST_PROJECT_KEY = "ai_ds_last_project";
const LAST_TAB_PREFIX = "ai_ds_last_tab:";
const STEPS_PREFIX = "ai_ds_steps:";
const PREFS_KEY = "ai_ds_preferences";

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore — private browsing / storage disabled
  }
}

export function setLastProject(projectId: string): void {
  safeSet(LAST_PROJECT_KEY, projectId);
}

export function getLastProject(): string | null {
  return safeGet(LAST_PROJECT_KEY);
}

export function setLastTab(projectId: string, tab: string): void {
  safeSet(`${LAST_TAB_PREFIX}${projectId}`, tab);
}

export function getLastTab(projectId: string): string | null {
  return safeGet(`${LAST_TAB_PREFIX}${projectId}`);
}

export type WorkflowStep = "dataset" | "cleaning" | "eda" | "modeling" | "predictions" | "ai" | "reports";
export type StepCompletionStatus = "success" | "warning";

export function markStepComplete(projectId: string, step: WorkflowStep, status: StepCompletionStatus = "success"): void {
  const key = `${STEPS_PREFIX}${projectId}`;
  const current = getCompletedStepsMap(projectId);
  current.set(step, status);
  safeSet(key, JSON.stringify(Array.from(current.entries())));
}

/** @deprecated kept for backward-compat reads of older localStorage entries (a plain array
 * of step keys, before per-step status was tracked). */
export function getCompletedSteps(projectId: string): WorkflowStep[] {
  return Array.from(getCompletedStepsMap(projectId).keys());
}

export function getCompletedStepsMap(projectId: string): Map<WorkflowStep, StepCompletionStatus> {
  const raw = safeGet(`${STEPS_PREFIX}${projectId}`);
  if (!raw) return new Map();
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.every((e) => typeof e === "string")) {
      // Legacy shape: a plain array of step keys, all implicitly "success".
      return new Map((parsed as WorkflowStep[]).map((k) => [k, "success" as StepCompletionStatus]));
    }
    return new Map(parsed as [WorkflowStep, StepCompletionStatus][]);
  } catch {
    return new Map();
  }
}

export interface TrainingPreferences {
  testSize?: number;
  cvFolds?: number;
  models?: string[];
  theme?: string;
}

export function getPreferences(): TrainingPreferences {
  const raw = safeGet(PREFS_KEY);
  if (!raw) return {};
  try {
    return JSON.parse(raw) as TrainingPreferences;
  } catch {
    return {};
  }
}

export function savePreferences(update: Partial<TrainingPreferences>): void {
  const next = { ...getPreferences(), ...update };
  safeSet(PREFS_KEY, JSON.stringify(next));
}
