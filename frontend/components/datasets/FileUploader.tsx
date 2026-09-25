"use client";

import { useCallback, useRef, useState } from "react";
import { UploadCloud, FileSpreadsheet, X } from "lucide-react";
import { cn, formatBytes } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/States";

const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".xls"];

export function FileUploader({
  onUpload,
  isUploading,
  error,
  confirmLabel = "Upload dataset",
  loadingLabel = "Uploading...",
}: {
  onUpload: (file: File) => void;
  isUploading?: boolean;
  error?: string | null;
  confirmLabel?: string;
  loadingLabel?: string;
}) {
  const [dragOver, setDragOver] = useState(false);
  const [selected, setSelected] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const validateAndSet = useCallback((file: File) => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      return;
    }
    setSelected(file);
  }, []);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) validateAndSet(file);
  };

  return (
    <div className="w-full">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        className={cn(
          "flex min-h-[176px] cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-6 text-center transition-colors focus-ring",
          dragOver ? "border-primary bg-primary/5" : "border-border hover:border-neutral-400 dark:hover:border-neutral-600"
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) validateAndSet(file);
          }}
        />
        {selected ? (
          <>
            <FileSpreadsheet className="h-8 w-8 text-primary" />
            <p className="font-medium text-foreground">{selected.name}</p>
            <p className="text-xs text-muted">{formatBytes(selected.size)}</p>
          </>
        ) : (
          <>
            <UploadCloud className="h-8 w-8 text-muted" />
            <p className="font-medium text-foreground">Drag and drop your file here</p>
            <p className="text-sm text-muted">or click to browse — .csv, .xlsx, .xls</p>
          </>
        )}
      </div>

      {error && <p className="mt-2 text-sm text-danger">{error}</p>}

      {selected && (
        <div className="mt-3 flex items-center gap-2">
          <Button
            onClick={() => onUpload(selected)}
            isLoading={isUploading}
            disabled={isUploading}
          >
            {isUploading ? loadingLabel : confirmLabel}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              setSelected(null);
              if (inputRef.current) inputRef.current.value = "";
            }}
            disabled={isUploading}
            aria-label="Remove selected file"
          >
            <X className="h-4 w-4" />
          </Button>
          {isUploading && <Spinner className="h-4 w-4" />}
        </div>
      )}
    </div>
  );
}
