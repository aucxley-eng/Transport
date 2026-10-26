import * as React from "react";
import { Label } from "./label";
import { cn } from "@/lib/utils";

export interface FieldProps {
  label?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: FieldProps) {
  return (
    <div className={cn("flex flex-col gap-2 mb-4", className)}>
      {label && (
        <Label>
          {label}
          {required && <span className="text-[var(--danger)] ml-1">*</span>}
        </Label>
      )}
      {children}
      {hint && !error && (
        <p className="text-xs text-[var(--text-tertiary)]">{hint}</p>
      )}
      {error && (
        <p className="text-xs text-[var(--danger)] animate-[slideDown_150ms_ease-out]">
          {error}
        </p>
      )}
    </div>
  );
}
