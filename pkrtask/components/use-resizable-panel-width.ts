"use client";

import { useCallback, useState, type PointerEvent as ReactPointerEvent } from "react";

const DEFAULT_WIDTH = 420;
const MIN_WIDTH = 320;
const MAX_WIDTH = 720;

function readStored(key: string, fallback: number): number {
  if (typeof window === "undefined") return fallback;
  const raw = window.localStorage.getItem(key);
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, n));
}

/**
 * Persisted panel width with drag-resize support (left edge of a right-side panel).
 */
export function useResizablePanelWidth(storageKey: string, defaultWidth = DEFAULT_WIDTH) {
  const [width, setWidth] = useState(() => readStored(storageKey, defaultWidth));

  const onResizePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width;
      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);

      const onMove = (e: PointerEvent) => {
        // Dragging the left edge: moving left grows the panel.
        const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + (startX - e.clientX)));
        setWidth(next);
      };
      const onUp = (e: PointerEvent) => {
        try {
          target.releasePointerCapture(e.pointerId);
        } catch {
          // Capture already released (unmount / pointercancel).
        }
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        setWidth((w) => {
          window.localStorage.setItem(storageKey, String(w));
          return w;
        });
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [storageKey, width],
  );

  return { width, onResizePointerDown, minWidth: MIN_WIDTH, maxWidth: MAX_WIDTH };
}
