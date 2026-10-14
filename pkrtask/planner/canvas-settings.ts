/**
 * How one person moves around the canvas. Stored in this browser, keyed by
 * kerberos, so two people on one computer do not share a trackpad setup.
 * A different computer starts from the defaults: sensitivity is per device.
 */

export const SCROLL_ACTIONS = ["pan", "zoom"] as const;
export type ScrollAction = (typeof SCROLL_ACTIONS)[number];

export const SCROLL_AXES = ["free", "horizontal", "vertical"] as const;
export type ScrollAxis = (typeof SCROLL_AXES)[number];

export const DRAG_EMPTY = ["pan", "select"] as const;
export type DragEmpty = (typeof DRAG_EMPTY)[number];

export const SELECTION_BOXES = ["full", "partial"] as const;
export type SelectionBox = (typeof SELECTION_BOXES)[number];

export const SELECT_MODIFIERS = ["shift", "meta"] as const;
export type SelectModifier = (typeof SELECT_MODIFIERS)[number];

export const GRID_SIZES = [10, 20, 40] as const;
export type GridSize = (typeof GRID_SIZES)[number];

export type CanvasSettings = {
  /** What the scroll wheel or trackpad scroll does. Pinch still zooms. */
  scrollAction: ScrollAction;
  /** How far one scroll tick moves the board. Ignored while scroll zooms. */
  scrollSensitivity: number;
  /** Reverse the scroll direction. */
  invertScroll: boolean;
  scrollAxis: ScrollAxis;
  /** Dragging empty space pans the board, or draws a selection box. */
  dragEmpty: DragEmpty;
  /** Add to the selection with Shift, or with Command (Control on other systems). */
  selectModifier: SelectModifier;
  /** A box selects only fully enclosed cards, or any card it touches. */
  selectionBox: SelectionBox;
  /** Pan the board when a drag reaches the edge. */
  autoPan: boolean;
  autoPanSpeed: number;
  /** How close a dependency line must be to a handle to connect. */
  connectionRadius: number;
  snapToGrid: boolean;
  gridSize: GridSize;
  showMinimap: boolean;
  showDots: boolean;
  showZoomControls: boolean;
  doubleClickAddsTask: boolean;
  confirmDeleteEdge: boolean;
  /** The occasional pop on focus points. The outline stays either way. */
  animateFocus: boolean;
  /** Hide tasks that are still blocked by a prerequisite. */
  startableOnly: boolean;
};

export const DEFAULT_CANVAS_SETTINGS: CanvasSettings = {
  scrollAction: "pan",
  scrollSensitivity: 0.5,
  invertScroll: false,
  scrollAxis: "free",
  dragEmpty: "pan",
  selectModifier: "shift",
  selectionBox: "full",
  autoPan: true,
  autoPanSpeed: 15,
  connectionRadius: 20,
  snapToGrid: false,
  gridSize: 20,
  showMinimap: true,
  showDots: true,
  showZoomControls: true,
  doubleClickAddsTask: true,
  confirmDeleteEdge: true,
  animateFocus: true,
  startableOnly: false,
};

const LEGACY_PAN_SPEED_KEY = "pkrtask-pan-speed";
const LEGACY_STARTABLE_KEY = "pkrtask.canvas.startableOnly";

export type SettingsStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

export function canvasSettingsKey(kerberos: string): string {
  return `pkrtask.settings.v1.${encodeURIComponent(kerberos)}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function finiteNumber(value: unknown, fallback: number, min: number, max: number): number {
  return typeof value === "number" && Number.isFinite(value) ? clamp(value, min, max) : fallback;
}

function gridSizeOf(value: unknown): GridSize {
  return value === 10 || value === 20 || value === 40 ? value : DEFAULT_CANVAS_SETTINGS.gridSize;
}

function flag(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/** Drop unknown or out-of-range fields instead of trusting stored JSON. */
export function normalizeCanvasSettings(value: unknown): CanvasSettings {
  const record = typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  const defaults = DEFAULT_CANVAS_SETTINGS;
  return {
    scrollAction: oneOf(record.scrollAction, SCROLL_ACTIONS, defaults.scrollAction),
    scrollSensitivity: finiteNumber(record.scrollSensitivity, defaults.scrollSensitivity, 0.1, 1.5),
    invertScroll: flag(record.invertScroll, defaults.invertScroll),
    scrollAxis: oneOf(record.scrollAxis, SCROLL_AXES, defaults.scrollAxis),
    dragEmpty: oneOf(record.dragEmpty, DRAG_EMPTY, defaults.dragEmpty),
    selectModifier: oneOf(record.selectModifier, SELECT_MODIFIERS, defaults.selectModifier),
    selectionBox: oneOf(record.selectionBox, SELECTION_BOXES, defaults.selectionBox),
    autoPan: flag(record.autoPan, defaults.autoPan),
    autoPanSpeed: finiteNumber(record.autoPanSpeed, defaults.autoPanSpeed, 4, 40),
    connectionRadius: finiteNumber(record.connectionRadius, defaults.connectionRadius, 8, 80),
    snapToGrid: flag(record.snapToGrid, defaults.snapToGrid),
    gridSize: gridSizeOf(record.gridSize),
    showMinimap: flag(record.showMinimap, defaults.showMinimap),
    showDots: flag(record.showDots, defaults.showDots),
    showZoomControls: flag(record.showZoomControls, defaults.showZoomControls),
    doubleClickAddsTask: flag(record.doubleClickAddsTask, defaults.doubleClickAddsTask),
    confirmDeleteEdge: flag(record.confirmDeleteEdge, defaults.confirmDeleteEdge),
    animateFocus: flag(record.animateFocus, defaults.animateFocus),
    startableOnly: flag(record.startableOnly, defaults.startableOnly),
  };
}

function readLegacy(storage: SettingsStorage): CanvasSettings {
  const next = { ...DEFAULT_CANVAS_SETTINGS };
  const pan = storage.getItem(LEGACY_PAN_SPEED_KEY);
  if (pan !== null) {
    const parsed = Number(pan);
    if (Number.isFinite(parsed)) next.scrollSensitivity = clamp(parsed, 0.1, 1.5);
  }
  if (storage.getItem(LEGACY_STARTABLE_KEY) === "true") next.startableOnly = true;
  return next;
}

export function readCanvasSettings(kerberos: string, storage: SettingsStorage | null): CanvasSettings {
  if (!storage) return { ...DEFAULT_CANVAS_SETTINGS };
  const raw = storage.getItem(canvasSettingsKey(kerberos));
  if (raw === null) return readLegacy(storage);
  try {
    return normalizeCanvasSettings(JSON.parse(raw) as unknown);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    return readLegacy(storage);
  }
}

export function writeCanvasSettings(kerberos: string, settings: CanvasSettings, storage: SettingsStorage): void {
  storage.setItem(canvasSettingsKey(kerberos), JSON.stringify(normalizeCanvasSettings(settings)));
}

/** Signed pan speed passed to the canvas. Scrolling to zoom ignores this. */
export function panScrollSpeed(settings: CanvasSettings): number {
  return settings.invertScroll ? -settings.scrollSensitivity : settings.scrollSensitivity;
}
