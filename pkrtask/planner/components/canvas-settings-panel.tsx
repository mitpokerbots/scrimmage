"use client";

import { useEffect, type ReactNode } from "react";
import { Button } from "@/components/button";
import {
  DEFAULT_CANVAS_SETTINGS,
  GRID_SIZES,
  type CanvasSettings,
  type DragEmpty,
  type ScrollAction,
  type ScrollAxis,
  type SelectModifier,
  type SelectionBox,
} from "../canvas-settings";

type CanvasSettingsPanelProps = {
  settings: CanvasSettings;
  onChange: (next: CanvasSettings) => void;
  onClose: () => void;
};

function patch<K extends keyof CanvasSettings>(
  settings: CanvasSettings,
  key: K,
  value: CanvasSettings[K],
  onChange: (next: CanvasSettings) => void,
) {
  onChange({ ...settings, [key]: value });
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-3 border-t border-line pt-3">
      <legend className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{title}</legend>
      {children}
    </fieldset>
  );
}

function Choice<T extends string>({
  label,
  hint,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  value: T;
  options: readonly { value: T; label: string }[];
  disabled?: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm text-ink">{label}</span>
      {hint ? <span className="text-xs text-ink-muted">{hint}</span> : null}
      <div className={`flex flex-wrap gap-1.5 ${disabled ? "opacity-50" : ""}`}>
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={selected}
              disabled={disabled}
              onClick={() => onChange(option.value)}
              className={`rounded-md border px-2 py-1 text-xs ${
                selected
                  ? "border-highlight bg-highlight/15 text-highlight"
                  : "border-line bg-raised text-ink-muted hover:text-ink"
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Slider({
  label,
  hint,
  min,
  max,
  step,
  value,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  min: number;
  max: number;
  step: number;
  value: number;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className={`flex flex-col gap-1 text-sm text-ink ${disabled ? "opacity-50" : ""}`}>
      <span className="flex items-baseline justify-between gap-3">
        {label}
        <span className="text-xs tabular-nums text-ink-muted">{value.toFixed(step < 1 ? 2 : 0)}</span>
      </span>
      {hint ? <span className="text-xs text-ink-muted">{hint}</span> : null}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="accent-highlight"
      />
    </label>
  );
}

function Check({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`flex items-start gap-2 text-sm text-ink ${disabled ? "opacity-50" : ""}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 accent-highlight"
      />
      <span>
        {label}
        {hint ? <span className="mt-0.5 block text-xs text-ink-muted">{hint}</span> : null}
      </span>
    </label>
  );
}

export function CanvasSettingsPanel({ settings, onChange, onClose }: CanvasSettingsPanelProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const scrollZooms = settings.scrollAction === "zoom";

  return (
    <aside
      className="absolute inset-y-0 left-0 z-30 flex w-80 flex-col border-r border-line bg-surface"
      aria-label="Canvas settings"
    >
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Settings</h2>
          <p className="mt-1 text-xs text-ink-muted">Saved in this browser for you. Try them on the board while this stays open.</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-2 py-0.5 text-sm text-ink-muted hover:bg-raised hover:text-ink"
          aria-label="Close settings (Esc)"
        >
          ✕
        </button>
      </div>
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-3">
        <Section title="Moving">
          <Choice<ScrollAction>
            label="Scroll"
            hint="Pinch still zooms."
            value={settings.scrollAction}
            options={[
              { value: "pan", label: "Pan the board" },
              { value: "zoom", label: "Zoom" },
            ]}
            onChange={(scrollAction) => patch(settings, "scrollAction", scrollAction, onChange)}
          />
          <Slider
            label="Scroll sensitivity"
            hint={scrollZooms ? "Used when scroll pans the board." : undefined}
            min={0.1}
            max={1.5}
            step={0.05}
            value={settings.scrollSensitivity}
            disabled={scrollZooms}
            onChange={(scrollSensitivity) => patch(settings, "scrollSensitivity", scrollSensitivity, onChange)}
          />
          <Choice<ScrollAxis>
            label="Scroll direction"
            value={settings.scrollAxis}
            disabled={scrollZooms}
            options={[
              { value: "free", label: "Free" },
              { value: "horizontal", label: "Horizontal" },
              { value: "vertical", label: "Vertical" },
            ]}
            onChange={(scrollAxis) => patch(settings, "scrollAxis", scrollAxis, onChange)}
          />
          <Check
            label="Invert scroll"
            checked={settings.invertScroll}
            disabled={scrollZooms}
            onChange={(invertScroll) => patch(settings, "invertScroll", invertScroll, onChange)}
          />
          <Choice<DragEmpty>
            label="Drag empty space"
            hint={settings.dragEmpty === "select" ? "Hold Space to pan." : undefined}
            value={settings.dragEmpty}
            options={[
              { value: "pan", label: "Pan" },
              { value: "select", label: "Select cards" },
            ]}
            onChange={(dragEmpty) => patch(settings, "dragEmpty", dragEmpty, onChange)}
          />
          <Check
            label="Pan when a drag hits the edge"
            checked={settings.autoPan}
            onChange={(autoPan) => patch(settings, "autoPan", autoPan, onChange)}
          />
          <Slider
            label="Edge pan speed"
            min={4}
            max={40}
            step={1}
            value={settings.autoPanSpeed}
            disabled={!settings.autoPan}
            onChange={(autoPanSpeed) => patch(settings, "autoPanSpeed", autoPanSpeed, onChange)}
          />
        </Section>

        <Section title="Selecting">
          <Choice<SelectModifier>
            label="Add to the selection"
            value={settings.selectModifier}
            options={[
              { value: "shift", label: "Shift-click" },
              { value: "meta", label: "⌘ or Ctrl-click" },
            ]}
            onChange={(selectModifier) => patch(settings, "selectModifier", selectModifier, onChange)}
          />
          <Choice<SelectionBox>
            label="Selection box"
            value={settings.selectionBox}
            options={[
              { value: "full", label: "Fully inside" },
              { value: "partial", label: "Any overlap" },
            ]}
            onChange={(selectionBox) => patch(settings, "selectionBox", selectionBox, onChange)}
          />
        </Section>

        <Section title="Dependencies">
          <Slider
            label="Snap distance"
            hint="How close a line must get to a handle to connect."
            min={8}
            max={80}
            step={2}
            value={settings.connectionRadius}
            onChange={(connectionRadius) => patch(settings, "connectionRadius", connectionRadius, onChange)}
          />
          <Check
            label="Ask before removing a dependency"
            checked={settings.confirmDeleteEdge}
            onChange={(confirmDeleteEdge) => patch(settings, "confirmDeleteEdge", confirmDeleteEdge, onChange)}
          />
        </Section>

        <Section title="Board">
          <Check
            label="Snap cards to a grid"
            checked={settings.snapToGrid}
            onChange={(snapToGrid) => patch(settings, "snapToGrid", snapToGrid, onChange)}
          />
          <Choice
            label="Grid size"
            value={String(settings.gridSize)}
            disabled={!settings.snapToGrid}
            options={GRID_SIZES.map((size) => ({ value: String(size), label: `${size}px` }))}
            onChange={(value) => patch(settings, "gridSize", Number(value) as CanvasSettings["gridSize"], onChange)}
          />
          <Check
            label="Dot background"
            checked={settings.showDots}
            onChange={(showDots) => patch(settings, "showDots", showDots, onChange)}
          />
          <Check
            label="Minimap"
            checked={settings.showMinimap}
            onChange={(showMinimap) => patch(settings, "showMinimap", showMinimap, onChange)}
          />
          <Check
            label="Zoom buttons"
            checked={settings.showZoomControls}
            onChange={(showZoomControls) => patch(settings, "showZoomControls", showZoomControls, onChange)}
          />
          <Check
            label="Hide blocked tasks"
            hint="Only tasks you can start stay visible."
            checked={settings.startableOnly}
            onChange={(startableOnly) => patch(settings, "startableOnly", startableOnly, onChange)}
          />
          <Check
            label="Animate focus points"
            hint="The outline stays. This is the occasional pop."
            checked={settings.animateFocus}
            onChange={(animateFocus) => patch(settings, "animateFocus", animateFocus, onChange)}
          />
        </Section>

        <Section title="Editing">
          <Check
            label="Double-click empty space to add a task"
            checked={settings.doubleClickAddsTask}
            onChange={(doubleClickAddsTask) => patch(settings, "doubleClickAddsTask", doubleClickAddsTask, onChange)}
          />
        </Section>
      </div>
      <div className="border-t border-line px-4 py-3">
        <Button variant="ghost" onClick={() => onChange({ ...DEFAULT_CANVAS_SETTINGS })}>
          Reset to defaults
        </Button>
      </div>
    </aside>
  );
}
