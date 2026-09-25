import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";

describe("EmptyState", () => {
  it("renders title and description", () => {
    render(<EmptyState title="No projects yet" description="Create your first project." />);
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByText("Create your first project.")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("renders a retry button and calls onRetry", () => {
    render(<ErrorState onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("LoadingState", () => {
  it("renders the loading label", () => {
    render(<LoadingState label="Analyzing dataset..." />);
    expect(screen.getByText("Analyzing dataset...")).toBeInTheDocument();
  });
});
