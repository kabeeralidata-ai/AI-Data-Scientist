import { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function TableContainer({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("w-full overflow-x-auto rounded-lg border border-border", className)}>
      <table className="w-full min-w-max text-sm">{children}</table>
    </div>
  );
}

export function Thead({ children }: { children: React.ReactNode }) {
  return <thead className="bg-neutral-50 dark:bg-neutral-900/60 text-left text-xs font-semibold uppercase tracking-wide text-muted">{children}</thead>;
}

export function Th({ children, className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th className={cn("px-3 sm:px-4 py-2.5 whitespace-nowrap", className)} {...props}>
      {children}
    </th>
  );
}

export function Tbody({ children }: { children: React.ReactNode }) {
  return <tbody className="divide-y divide-border">{children}</tbody>;
}

export function Tr({ children, className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={cn("hover:bg-neutral-50 dark:hover:bg-neutral-900/40", className)} {...props}>
      {children}
    </tr>
  );
}

export function Td({ children, className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn("px-3 sm:px-4 py-2.5 whitespace-nowrap text-foreground", className)} {...props}>
      {children}
    </td>
  );
}
