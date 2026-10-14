"use client";

import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";

/** Canvas-wide preferences read by nodes without rebuilding the graph. */
export const CanvasPrefsContext = createContext({ animateFocus: true });

const POP_DURATION_MS = 900;
const POP_MIN_DELAY_MS = 5000;
const POP_MAX_DELAY_MS = 15000;

function subscribeReducedMotion(onStoreChange: () => void): () => void {
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  mq.addEventListener("change", onStoreChange);
  return () => mq.removeEventListener("change", onStoreChange);
}

function getReducedMotionSnapshot(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function randomPopDelay(): number {
  return POP_MIN_DELAY_MS + Math.random() * (POP_MAX_DELAY_MS - POP_MIN_DELAY_MS);
}

export function FocusTargetIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
      className="h-3 w-3"
    >
      <circle cx="8" cy="8" r="5.5" />
      <circle cx="8" cy="8" r="1.75" fill="currentColor" stroke="none" />
      <path d="M8 1v2M8 13v2M1 8h2M13 8h2" strokeLinecap="round" />
    </svg>
  );
}

type FocusTargetButtonProps = {
  focused: boolean;
  onToggle: () => void;
  labelAdd: string;
  labelRemove: string;
};

export function FocusTargetButton({ focused, onToggle, labelAdd, labelRemove }: FocusTargetButtonProps) {
  return (
    <button
      type="button"
      className={`nodrag nopan absolute right-0 top-0 z-10 flex h-6 w-6 -translate-y-1/2 translate-x-1/2 items-center justify-center rounded-full border border-line bg-surface text-ink-muted shadow-sm transition-opacity hover:border-highlight hover:bg-raised hover:text-highlight ${
        focused
          ? "pointer-events-auto opacity-100"
          : "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100"
      }`}
      aria-label={focused ? labelRemove : labelAdd}
      aria-pressed={focused}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <FocusTargetIcon />
    </button>
  );
}

type FocusPopOptions = {
  /** Raise the React Flow node while popping. Off for large sub-plan frames so they don't cover nested tasks. */
  elevateNode?: boolean;
  /** `scale` is the 1.6× task pop; `glow` pulses in place (sub-plan frames). */
  motion?: "scale" | "glow";
};

export function useFocusPop(
  focused: boolean,
  options: FocusPopOptions = {},
): {
  isPopping: boolean;
  rootRef: RefObject<HTMLDivElement | null>;
  cardAnimationClass: string;
} {
  const elevateNode = options.elevateNode ?? true;
  const motion = options.motion ?? "scale";
  const animateFocus = useContext(CanvasPrefsContext).animateFocus;
  const rootRef = useRef<HTMLDivElement>(null);
  const [popping, setPopping] = useState(false);
  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    getReducedMotionSnapshot,
    () => false,
  );

  useEffect(() => {
    if (!focused || !animateFocus) return;

    let waitTimer: ReturnType<typeof setTimeout> | undefined;
    let popTimer: ReturnType<typeof setTimeout> | undefined;

    const scheduleNext = () => {
      waitTimer = setTimeout(() => {
        setPopping(true);
        popTimer = setTimeout(() => {
          setPopping(false);
          scheduleNext();
        }, POP_DURATION_MS);
      }, randomPopDelay());
    };

    scheduleNext();

    return () => {
      if (waitTimer !== undefined) clearTimeout(waitTimer);
      if (popTimer !== undefined) clearTimeout(popTimer);
      setPopping(false);
    };
  }, [animateFocus, focused]);

  const isPopping = focused && popping;

  useEffect(() => {
    if (!isPopping || !elevateNode) return;
    const flowNode = rootRef.current?.closest(".react-flow__node") as HTMLElement | null;
    if (!flowNode) return;
    const prevZ = flowNode.style.zIndex;
    flowNode.style.zIndex = "10000";
    return () => {
      flowNode.style.zIndex = prevZ;
    };
  }, [isPopping, elevateNode]);

  const cardAnimationClass = isPopping
    ? reducedMotion || motion === "glow"
      ? "focus-point-glow-pulse-active"
      : "focus-point-pop-active"
    : "";

  return { isPopping, rootRef, cardAnimationClass };
}
