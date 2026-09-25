import { describe, expect, it } from "vitest";
import { cn, formatBytes, formatNumber, formatPercent, titleCase } from "@/lib/utils";

describe("cn", () => {
  it("merges class names and resolves tailwind conflicts", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("text-sm", false && "hidden", "font-bold")).toBe("text-sm font-bold");
  });
});

describe("formatBytes", () => {
  it("formats zero bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("formats kilobytes and megabytes", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("formatPercent", () => {
  it("returns a dash for null or undefined", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
  });

  it("formats a fraction as a percentage", () => {
    expect(formatPercent(0.8234, 1)).toBe("82.3%");
  });
});

describe("formatNumber", () => {
  it("returns a dash for null or undefined", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(undefined)).toBe("—");
  });

  it("adds thousands separators", () => {
    expect(formatNumber(26935)).toBe("26,935");
    expect(formatNumber(0)).toBe("0");
  });

  it("rounds to the given digit count", () => {
    expect(formatNumber(469.0188, 2)).toBe("469.02");
  });
});

describe("titleCase", () => {
  it("converts snake_case to Title Case", () => {
    expect(titleCase("random_forest")).toBe("Random Forest");
    expect(titleCase("xgboost")).toBe("Xgboost");
  });
});
