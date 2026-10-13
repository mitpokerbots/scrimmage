export type HistorySnap = {
  scrollX: number;
  scrollY: number;
  ui?: Record<string, unknown>;
  containers?: Record<string, { left: number; top: number }>;
};

const HISTORY_STATE_KEY = "pkrtask";
const SNAP_STORE_PREFIX = "pkrtask:snap:";

function historyStateRecord(state: unknown): Record<string, unknown> {
  if (state !== null && typeof state === "object" && !Array.isArray(state)) {
    return { ...(state as Record<string, unknown>) };
  }
  return {};
}

function snapStoreKey(): string {
  return `${SNAP_STORE_PREFIX}${window.location.pathname}${window.location.search}`;
}

/**
 * Next.js rewrites `history.state` with its own object on router navigations,
 * which can drop the per-entry `pkrtask` snapshot. Every stamp also mirrors the
 * snap per URL in sessionStorage so back/forward can still restore after the
 * entry state was wiped. Last write per URL wins.
 */
export function readStoredSnap(): HistorySnap | null {
  try {
    const raw = window.sessionStorage.getItem(snapStoreKey());
    if (!raw) return null;
    return snapFromHistoryState({ [HISTORY_STATE_KEY]: JSON.parse(raw) as unknown });
  } catch {
    return null;
  }
}

function writeStoredSnap(snap: HistorySnap): void {
  try {
    window.sessionStorage.setItem(snapStoreKey(), JSON.stringify(snap));
  } catch {
    // sessionStorage unavailable (private mode / quota) — the history.state stamp still applies
  }
}

function captureScrollContainers(): Record<string, { left: number; top: number }> | undefined {
  if (typeof document === "undefined") {
    return undefined;
  }
  const nodes = document.querySelectorAll("[data-pkrtask-scroll]");
  const containers: Record<string, { left: number; top: number }> = {};
  for (const node of nodes) {
    const key = node.getAttribute("data-pkrtask-scroll");
    if (!key) {
      continue;
    }
    if (node instanceof HTMLElement) {
      containers[key] = { left: node.scrollLeft, top: node.scrollTop };
    }
  }
  return Object.keys(containers).length > 0 ? containers : undefined;
}

function parseContainers(raw: unknown): Record<string, { left: number; top: number }> | undefined {
  if (raw === undefined) {
    return undefined;
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return undefined;
  }
  const containers: Record<string, { left: number; top: number }> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      continue;
    }
    const { left, top } = value as Record<string, unknown>;
    if (typeof left !== "number" || typeof top !== "number") {
      continue;
    }
    if (!Number.isFinite(left) || !Number.isFinite(top)) {
      continue;
    }
    containers[key] = { left, top };
  }
  return Object.keys(containers).length > 0 ? containers : undefined;
}

export function captureSnap(ui?: Record<string, unknown>): HistorySnap {
  const snap: HistorySnap = {
    scrollX: window.scrollX,
    scrollY: window.scrollY,
  };
  const containers = captureScrollContainers();
  if (containers !== undefined) {
    snap.containers = containers;
  }
  if (ui !== undefined) {
    snap.ui = ui;
  }
  return snap;
}

export function snapFromHistoryState(state: unknown): HistorySnap | null {
  if (state === null || typeof state !== "object" || Array.isArray(state)) {
    return null;
  }
  const raw = (state as Record<string, unknown>)[HISTORY_STATE_KEY];
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const candidate = raw as Record<string, unknown>;
  const { scrollX, scrollY, ui, containers: rawContainers } = candidate;
  if (typeof scrollX !== "number" || typeof scrollY !== "number") {
    return null;
  }
  if (!Number.isFinite(scrollX) || !Number.isFinite(scrollY)) {
    return null;
  }
  const snap: HistorySnap = { scrollX, scrollY };
  if (ui !== undefined) {
    if (ui === null || typeof ui !== "object" || Array.isArray(ui)) {
      return null;
    }
    snap.ui = ui as Record<string, unknown>;
  }
  const containers = parseContainers(rawContainers);
  if (containers !== undefined) {
    snap.containers = containers;
  }
  return snap;
}

export function restoreScrollContainers(
  containers: Record<string, { left: number; top: number }>,
): void {
  if (typeof document === "undefined") {
    return;
  }
  for (const [key, pos] of Object.entries(containers)) {
    const node = document.querySelector(`[data-pkrtask-scroll="${CSS.escape(key)}"]`);
    if (node instanceof HTMLElement) {
      node.scrollLeft = pos.left;
      node.scrollTop = pos.top;
    }
  }
}

export function stampCurrentHistory(ui?: Record<string, unknown>): void {
  const existing = historyStateRecord(window.history.state);
  const existingSnap = snapFromHistoryState(existing);
  // Scroll/click stamps carry no ui of their own. If the router wiped
  // history.state, fall back to the mirrored ui so a background stamp can
  // never erase app state (e.g. an open panel) from the snapshot.
  const mergedUi = ui ?? existingSnap?.ui ?? readStoredSnap()?.ui;
  const snap = captureSnap(mergedUi);
  window.history.replaceState({ ...existing, [HISTORY_STATE_KEY]: snap }, "", window.location.href);
  writeStoredSnap(snap);
}

export function pushAppUrl(url: string, nextUi?: Record<string, unknown>): void {
  stampCurrentHistory();
  const snap: HistorySnap = nextUi
    ? { scrollX: 0, scrollY: 0, ui: nextUi }
    : { scrollX: 0, scrollY: 0 };
  window.history.pushState({ [HISTORY_STATE_KEY]: snap }, "", url);
  writeStoredSnap(snap);
}

export function replaceAppUrl(url: string, ui?: Record<string, unknown>): void {
  const existing = historyStateRecord(window.history.state);
  const snap = captureSnap(ui);
  window.history.replaceState({ ...existing, [HISTORY_STATE_KEY]: snap }, "", url);
  writeStoredSnap(snap);
}
