"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  createContext,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  readStoredSnap,
  restoreScrollContainers,
  snapFromHistoryState,
  stampCurrentHistory,
  type HistorySnap,
} from "@/lib/history-restore";

export type HistoryDockActions = {
  canUndo: boolean;
  canRedo: boolean;
  undoLabel?: string;
  redoLabel?: string;
  busy?: boolean;
  onUndo: () => void;
  onRedo: () => void;
};

type HistoryDockContextValue = {
  register: (actions: HistoryDockActions | null) => void;
};

const HistoryDockContext = createContext<HistoryDockContextValue | null>(null);

const SCROLL_STAMP_DEBOUNCE_MS = 80;
const RESTORE_GUARD_MS = 800;
/** Re-apply the snap over ~600ms so late layout (streamed content, tall grids) cannot clamp it to 0. */
const RESTORE_RETRY_DELAYS_MS = [50, 150, 300, 600];

function applySnap(snap: HistorySnap) {
  if (window.scrollX !== snap.scrollX || window.scrollY !== snap.scrollY) {
    window.scrollTo(snap.scrollX, snap.scrollY);
  }
  if (snap.containers) {
    restoreScrollContainers(snap.containers);
  }
}

function DockButton({
  title,
  label,
  onClick,
  disabled = false,
}: {
  title: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm transition-colors ${
        disabled
          ? "cursor-not-allowed text-ink-muted opacity-40"
          : "text-ink-muted hover:bg-raised hover:text-ink"
      }`}
    >
      {label}
    </button>
  );
}

function HistoryDockBar({ actions }: { actions: HistoryDockActions | null }) {
  const router = useRouter();

  return (
    <div
      data-pkrtask-history-dock
      className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 rounded-md border border-line bg-surface p-1.5 shadow-lg"
      aria-label="History navigation"
    >
      {actions ? (
        <>
          <DockButton
            title={actions.undoLabel ?? "Undo"}
            label="↶"
            onClick={actions.onUndo}
            disabled={!actions.canUndo || actions.busy}
          />
          <DockButton
            title={actions.redoLabel ?? "Redo"}
            label="↷"
            onClick={actions.onRedo}
            disabled={!actions.canRedo || actions.busy}
          />
        </>
      ) : null}
      <DockButton
        title="Back"
        label="←"
        onClick={() => {
          stampCurrentHistory();
          router.back();
        }}
      />
      <DockButton
        title="Forward"
        label="→"
        onClick={() => {
          stampCurrentHistory();
          router.forward();
        }}
      />
    </div>
  );
}

export function useRegisterHistoryDock(actions: HistoryDockActions | null): void {
  const ctx = useContext(HistoryDockContext);
  if (!ctx) {
    throw new Error("useRegisterHistoryDock must be used within HistoryDockProvider");
  }
  const { register } = ctx;
  const actionsRef = useRef(actions);
  useEffect(() => {
    actionsRef.current = actions;
  });

  const hasActions = actions !== null;
  const canUndo = actions?.canUndo ?? false;
  const canRedo = actions?.canRedo ?? false;
  const undoLabel = actions?.undoLabel;
  const redoLabel = actions?.redoLabel;
  const busy = actions?.busy;

  useEffect(() => {
    if (!hasActions) {
      register(null);
      return;
    }
    register({
      canUndo,
      canRedo,
      undoLabel,
      redoLabel,
      busy,
      onUndo: () => {
        actionsRef.current?.onUndo();
      },
      onRedo: () => {
        actionsRef.current?.onRedo();
      },
    });
  }, [register, hasActions, canUndo, canRedo, undoLabel, redoLabel, busy]);

  useEffect(() => {
    return () => {
      register(null);
    };
  }, [register]);
}

function HistoryDockRestore() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const restorePendingRef = useRef(false);
  const restoreTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stampTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasPopRef = useRef(false);
  const retryTimeoutsRef = useRef<number[]>([]);

  const beginRestoreGuard = useCallback(() => {
    restorePendingRef.current = true;
    if (restoreTimeoutRef.current !== null) {
      clearTimeout(restoreTimeoutRef.current);
    }
    restoreTimeoutRef.current = setTimeout(() => {
      restorePendingRef.current = false;
      restoreTimeoutRef.current = null;
    }, RESTORE_GUARD_MS);
  }, []);

  // We restore scroll ourselves; stop the browser from racing us on back/forward.
  useEffect(() => {
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    return () => {
      window.history.scrollRestoration = previous;
    };
  }, []);

  // Mark back/forward navigations and suppress stamping while the router is
  // still in flight, so a transient scroll-to-top cannot overwrite the snap.
  useEffect(() => {
    const onPopState = () => {
      wasPopRef.current = true;
      beginRestoreGuard();
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [beginRestoreGuard]);

  useLayoutEffect(() => {
    const fromPop = wasPopRef.current;
    wasPopRef.current = false;

    // Next.js replaces history.state with its own object on router
    // navigations, dropping our per-entry snap — on back/forward, fall back
    // to the per-URL sessionStorage mirror.
    const snap = snapFromHistoryState(window.history.state) ?? (fromPop ? readStoredSnap() : null);
    if (!snap) return;

    beginRestoreGuard();
    for (const t of retryTimeoutsRef.current) clearTimeout(t);
    retryTimeoutsRef.current = [];

    requestAnimationFrame(() => {
      requestAnimationFrame(() => applySnap(snap));
    });
    const worthRetrying = snap.scrollX !== 0 || snap.scrollY !== 0 || Boolean(snap.containers);
    if (worthRetrying) {
      for (const delay of RESTORE_RETRY_DELAYS_MS) {
        retryTimeoutsRef.current.push(window.setTimeout(() => applySnap(snap), delay));
      }
    }
  }, [pathname, searchParams, beginRestoreGuard]);

  useEffect(() => {
    return () => {
      for (const t of retryTimeoutsRef.current) clearTimeout(t);
      retryTimeoutsRef.current = [];
    };
  }, []);

  useEffect(() => {
    const scheduleStamp = () => {
      if (restorePendingRef.current) {
        return;
      }
      if (stampTimeoutRef.current !== null) {
        clearTimeout(stampTimeoutRef.current);
      }
      stampTimeoutRef.current = setTimeout(() => {
        stampTimeoutRef.current = null;
        if (!restorePendingRef.current) {
          stampCurrentHistory();
        }
      }, SCROLL_STAMP_DEBOUNCE_MS);
    };

    const handleLinkClick = (event: MouseEvent) => {
      if (restorePendingRef.current) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const anchor = target.closest("a");
      if (!anchor || !anchor.href) {
        return;
      }
      try {
        const url = new URL(anchor.href);
        if (url.origin !== window.location.origin) {
          return;
        }
      } catch {
        return;
      }
      if (stampTimeoutRef.current !== null) {
        clearTimeout(stampTimeoutRef.current);
        stampTimeoutRef.current = null;
      }
      stampCurrentHistory();
    };

    document.addEventListener("scroll", scheduleStamp, { capture: true, passive: true });
    document.addEventListener("click", handleLinkClick, { capture: true });

    return () => {
      document.removeEventListener("scroll", scheduleStamp, { capture: true });
      document.removeEventListener("click", handleLinkClick, { capture: true });
      if (stampTimeoutRef.current !== null) {
        clearTimeout(stampTimeoutRef.current);
        stampTimeoutRef.current = null;
      }
      if (restoreTimeoutRef.current !== null) {
        clearTimeout(restoreTimeoutRef.current);
        restoreTimeoutRef.current = null;
      }
    };
  }, []);

  return null;
}

export function HistoryDockProvider({ children }: { children: ReactNode }) {
  const [actions, setActions] = useState<HistoryDockActions | null>(null);
  const register = useCallback((next: HistoryDockActions | null) => {
    setActions(next);
  }, []);

  return (
    <HistoryDockContext.Provider value={{ register }}>
      {children}
      <Suspense fallback={null}>
        <HistoryDockRestore />
      </Suspense>
      <HistoryDockBar actions={actions} />
    </HistoryDockContext.Provider>
  );
}
