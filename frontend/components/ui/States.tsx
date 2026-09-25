import { FolderOpen, Loader2, ServerCrash } from "lucide-react";
import { Button } from "./Button";

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return <Loader2 className={`animate-spin text-primary ${className}`} />;
}

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <Spinner className="h-8 w-8" />
      <p className="text-sm text-muted">{label}</p>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border py-16 px-4 text-center">
      <div className="rounded-full bg-neutral-100 p-3 text-muted dark:bg-neutral-800">
        {icon ?? <FolderOpen className="h-6 w-6" />}
      </div>
      <div>
        <p className="font-medium text-foreground">{title}</p>
        {description && <p className="mt-1 text-sm text-muted max-w-sm">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({
  title = "We could not complete this action.",
  description = "Please check your input and try again.",
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-danger/30 bg-danger/5 py-16 px-4 text-center">
      <div className="rounded-full bg-danger/10 p-3 text-danger">
        <ServerCrash className="h-6 w-6" />
      </div>
      <div>
        <p className="font-medium text-foreground">{title}</p>
        <p className="mt-1 text-sm text-muted max-w-sm">{description}</p>
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
