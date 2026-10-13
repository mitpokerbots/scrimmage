import type { HTMLAttributes } from "react";

/** Small pill for people, statuses, counts. */
export function Tag({ className = "", ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={`inline-flex items-center rounded-full border border-line bg-raised px-2 py-0.5 text-xs font-medium text-ink-muted ${className}`}
      {...props}
    />
  );
}
