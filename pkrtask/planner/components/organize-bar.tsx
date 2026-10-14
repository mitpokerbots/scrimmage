"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/button";

export type OrganizeDestination = {
  planId: string;
  label: string;
};

type OrganizeBarProps = {
  selectionCount: number;
  destinations: OrganizeDestination[];
  busy: boolean;
  onMoveTo: (targetPlanId: string) => void;
  onPackage: () => void;
  onClear: () => void;
};

export function OrganizeBar({
  selectionCount,
  destinations,
  busy,
  onMoveTo,
  onPackage,
  onClear,
}: OrganizeBarProps) {
  const [moveOpen, setMoveOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!moveOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setMoveOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [moveOpen]);

  return (
    <div
      className="absolute bottom-12 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 shadow-lg"
      role="toolbar"
      aria-label="Organize selection"
    >
      <span className="text-xs text-ink-muted">
        {selectionCount} selected
      </span>
      <div className="relative" ref={menuRef}>
        <Button
          variant="ghost"
          disabled={busy || destinations.length === 0}
          aria-expanded={moveOpen}
          aria-haspopup="listbox"
          onClick={() => setMoveOpen((open) => !open)}
        >
          Move to…
        </Button>
        {moveOpen ? (
          <div
            className="absolute bottom-full left-0 mb-1 max-h-56 min-w-56 overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-lg"
            role="listbox"
            aria-label="Move destination plans"
          >
            {destinations.length === 0 ? (
              <p className="px-3 py-2 text-xs text-ink-muted">No valid destinations</p>
            ) : (
              destinations.map((dest) => (
                <button
                  key={dest.planId}
                  type="button"
                  role="option"
                  aria-selected={false}
                  className="block w-full truncate px-3 py-1.5 text-left text-sm text-ink hover:bg-raised"
                  onClick={() => {
                    setMoveOpen(false);
                    onMoveTo(dest.planId);
                  }}
                >
                  {dest.label}
                </button>
              ))
            )}
          </div>
        ) : null}
      </div>
      <Button variant="ghost" disabled={busy} onClick={onPackage}>
        Package into sub-plan
      </Button>
      <Button variant="ghost" disabled={busy} onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
