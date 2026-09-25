import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type AlertVariant = "info" | "success" | "warning" | "danger";

const config: Record<AlertVariant, { icon: React.ElementType; classes: string }> = {
  info: { icon: Info, classes: "bg-indigo-50 text-indigo-800 border-indigo-200 dark:bg-indigo-500/10 dark:text-indigo-300 dark:border-indigo-500/30" },
  success: { icon: CheckCircle2, classes: "bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/30" },
  warning: { icon: AlertTriangle, classes: "bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/30" },
  danger: { icon: XCircle, classes: "bg-red-50 text-red-800 border-red-200 dark:bg-red-500/10 dark:text-red-300 dark:border-red-500/30" },
};

export function Alert({
  variant = "info",
  title,
  children,
  className,
}: {
  variant?: AlertVariant;
  title?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const { icon: Icon, classes } = config[variant];
  return (
    <div role="alert" className={cn("flex gap-3 rounded-lg border p-3 sm:p-4 text-sm", classes, className)}>
      <Icon className="h-5 w-5 shrink-0" />
      <div>
        {title && <p className="font-medium">{title}</p>}
        {children && <div className="mt-0.5 text-sm opacity-90">{children}</div>}
      </div>
    </div>
  );
}
