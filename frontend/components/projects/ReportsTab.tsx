"use client";

import { useState } from "react";
import { Download, Eye, FileText, Sparkles, WifiOff } from "lucide-react";
import { useGenerateReport, useReportPreview, useReports } from "@/hooks/useReports";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { Badge } from "@/components/ui/Badge";
import { api, getErrorMessage } from "@/lib/api";
import { useToast } from "@/lib/toast-context";
import { formatDate } from "@/lib/utils";
import type { Report } from "@/types";

export function ReportsTab({
  projectId,
  datasetId,
  modelId,
  onGenerated,
}: {
  projectId: string;
  datasetId: string | null;
  modelId: string | null;
  onGenerated?: () => void;
}) {
  const { data: reports, isLoading, isError, refetch } = useReports(projectId);
  const generateReport = useGenerateReport(projectId);
  const { showToast } = useToast();
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [previewReport, setPreviewReport] = useState<Report | null>(null);

  const handleGenerate = async () => {
    try {
      await generateReport.mutateAsync({ dataset_id: datasetId ?? undefined, model_id: modelId ?? undefined });
      showToast("Report generated successfully.", "success");
      onGenerated?.();
    } catch (error) {
      showToast(getErrorMessage(error, "We could not generate a report."), "error");
    }
  };

  const handleDownload = async (reportId: string, title: string) => {
    setDownloadingId(reportId);
    try {
      const response = await api.get(`/api/reports/${reportId}/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([response.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${title}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      showToast(getErrorMessage(error, "We could not download this report."), "error");
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Generate a report</CardTitle>
          <CardDescription>
            A professional, stakeholder-ready analytics report — cover page, data quality before/after,
            modeling approach, model performance, feature drivers, a dataset-specific outlook, and an
            AI-generated business narrative. Preview it as a page or download it as a PDF.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={handleGenerate} isLoading={generateReport.isPending}>
            <FileText className="h-4 w-4" /> Generate report
          </Button>
        </CardContent>
      </Card>

      {isLoading && <LoadingState label="Loading reports..." />}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {!isLoading && (reports?.length ?? 0) === 0 && (
        <EmptyState title="No reports yet" description="Generate your first report above." />
      )}

      {!isLoading && (reports?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-3">
          {reports!.map((r) => (
            <Card key={r.id}>
              <CardContent className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-6">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-foreground truncate">{r.title}</p>
                    {r.content_json?.ai_available ? (
                      <Badge variant="info" className="shrink-0">
                        <Sparkles className="h-3 w-3 mr-1 inline" /> AI insights
                      </Badge>
                    ) : (
                      <Badge variant="default" className="shrink-0">
                        <WifiOff className="h-3 w-3 mr-1 inline" /> AI unavailable
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm text-muted">Generated {formatDate(r.created_at)}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button variant="outline" size="sm" onClick={() => setPreviewReport(r)}>
                    <Eye className="h-4 w-4" /> Preview
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDownload(r.id, r.title)}
                    isLoading={downloadingId === r.id}
                  >
                    <Download className="h-4 w-4" /> Download PDF
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <ReportPreviewModal report={previewReport} onClose={() => setPreviewReport(null)} />
    </div>
  );
}

function ReportPreviewModal({ report, onClose }: { report: Report | null; onClose: () => void }) {
  const { data: html, isLoading, isError } = useReportPreview(report?.id ?? null);

  return (
    <Modal
      open={!!report}
      onClose={onClose}
      title={report?.title ?? "Report preview"}
      className="sm:max-w-4xl w-full"
    >
      <div className="h-[75vh] rounded-lg border border-border overflow-hidden bg-white">
        {isLoading && <LoadingState label="Loading preview..." />}
        {isError && <ErrorState description="We could not load this report's preview." />}
        {html && <iframe title="Report preview" srcDoc={html} className="w-full h-full" sandbox="" />}
      </div>
    </Modal>
  );
}
