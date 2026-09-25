import Link from "next/link";
import {
  BrainCircuit,
  UploadCloud,
  Sparkles,
  LineChart,
  ShieldCheck,
  MessageSquare,
  FileText,
  ArrowRight,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";

const features = [
  {
    icon: UploadCloud,
    title: "Upload & Profile",
    description: "Drop in a CSV or Excel file and get an instant structural profile — rows, types, missing values, duplicates.",
  },
  {
    icon: LineChart,
    title: "Automated EDA & Charts",
    description: "Descriptive stats, distributions, outliers, and correlations rendered as interactive, responsive charts.",
  },
  {
    icon: BrainCircuit,
    title: "ML Model Comparison",
    description: "Train Logistic/Linear Regression, Random Forest, Gradient Boosting, and XGBoost, then compare metrics side-by-side.",
  },
  {
    icon: Sparkles,
    title: "AI Business Insights",
    description: "Qwen (via Ollama) explains your verified results in plain business language — it never invents numbers.",
  },
  {
    icon: MessageSquare,
    title: "Ask Your Data",
    description: "Chat with an AI analyst grounded in your project's real metrics and feature importance.",
  },
  {
    icon: FileText,
    title: "Automated Reports",
    description: "Generate a polished PDF report combining data quality, modeling results, and AI narrative in one click.",
  },
];

const steps = [
  "Create a project",
  "Upload your dataset",
  "Clean & explore the data",
  "Train and compare models",
  "Get AI-powered insights",
  "Download your report",
];

export default function LandingPage() {
  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2">
            <BrainCircuit className="h-6 w-6 text-primary" />
            <span className="font-semibold">AI Data Scientist</span>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/login">
              <Button variant="ghost" size="sm">Log in</Button>
            </Link>
            <Link href="/register">
              <Button size="sm">Get started</Button>
            </Link>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-24 text-center">
        <h1 className="text-3xl sm:text-5xl font-bold tracking-tight text-foreground">
          Turn Raw Data Into <span className="text-primary">Actionable Business Intelligence</span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-base sm:text-lg text-muted">
          Upload your dataset, analyze it, build ML models, and get AI-powered business
          insights — without writing a line of Python.
        </p>
        <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3">
          <Link href="/register" className="w-full sm:w-auto">
            <Button size="lg" className="w-full sm:w-auto">
              Start Analyzing <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
          <Link href="/login" className="w-full sm:w-auto">
            <Button size="lg" variant="outline" className="w-full sm:w-auto">
              View Demo
            </Button>
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 sm:px-6 pb-16 sm:pb-24">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
          {features.map((f) => (
            <Card key={f.title}>
              <CardContent className="flex flex-col gap-3 pt-6">
                <div className="w-fit rounded-lg bg-primary/10 p-2.5 text-primary">
                  <f.icon className="h-5 w-5" />
                </div>
                <h3 className="font-semibold text-foreground">{f.title}</h3>
                <p className="text-sm text-muted">{f.description}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-t border-border bg-neutral-50 dark:bg-neutral-900/40">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-16 sm:py-24">
          <h2 className="text-center text-2xl sm:text-3xl font-bold text-foreground">How it works</h2>
          <div className="mt-10 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
            {steps.map((step, i) => (
              <div key={step} className="flex flex-col items-center text-center gap-2">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground font-semibold">
                  {i + 1}
                </div>
                <p className="text-sm text-muted">{step}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 sm:px-6 py-16 sm:py-24">
        <Card className="overflow-hidden">
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <ShieldCheck className="h-8 w-8 text-primary" />
            <h2 className="text-xl sm:text-2xl font-semibold text-foreground">
              Facts, predictions, and AI interpretation — clearly separated
            </h2>
            <p className="max-w-xl text-sm sm:text-base text-muted">
              Every number on this platform is calculated deterministically by Python and
              scikit-learn. Qwen only explains what those verified numbers mean for your
              business — every AI-generated block is clearly labeled next to its source data.
            </p>
            <Link href="/register" className="mt-2">
              <Button>Create your first project</Button>
            </Link>
          </CardContent>
        </Card>
      </section>

      <footer className="border-t border-border py-8">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 flex flex-col sm:flex-row items-center justify-between gap-3 text-sm text-muted">
          <span>© {new Date().getFullYear()} AI Data Scientist Platform</span>
          <span>Built with Next.js, FastAPI, PostgreSQL, scikit-learn &amp; Ollama</span>
        </div>
      </footer>
    </div>
  );
}
