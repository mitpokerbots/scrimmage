"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_PREFIX = "pkrtask:focus-points:";

function storageKey(planId: string): string {
  return `${STORAGE_PREFIX}${planId}`;
}

function readStoredIds(planId: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(storageKey(planId));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

export function useFocusPoints(planId: string): {
  focusedIds: Set<string>;
  toggleFocus: (nodeId: string) => void;
} {
  const [focusedIds, setFocusedIds] = useState<Set<string>>(() => new Set());
  const skipPersistRef = useRef(true);

  useEffect(() => {
    skipPersistRef.current = true;
    // Hydrate from localStorage after mount; SSR renders an empty Set to avoid mismatch.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional post-mount hydration
    setFocusedIds(new Set(readStoredIds(planId)));
  }, [planId]);

  useEffect(() => {
    if (skipPersistRef.current) {
      skipPersistRef.current = false;
      return;
    }
    window.localStorage.setItem(storageKey(planId), JSON.stringify([...focusedIds]));
  }, [focusedIds, planId]);

  const toggleFocus = useCallback((nodeId: string) => {
    setFocusedIds((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }, []);

  return { focusedIds, toggleFocus };
}
