import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "ghost" | "danger";

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-brand hover:bg-brand-deep text-ink border border-brand-deep",
  ghost:
    "bg-transparent hover:bg-raised text-ink-muted hover:text-ink border border-line",
  danger:
    "bg-transparent hover:bg-status-blocked/10 text-status-blocked border border-status-blocked/40",
};

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant };

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  return (
    <button
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${variantClasses[variant]} ${className}`}
      {...props}
    />
  );
}
