import { describe, expect, it } from "vitest";
import {
  DEFAULT_CANVAS_SETTINGS,
  canvasSettingsKey,
  normalizeCanvasSettings,
  panScrollSpeed,
  readCanvasSettings,
  writeCanvasSettings,
  type SettingsStorage,
} from "./canvas-settings";

function memory(): SettingsStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

describe("canvas settings", () => {
  it("starts from the current canvas behavior when nothing is stored", () => {
    expect(readCanvasSettings("bcostin", memory())).toEqual(DEFAULT_CANVAS_SETTINGS);
    expect(readCanvasSettings("bcostin", null)).toEqual(DEFAULT_CANVAS_SETTINGS);
  });

  it("keeps two people apart and round-trips a change", () => {
    const storage = memory();
    writeCanvasSettings("bcostin", { ...DEFAULT_CANVAS_SETTINGS, scrollAction: "zoom", gridSize: 40 }, storage);
    writeCanvasSettings("samtzhou", { ...DEFAULT_CANVAS_SETTINGS, invertScroll: true }, storage);

    expect(readCanvasSettings("bcostin", storage).scrollAction).toBe("zoom");
    expect(readCanvasSettings("bcostin", storage).gridSize).toBe(40);
    expect(readCanvasSettings("samtzhou", storage).scrollAction).toBe("pan");
    expect(readCanvasSettings("samtzhou", storage).invertScroll).toBe(true);
    expect(canvasSettingsKey("bcostin")).not.toBe(canvasSettingsKey("samtzhou"));
  });

  it("drops a broken or out-of-range value instead of applying it", () => {
    const storage = memory();
    storage.setItem(canvasSettingsKey("bcostin"), JSON.stringify({ scrollSensitivity: 99, scrollAction: "fly" }));
    const settings = readCanvasSettings("bcostin", storage);
    expect(settings.scrollSensitivity).toBe(1.5);
    expect(settings.scrollAction).toBe("pan");
    expect(settings.showMinimap).toBe(true);
  });

  it("falls back when the stored JSON is not JSON", () => {
    const storage = memory();
    storage.setItem(canvasSettingsKey("bcostin"), "{");
    expect(readCanvasSettings("bcostin", storage)).toEqual(DEFAULT_CANVAS_SETTINGS);
  });

  it("picks up the old scroll slider and startable checkbox once", () => {
    const storage = memory();
    storage.setItem("pkrtask-pan-speed", "1.2");
    storage.setItem("pkrtask.canvas.startableOnly", "true");
    const first = readCanvasSettings("bcostin", storage);
    expect(first.scrollSensitivity).toBeCloseTo(1.2);
    expect(first.startableOnly).toBe(true);

    writeCanvasSettings("bcostin", { ...first, scrollSensitivity: 0.2, startableOnly: false }, storage);
    storage.setItem("pkrtask-pan-speed", "1.5");
    expect(readCanvasSettings("bcostin", storage).scrollSensitivity).toBeCloseTo(0.2);
    expect(readCanvasSettings("bcostin", storage).startableOnly).toBe(false);
  });

  it("reverses pan speed only when invert is on", () => {
    expect(panScrollSpeed(DEFAULT_CANVAS_SETTINGS)).toBe(0.5);
    expect(panScrollSpeed({ ...DEFAULT_CANVAS_SETTINGS, invertScroll: true, scrollSensitivity: 0.8 })).toBeCloseTo(
      -0.8,
    );
  });

  it("normalizes a partial object", () => {
    expect(normalizeCanvasSettings({ snapToGrid: true, gridSize: 10 }).snapToGrid).toBe(true);
    expect(normalizeCanvasSettings({ gridSize: 15 }).gridSize).toBe(20);
  });
});
