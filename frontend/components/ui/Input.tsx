import { InputHTMLAttributes, forwardRef, TextareaHTMLAttributes, SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface FieldWrapperProps {
  label?: string;
  error?: string;
  hint?: string;
  required?: boolean;
}

interface InputProps extends InputHTMLAttributes<HTMLInputElement>, FieldWrapperProps {}

function FieldChrome({ label, error, hint, required, htmlFor, children }: FieldWrapperProps & { htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="w-full">
      {label && (
        <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium text-neutral-700 dark:text-neutral-300">
          {label} {required && <span className="text-danger">*</span>}
        </label>
      )}
      {children}
      {error ? (
        <p className="mt-1.5 text-sm text-danger">{error}</p>
      ) : hint ? (
        <p className="mt-1.5 text-sm text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

const baseFieldClasses =
  "w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-sm text-foreground placeholder:text-muted focus-ring disabled:opacity-50 disabled:cursor-not-allowed";

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, label, error, hint, required, id, ...props }, ref) => {
    return (
      <FieldChrome label={label} error={error} hint={hint} required={required} htmlFor={id}>
        <input
          ref={ref}
          id={id}
          className={cn(baseFieldClasses, error && "border-danger", className)}
          {...props}
        />
      </FieldChrome>
    );
  }
);
Input.displayName = "Input";

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement>, FieldWrapperProps {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, label, error, hint, required, id, ...props }, ref) => {
    return (
      <FieldChrome label={label} error={error} hint={hint} required={required} htmlFor={id}>
        <textarea
          ref={ref}
          id={id}
          className={cn(baseFieldClasses, "min-h-[100px] resize-y", error && "border-danger", className)}
          {...props}
        />
      </FieldChrome>
    );
  }
);
Textarea.displayName = "Textarea";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement>, FieldWrapperProps {
  options: { label: string; value: string }[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, label, error, hint, required, id, options, placeholder, ...props }, ref) => {
    return (
      <FieldChrome label={label} error={error} hint={hint} required={required} htmlFor={id}>
        <select
          ref={ref}
          id={id}
          className={cn(baseFieldClasses, "appearance-none bg-no-repeat", error && "border-danger", className)}
          {...props}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </FieldChrome>
    );
  }
);
Select.displayName = "Select";
