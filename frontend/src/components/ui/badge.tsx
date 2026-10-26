import * as React from "react";
import { cn } from "@/lib/utils";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "success" | "warning" | "danger" | "info";
}

const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant = "default", ...props }, ref) => {
    const variants = {
      default: "bg-[var(--surface-subtle)] text-[var(--text-secondary)] border-[var(--border)]",
      success: "bg-[var(--success-bg)] text-[var(--success)] border-[var(--success-border)]",
      warning: "bg-[var(--warning-bg)] text-[var(--warning)] border-[var(--warning-border)]",
      danger: "bg-[var(--danger-bg)] text-[var(--danger)] border-[var(--danger-border)]",
      info: "bg-[var(--info-bg)] text-[var(--info)] border-[var(--info-border)]",
    };

    return (
      <span
        ref={ref}
        className={cn(
          "inline-flex items-center px-2.5 py-1 rounded-[var(--radius-full)] text-xs font-semibold border",
          variants[variant],
          className
        )}
        {...props}
      />
    );
  }
);
Badge.displayName = "Badge";

export { Badge };
