import { beforeEach, describe, expect, it } from "vitest";
import {
  getCompletedSteps,
  getCompletedStepsMap,
  getLastProject,
  getLastTab,
  getPreferences,
  markStepComplete,
  savePreferences,
  setLastProject,
  setLastTab,
} from "@/lib/workflow-state";

beforeEach(() => {
  localStorage.clear();
});

describe("last project / tab", () => {
  it("returns null when nothing has been recorded yet", () => {
    expect(getLastProject()).toBeNull();
    expect(getLastTab("proj-1")).toBeNull();
  });

  it("remembers the last project and the last tab per project", () => {
    setLastProject("proj-1");
    setLastTab("proj-1", "modeling");
    setLastTab("proj-2", "eda");

    expect(getLastProject()).toBe("proj-1");
    expect(getLastTab("proj-1")).toBe("modeling");
    expect(getLastTab("proj-2")).toBe("eda");
  });
});

describe("completed steps", () => {
  it("starts empty for a project with no recorded progress", () => {
    expect(getCompletedSteps("proj-1")).toEqual([]);
  });

  it("accumulates distinct steps as they're marked complete", () => {
    markStepComplete("proj-1", "dataset");
    markStepComplete("proj-1", "modeling");

    expect(getCompletedSteps("proj-1")).toEqual(["dataset", "modeling"]);
  });

  it("does not add the same step twice", () => {
    markStepComplete("proj-1", "dataset");
    markStepComplete("proj-1", "dataset");

    expect(getCompletedSteps("proj-1")).toEqual(["dataset"]);
  });

  it("keeps progress isolated per project", () => {
    markStepComplete("proj-1", "dataset");
    markStepComplete("proj-2", "eda");

    expect(getCompletedSteps("proj-1")).toEqual(["dataset"]);
    expect(getCompletedSteps("proj-2")).toEqual(["eda"]);
  });

  it("degrades to an empty list instead of throwing on corrupted storage", () => {
    localStorage.setItem("ai_ds_steps:proj-1", "{not valid json");
    expect(getCompletedSteps("proj-1")).toEqual([]);
  });
});

describe("per-step completion status", () => {
  it("defaults a marked step to 'success'", () => {
    markStepComplete("proj-1", "dataset");
    expect(getCompletedStepsMap("proj-1").get("dataset")).toBe("success");
  });

  it("records an explicit 'warning' status (e.g. AI insights degraded)", () => {
    markStepComplete("proj-1", "ai", "warning");
    expect(getCompletedStepsMap("proj-1").get("ai")).toBe("warning");
  });

  it("overwrites a step's status when marked again with a different outcome", () => {
    markStepComplete("proj-1", "modeling", "warning");
    markStepComplete("proj-1", "modeling", "success");
    expect(getCompletedStepsMap("proj-1").get("modeling")).toBe("success");
  });

  it("migrates the legacy plain-array storage shape to all-success statuses", () => {
    localStorage.setItem("ai_ds_steps:proj-1", JSON.stringify(["dataset", "eda"]));
    const map = getCompletedStepsMap("proj-1");
    expect(map.get("dataset")).toBe("success");
    expect(map.get("eda")).toBe("success");
  });
});

describe("training preferences", () => {
  it("returns an empty object when nothing has been saved", () => {
    expect(getPreferences()).toEqual({});
  });

  it("merges partial updates onto existing preferences", () => {
    savePreferences({ testSize: 0.2, cvFolds: 5 });
    savePreferences({ models: ["random_forest", "xgboost"] });

    expect(getPreferences()).toEqual({
      testSize: 0.2,
      cvFolds: 5,
      models: ["random_forest", "xgboost"],
    });
  });

  it("lets a later save overwrite an earlier field", () => {
    savePreferences({ theme: "light" });
    savePreferences({ theme: "dark" });

    expect(getPreferences().theme).toBe("dark");
  });

  it("degrades to an empty object instead of throwing on corrupted storage", () => {
    localStorage.setItem("ai_ds_preferences", "{not valid json");
    expect(getPreferences()).toEqual({});
  });
});
