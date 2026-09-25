import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AIInsightDisplay } from "@/components/ai/AIInsightDisplay";

describe("AIInsightDisplay", () => {
  it("shows the AI-generated badge when the insight actually came from Ollama", () => {
    render(<AIInsightDisplay insight="Sales grew 12% quarter over quarter." aiAvailable={true} />);
    expect(screen.getByText(/AI-generated insight/i)).toBeInTheDocument();
    expect(screen.getByText("Sales grew 12% quarter over quarter.")).toBeInTheDocument();
  });

  it("never shows the AI-generated badge for the degraded fallback message", () => {
    render(
      <AIInsightDisplay
        insight="AI service is currently unavailable. Your analytical results are still available above."
        aiAvailable={false}
      />
    );
    expect(screen.queryByText(/AI-generated insight/i)).not.toBeInTheDocument();
  });

  it("renders the fallback message as a warning alert, not the AI insight card", () => {
    render(<AIInsightDisplay insight="AI service is currently unavailable." aiAvailable={false} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("AI service unavailable")).toBeInTheDocument();
  });

  it("labels the badge with the actual provider, not a hard-coded name", () => {
    render(<AIInsightDisplay insight="Revenue grew." aiAvailable={true} provider="gemini" />);
    expect(screen.getByText(/AI-generated insight \(Gemini\)/i)).toBeInTheDocument();
  });

  it("renders Markdown formatting instead of showing raw ### and ** symbols", () => {
    render(
      <AIInsightDisplay
        insight={"### Key Findings\n\n**Revenue** grew by 12%.\n\n- Point one\n- Point two"}
        aiAvailable={true}
        provider="gemini"
      />
    );
    expect(screen.getByRole("heading", { name: "Key Findings" })).toBeInTheDocument();
    expect(screen.getByText("Revenue").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByText(/###/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
  });
});
