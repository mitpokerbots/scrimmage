import { describe, expect, it, vi } from "vitest";
import {
  captureSnap,
  readStoredSnap,
  snapFromHistoryState,
  stampCurrentHistory,
} from "./history-restore";

function stubWindowWithStorage({
  pathname = "/plans/p1",
  search = "?focus=a",
  scrollX = 0,
  scrollY = 0,
  store = new Map<string, string>(),
}: {
  pathname?: string;
  search?: string;
  scrollX?: number;
  scrollY?: number;
  store?: Map<string, string>;
} = {}) {
  vi.stubGlobal("window", {
    scrollX,
    scrollY,
    location: { pathname, search, href: `http://localhost${pathname}${search}` },
    history: { state: null, replaceState: () => {} },
    sessionStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    },
  });
  return store;
}

describe("back restores the stamped scroll snapshot", () => {
  it("reads scroll coordinates from pkrtask history state", () => {
    expect(
      snapFromHistoryState({
        pkrtask: { scrollX: 120, scrollY: 480 },
      }),
    ).toEqual({ scrollX: 120, scrollY: 480 });
  });

  it("preserves ui alongside scroll when present", () => {
    expect(
      snapFromHistoryState({
        pkrtask: {
          scrollX: 0,
          scrollY: 0,
          ui: { inspector: { selectedTaskId: "t1" } },
        },
      }),
    ).toEqual({
      scrollX: 0,
      scrollY: 0,
      ui: { inspector: { selectedTaskId: "t1" } },
    });
  });
});

describe("garbage history state is ignored", () => {
  it("rejects null, primitives, arrays, and missing pkrtask", () => {
    expect(snapFromHistoryState(null)).toBeNull();
    expect(snapFromHistoryState(undefined)).toBeNull();
    expect(snapFromHistoryState("nope")).toBeNull();
    expect(snapFromHistoryState([])).toBeNull();
    expect(snapFromHistoryState({})).toBeNull();
  });

  it("rejects non-finite scroll and malformed ui", () => {
    expect(snapFromHistoryState({ pkrtask: { scrollX: NaN, scrollY: 0 } })).toBeNull();
    expect(snapFromHistoryState({ pkrtask: { scrollX: 0, scrollY: Infinity } })).toBeNull();
    expect(
      snapFromHistoryState({ pkrtask: { scrollX: 0, scrollY: 0, ui: "bad" } }),
    ).toBeNull();
    expect(
      snapFromHistoryState({ pkrtask: { scrollX: 0, scrollY: 0, ui: [] } }),
    ).toBeNull();
  });
});

describe("captureSnap stamps live scroll and optional ui", () => {
  it("returns the expected shape without ui", () => {
    vi.stubGlobal("window", { scrollX: 12, scrollY: 34 });
    expect(captureSnap()).toEqual({ scrollX: 12, scrollY: 34 });
    vi.unstubAllGlobals();
  });

  it("includes ui when provided", () => {
    vi.stubGlobal("window", { scrollX: 0, scrollY: 0 });
    expect(captureSnap({ listSort: { key: "name", dir: "asc" } })).toEqual({
      scrollX: 0,
      scrollY: 0,
      ui: { listSort: { key: "name", dir: "asc" } },
    });
    vi.unstubAllGlobals();
  });
});

describe("back can restore even after the router wipes history.state", () => {
  it("stamping mirrors the snap per URL and readStoredSnap returns it", () => {
    stubWindowWithStorage({ scrollY: 640 });
    stampCurrentHistory({ inspector: { selectedTaskId: "t2" } });
    expect(readStoredSnap()).toEqual({
      scrollX: 0,
      scrollY: 640,
      ui: { inspector: { selectedTaskId: "t2" } },
    });
    vi.unstubAllGlobals();
  });

  it("mirrors are scoped to the exact path and query", () => {
    const store = stubWindowWithStorage({ scrollY: 300 });
    stampCurrentHistory();
    expect(store.has("pkrtask:snap:/plans/p1?focus=a")).toBe(true);
    stubWindowWithStorage({ pathname: "/plans/p1", search: "?focus=b", store });
    expect(readStoredSnap()).toBeNull();
    vi.unstubAllGlobals();
  });

  it("a corrupt mirror is ignored instead of breaking restore", () => {
    const store = stubWindowWithStorage();
    store.set("pkrtask:snap:/plans/p1?focus=a", "{not json");
    expect(readStoredSnap()).toBeNull();
    vi.unstubAllGlobals();
  });

  it("a background scroll stamp cannot erase mirrored ui state", () => {
    stubWindowWithStorage({ scrollY: 200 });
    stampCurrentHistory({ inspector: { open: true } });
    // Simulate the router wiping history.state, then a ui-less scroll stamp.
    stampCurrentHistory();
    expect(readStoredSnap()?.ui).toEqual({ inspector: { open: true } });
    vi.unstubAllGlobals();
  });
});

describe("back restores named overflow scroll positions", () => {
  it("returns scroll and well-formed container positions from history state", () => {
    expect(
      snapFromHistoryState({
        pkrtask: {
          scrollX: 10,
          scrollY: 20,
          containers: {
            "plan-list": { left: 100, top: 200 },
            "plan-canvas": { left: 50, top: 75 },
          },
        },
      }),
    ).toEqual({
      scrollX: 10,
      scrollY: 20,
      containers: {
        "plan-list": { left: 100, top: 200 },
        "plan-canvas": { left: 50, top: 75 },
      },
    });
  });
});

describe("malformed container snapshots do not break scroll restore", () => {
  it("still returns page scroll when containers is not an object", () => {
    expect(
      snapFromHistoryState({
        pkrtask: { scrollX: 5, scrollY: 15, containers: "bad" },
      }),
    ).toEqual({ scrollX: 5, scrollY: 15 });

    expect(
      snapFromHistoryState({
        pkrtask: { scrollX: 5, scrollY: 15, containers: null },
      }),
    ).toEqual({ scrollX: 5, scrollY: 15 });

    expect(
      snapFromHistoryState({
        pkrtask: { scrollX: 5, scrollY: 15, containers: [] },
      }),
    ).toEqual({ scrollX: 5, scrollY: 15 });
  });

  it("omits container entries with non-finite or non-numeric positions", () => {
    expect(
      snapFromHistoryState({
        pkrtask: {
          scrollX: 0,
          scrollY: 0,
          containers: {
            good: { left: 10, top: 20 },
            badLeft: { left: NaN, top: 5 },
            badTop: { left: 5, top: Infinity },
            notNumbers: { left: "0", top: 0 },
            notAnObject: null,
          },
        },
      }),
    ).toEqual({
      scrollX: 0,
      scrollY: 0,
      containers: { good: { left: 10, top: 20 } },
    });
  });

  it("drops containers entirely when every entry is invalid", () => {
    expect(
      snapFromHistoryState({
        pkrtask: {
          scrollX: 3,
          scrollY: 4,
          containers: {
            badLeft: { left: NaN, top: 1 },
            badTop: { left: 2, top: Infinity },
          },
        },
      }),
    ).toEqual({ scrollX: 3, scrollY: 4 });
  });
});

describe("captureSnap records named overflow scroll positions", () => {
  it("includes scroll positions from data-pkrtask-scroll elements", () => {
    class MockHTMLElement {
      scrollLeft = 0;
      scrollTop = 0;
      getAttribute = (): string | null => null;
    }
    vi.stubGlobal("HTMLElement", MockHTMLElement);

    const rosterScroller = new MockHTMLElement();
    rosterScroller.getAttribute = () => "task-panel";
    rosterScroller.scrollLeft = 88;
    rosterScroller.scrollTop = 99;

    vi.stubGlobal("window", { scrollX: 1, scrollY: 2 });
    vi.stubGlobal("document", {
      querySelectorAll: () => [rosterScroller],
    });

    expect(captureSnap()).toEqual({
      scrollX: 1,
      scrollY: 2,
      containers: { "task-panel": { left: 88, top: 99 } },
    });

    vi.unstubAllGlobals();
  });

  it("still returns page scroll when document is unavailable", () => {
    vi.stubGlobal("window", { scrollX: 7, scrollY: 8 });
    vi.stubGlobal("document", undefined);

    expect(captureSnap()).toEqual({ scrollX: 7, scrollY: 8 });

    vi.unstubAllGlobals();
  });
});
