"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { useCreateProject, useDeleteProject, useProjects } from "@/hooks/useProjects";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { StatusBadge } from "@/components/ui/Badge";
import { useToast } from "@/lib/toast-context";
import { getErrorMessage } from "@/lib/api";
import { formatDate } from "@/lib/utils";

export default function ProjectsPage() {
  const { data: projects, isLoading, isError, refetch } = useProjects();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();
  const { showToast } = useToast();
  const router = useRouter();

  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);

  const handleCreate = async () => {
    if (!name.trim()) return;
    try {
      const project = await createProject.mutateAsync({ name: name.trim(), description: description.trim() || undefined });
      showToast("Project created successfully.", "success");
      setModalOpen(false);
      setName("");
      setDescription("");
      router.push(`/projects/${project.id}`);
    } catch (error) {
      showToast(getErrorMessage(error, "Could not create project."), "error");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteProject.mutateAsync(deleteTarget.id);
      showToast("Project deleted.", "success");
    } catch (error) {
      showToast(getErrorMessage(error, "Could not delete project."), "error");
    } finally {
      setDeleteTarget(null);
    }
  };

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-xl sm:text-2xl font-semibold text-foreground">Projects</h1>
            <p className="text-sm text-muted">Manage your analytics projects.</p>
          </div>
          <Button onClick={() => setModalOpen(true)}>
            <Plus className="h-4 w-4" /> New project
          </Button>
        </div>

        {isLoading && <LoadingState label="Loading projects..." />}
        {isError && <ErrorState onRetry={() => refetch()} />}
        {!isLoading && !isError && (projects?.length ?? 0) === 0 && (
          <EmptyState
            title="No projects yet"
            description="Create your first project to begin analyzing data."
            action={<Button onClick={() => setModalOpen(true)}>Create a project</Button>}
          />
        )}

        {!isLoading && (projects?.length ?? 0) > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {projects!.map((p) => (
              <div key={p.id} className="group relative h-full rounded-xl border border-border bg-surface p-4 shadow-sm transition-shadow hover:shadow-md">
                <Link href={`/projects/${p.id}`} className="block">
                  <div className="flex items-start justify-between gap-2 pr-8">
                    <p className="font-medium text-foreground line-clamp-1">{p.name}</p>
                    <StatusBadge status={p.status} />
                  </div>
                  <p className="mt-1 text-sm text-muted line-clamp-2 min-h-[40px]">{p.description || "No description"}</p>
                  <div className="mt-3 flex items-center gap-4 text-xs text-muted">
                    <span>{p.dataset_count} datasets</span>
                    <span>{p.model_count} models</span>
                    <span>{p.report_count} reports</span>
                  </div>
                  <p className="mt-2 text-xs text-muted">Created {formatDate(p.created_at)}</p>
                </Link>
                <button
                  aria-label={`Delete ${p.name}`}
                  onClick={() => setDeleteTarget({ id: p.id, name: p.name })}
                  className="absolute right-3 top-3 rounded-lg p-1.5 text-muted opacity-0 transition-opacity hover:bg-red-50 hover:text-danger focus-ring group-hover:opacity-100 sm:opacity-0"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Create a new project">
        <div className="flex flex-col gap-4">
          <Input label="Project name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Customer Churn Prediction" />
          <Textarea
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What are you trying to learn from this data?"
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreate} isLoading={createProject.isPending} disabled={!name.trim()}>
              Create project
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="Delete project">
        <p className="text-sm text-muted">
          Are you sure you want to delete <strong className="text-foreground">{deleteTarget?.name}</strong>? This
          will permanently remove its datasets, models, and reports.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={() => setDeleteTarget(null)}>
            Cancel
          </Button>
          <Button variant="danger" onClick={handleDelete} isLoading={deleteProject.isPending}>
            Delete
          </Button>
        </div>
      </Modal>
    </AppShell>
  );
}
