import * as React from "react";
import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "subtle" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  icon?: React.ReactNode;
  iconPosition?: "left" | "right";
  fullWidth?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = "primary",
      size = "md",
      loading = false,
      icon,
      iconPosition = "left",
      fullWidth = false,
      children,
      disabled,
      ...props
    },
    ref
  ) => {
    const baseStyles =
      "inline-flex items-center justify-center gap-2 font-semibold transition-all focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)] disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none active:scale-[0.98]";

    const variants = {
      primary:
        "bg-[var(--primary)] text-[var(--primary-foreground)] hover:bg-[var(--primary-hover)] hover:shadow-[0_2px_4px_rgba(0,107,96,0.2)] active:bg-[var(--primary-active)]",
      secondary:
        "bg-[var(--surface)] text-[var(--text-primary)] border border-[var(--border)] hover:bg-[var(--surface-hover)] hover:border-[var(--border-strong)]",
      ghost:
        "bg-transparent text-[var(--text-primary)] hover:bg-[var(--surface-hover)]",
      subtle:
        "bg-[var(--surface-subtle)] text-[var(--text-primary)] border border-[var(--border)] hover:bg-[var(--surface-hover)]",
      danger:
        "bg-[var(--danger)] text-[var(--text-inverse)] hover:bg-[#B91C1C] active:bg-[#991B1B]",
    };

    const sizes = {
      sm: "h-9 px-3 text-sm rounded-[var(--radius-sm)]",
      md: "h-12 px-5 text-sm rounded-[var(--radius)]",
      lg: "h-14 px-6 text-base rounded-[var(--radius)]",
    };

    return (
      <button
        className={cn(
          baseStyles,
          variants[variant],
          sizes[size],
          fullWidth && "w-full",
          className
        )}
        ref={ref}
        disabled={disabled || loading}
        {...props}
      >
        {loading && <Loader2 className="h-4 w-4 animate-spin" />}
        {!loading && icon && iconPosition === "left" && icon}
        {children}
        {!loading && icon && iconPosition === "right" && icon}
      </button>
    );
  }
);
Button.displayName = "Button";

export { Button };
