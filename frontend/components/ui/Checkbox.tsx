import { InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: React.ReactNode;
}

export function Checkbox({ label, className, id, ...props }: CheckboxProps) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm text-foreground cursor-pointer min-h-[44px] sm:min-h-0">
      <input
        type="checkbox"
        id={id}
        className={cn("h-4 w-4 shrink-0 rounded border-border accent-[var(--color-primary)] focus-ring", className)}
        {...props}
      />
      {label}
    </label>
  );
}
