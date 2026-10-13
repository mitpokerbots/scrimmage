import { describe, expect, it } from "vitest";
import {
  applyAffine,
  applyAffineToCoords,
  applyAffineToPoints,
  boundsOf,
  composeAffine,
  DEFAULT_FOCUS_PADDING,
  focusTransform,
  IDENTITY_AFFINE,
  invertAffine,
  matchViewportAcrossAffine,
  PLAN_LINK_HEADER_HEIGHT,
  PLAN_LINK_HEIGHT,
  PLAN_LINK_WIDTH,
  planLinkContentRect,
  TASK_NODE_HEIGHT,
  TASK_NODE_WIDTH,
  type Affine,
  type Point,
  type Rect,
  type Viewport,
} from "./plan-focus";

const FLOAT_TOLERANCE = 5;

function paddedContentRect(contentRect: Rect, padding = DEFAULT_FOCUS_PADDING): Rect {
  return {
    x: contentRect.x + padding,
    y: contentRect.y + padding,
    width: Math.max(0, contentRect.width - padding * 2),
    height: Math.max(0, contentRect.height - padding * 2),
  };
}

function rectCorners(rect: Rect): Point[] {
  return [
    { x: rect.x, y: rect.y },
    { x: rect.x + rect.width, y: rect.y },
    { x: rect.x, y: rect.y + rect.height },
    { x: rect.x + rect.width, y: rect.y + rect.height },
  ];
}

function expectPointInsideRect(point: Point, rect: Rect, tolerance = 1e-6): void {
  expect(point.x).toBeGreaterThanOrEqual(rect.x - tolerance);
  expect(point.y).toBeGreaterThanOrEqual(rect.y - tolerance);
  expect(point.x).toBeLessThanOrEqual(rect.x + rect.width + tolerance);
  expect(point.y).toBeLessThanOrEqual(rect.y + rect.height + tolerance);
}

function expectAffineIsFinite(affine: { scale: number; tx: number; ty: number }): void {
  expect(Number.isFinite(affine.scale)).toBe(true);
  expect(Number.isFinite(affine.tx)).toBe(true);
  expect(Number.isFinite(affine.ty)).toBe(true);
  expect(Number.isNaN(affine.scale)).toBe(false);
  expect(Number.isNaN(affine.tx)).toBe(false);
  expect(Number.isNaN(affine.ty)).toBe(false);
}

/**
 * Guarantee under test: zooming into a sub-plan maps child task positions into the
 * plan-link preview pane without spilling outside its padded content area.
 */
describe("child task positions land inside the plan-link preview pane", () => {
  it("maps child anchors and bounds corners into the padded content rect", () => {
    const contentRect = planLinkContentRect(120, 80);
    const childAnchors: Point[] = [
      { x: 0, y: 0 },
      { x: 140, y: 60 },
      { x: 40, y: 110 },
    ];
    const childBounds = boundsOf(childAnchors, TASK_NODE_WIDTH, TASK_NODE_HEIGHT);
    expect(childBounds).not.toBeNull();

    const affine = focusTransform(childBounds!, contentRect);
    const target = paddedContentRect(contentRect);

    for (const corner of rectCorners(childBounds!)) {
      expectPointInsideRect(applyAffine(corner, affine), target);
    }

    for (const anchor of childAnchors) {
      expectPointInsideRect(applyAffine(anchor, affine), target);
    }
  });
});

/**
 * Guarantee under test: saving a dragged task while focused requires reversing the
 * zoom map — round-tripping must recover the original child-local coordinates.
 */
describe("the focus zoom map round-trips through its inverse", () => {
  it("recovers child-local coordinates after mapping into parent space and back", () => {
    const contentRect = planLinkContentRect(0, 0);
    const childBounds = boundsOf(
      [
        { x: 20, y: 30 },
        { x: 200, y: 150 },
      ],
      TASK_NODE_WIDTH,
      TASK_NODE_HEIGHT,
    );
    expect(childBounds).not.toBeNull();

    const affine = focusTransform(childBounds!, contentRect);
    const inverse = invertAffine(affine);
    const samples: Point[] = [
      { x: 0, y: 0 },
      { x: 20, y: 30 },
      { x: 200, y: 150 },
      { x: 77.5, y: 99.25 },
    ];

    for (const sample of samples) {
      const mapped = applyAffine(sample, affine);
      const roundTripped = applyAffine(mapped, inverse);
      expect(roundTripped.x).toBeCloseTo(sample.x, FLOAT_TOLERANCE);
      expect(roundTripped.y).toBeCloseTo(sample.y, FLOAT_TOLERANCE);
    }
  });
});

/**
 * Guarantee under test: diving through nested sub-plans composes focus transforms so
 * N-level zoom matches applying each level's map in order.
 */
describe("nested focus levels compose like applying inner then outer", () => {
  it("matches sequential inner-then-outer application for sample child points", () => {
    const outerContent = planLinkContentRect(300, 100);
    const outerBounds = boundsOf([{ x: 0, y: 0 }, { x: 180, y: 120 }], TASK_NODE_WIDTH, TASK_NODE_HEIGHT);
    expect(outerBounds).not.toBeNull();
    const outer = focusTransform(outerBounds!, outerContent);

    const innerContent = planLinkContentRect(40, 60);
    const innerBounds = boundsOf([{ x: 10, y: 15 }, { x: 90, y: 70 }], TASK_NODE_WIDTH, TASK_NODE_HEIGHT);
    expect(innerBounds).not.toBeNull();
    const inner = focusTransform(innerBounds!, innerContent);

    const composed = composeAffine(outer, inner);
    const samples: Point[] = [
      { x: 0, y: 0 },
      { x: 10, y: 15 },
      { x: 90, y: 70 },
      { x: 55, y: 42 },
    ];

    for (const sample of samples) {
      const viaCompose = applyAffine(sample, composed);
      const viaSequential = applyAffine(applyAffine(sample, inner), outer);
      expect(viaCompose.x).toBeCloseTo(viaSequential.x, FLOAT_TOLERANCE);
      expect(viaCompose.y).toBeCloseTo(viaSequential.y, FLOAT_TOLERANCE);
    }
  });
});

/**
 * Guarantee under test: empty or degenerate layouts must not break the canvas math.
 */
describe("empty or degenerate child layouts do not break focus math", () => {
  it("returns null bounds for an empty point list", () => {
    expect(boundsOf([], TASK_NODE_WIDTH, TASK_NODE_HEIGHT)).toBeNull();
  });

  it("does not produce NaN when child bounds or target area are degenerate", () => {
    const contentRect = planLinkContentRect(50, 50);

    const zeroAreaChild: Rect = { x: 10, y: 20, width: 0, height: 40 };
    expectAffineIsFinite(focusTransform(zeroAreaChild, contentRect));

    const zeroHeightChild: Rect = { x: 10, y: 20, width: 40, height: 0 };
    expectAffineIsFinite(focusTransform(zeroHeightChild, contentRect));

    const childBounds = boundsOf([{ x: 0, y: 0 }], TASK_NODE_WIDTH, TASK_NODE_HEIGHT);
    expect(childBounds).not.toBeNull();

    const paddingLargerThanContent = Math.max(contentRect.width, contentRect.height);
    expectAffineIsFinite(focusTransform(childBounds!, contentRect, paddingLargerThanContent));
  });
});

/**
 * Guarantee under test: the nested preview pane sits below the plan-link header and
 * stays within the card's outer dimensions.
 */
describe("the plan-link preview pane sits below the header within the card", () => {
  it("offsets content below the header and keeps width and height inside the card", () => {
    const linkX = 75;
    const linkY = 125;
    const contentRect = planLinkContentRect(linkX, linkY);

    expect(contentRect.x).toBe(linkX);
    expect(contentRect.y).toBe(linkY + PLAN_LINK_HEADER_HEIGHT);
    expect(contentRect.width).toBe(PLAN_LINK_WIDTH);
    expect(contentRect.height).toBe(PLAN_LINK_HEIGHT - PLAN_LINK_HEADER_HEIGHT);

    expect(contentRect.y).toBeGreaterThanOrEqual(linkY);
    expect(contentRect.x + contentRect.width).toBeLessThanOrEqual(linkX + PLAN_LINK_WIDTH);
    expect(contentRect.y + contentRect.height).toBeLessThanOrEqual(linkY + PLAN_LINK_HEIGHT);
  });
});

/**
 * Guarantee under test: batch coordinate mapping for tasks keeps each task's identity
 * while only updating x/y for display or save inverse-mapping.
 */
describe("batch task coordinate mapping preserves task ids", () => {
  it("keeps ids unchanged and only updates coordinates", () => {
    const contentRect = planLinkContentRect(10, 20);
    const childBounds = boundsOf(
      [
        { x: 0, y: 0 },
        { x: 100, y: 50 },
      ],
      TASK_NODE_WIDTH,
      TASK_NODE_HEIGHT,
    );
    expect(childBounds).not.toBeNull();

    const affine = focusTransform(childBounds!, contentRect);
    const tasks = [
      { id: "task-a", x: 0, y: 0 },
      { id: "task-b", x: 100, y: 50 },
    ];

    const mapped = applyAffineToCoords(tasks, affine);

    expect(mapped.map((task) => task.id)).toEqual(["task-a", "task-b"]);
    expect(mapped).toHaveLength(tasks.length);

    for (let index = 0; index < tasks.length; index += 1) {
      const original = tasks[index]!;
      const result = mapped[index]!;
      const expected = applyAffine({ x: original.x, y: original.y }, affine);

      expect(result.id).toBe(original.id);
      expect(result.x).toBeCloseTo(expected.x, FLOAT_TOLERANCE);
      expect(result.y).toBeCloseTo(expected.y, FLOAT_TOLERANCE);
    }
  });

  it("maps an empty task list without error", () => {
    const identity = { scale: 1, tx: 0, ty: 0 };
    expect(applyAffineToCoords([], identity)).toEqual([]);
    expect(applyAffineToPoints([], identity)).toEqual([]);
  });
});

/**
 * Guarantee under test: preview chips and expand use the same focus affine (scale +
 * relative layout) when both feed TASK_NODE sizes into boundsOf — so diving into a
 * sub-plan continues the layout you already saw in the card.
 */
describe("preview and expand share the same focus scale for a task set", () => {
  it("matches scale when both paths size bounds with full task nodes", () => {
    const points = [
      { x: 40, y: 20 },
      { x: 280, y: 160 },
      { x: 120, y: 300 },
    ];
    const childBounds = boundsOf(points, TASK_NODE_WIDTH, TASK_NODE_HEIGHT);
    expect(childBounds).not.toBeNull();

    const previewContent = {
      x: 0,
      y: 0,
      width: PLAN_LINK_WIDTH,
      height: PLAN_LINK_HEIGHT - PLAN_LINK_HEADER_HEIGHT,
    };
    const expandContent = planLinkContentRect(400, 200);

    const previewAffine = focusTransform(childBounds!, previewContent);
    const expandAffine = focusTransform(childBounds!, expandContent);

    expect(previewAffine.scale).toBeCloseTo(expandAffine.scale, FLOAT_TOLERANCE);

    const previewMapped = applyAffineToPoints(points, previewAffine);
    const expandMapped = applyAffineToPoints(points, expandAffine);
    const previewDx = previewMapped[1]!.x - previewMapped[0]!.x;
    const expandDx = expandMapped[1]!.x - expandMapped[0]!.x;
    expect(previewDx).toBeCloseTo(expandDx, FLOAT_TOLERANCE);
  });
});

function flowToScreen(flow: Point, viewport: Viewport): Point {
  return {
    x: flow.x * viewport.zoom + viewport.x,
    y: flow.y * viewport.zoom + viewport.y,
  };
}

function focusRemapMatrix(fromAffine: Affine | null, toAffine: Affine | null) {
  const to = toAffine ?? IDENTITY_AFFINE;
  const from = fromAffine ?? IDENTITY_AFFINE;
  return composeAffine(invertAffine(to), from);
}

/**
 * Guarantee under test: matching the viewport across a focus affine change keeps
 * a sample flow point at the same screen position.
 */
describe("matching viewport keeps flow points on screen across affine change", () => {
  it("preserves screen position for a sample point when focus affine changes", () => {
    const fromViewport: Viewport = { x: 120, y: 80, zoom: 1.25 };
    const fromAffine = null;
    const toAffine = { scale: 2.4, tx: 48, ty: -16 };
    const sampleFlow: Point = { x: 310, y: 220 };

    const matched = matchViewportAcrossAffine(fromViewport, fromAffine, toAffine);
    const oldScreen = flowToScreen(sampleFlow, fromViewport);
    const remappedFlow = applyAffine(sampleFlow, focusRemapMatrix(fromAffine, toAffine));
    const newScreen = flowToScreen(remappedFlow, matched);

    expect(newScreen.x).toBeCloseTo(oldScreen.x, FLOAT_TOLERANCE);
    expect(newScreen.y).toBeCloseTo(oldScreen.y, FLOAT_TOLERANCE);
  });
});

/**
 * Guarantee under test: identity→identity focus changes leave the viewport unchanged.
 */
describe("identity to identity viewport matching is a no-op", () => {
  it("returns the same viewport when both affines are null", () => {
    const viewport: Viewport = { x: 50, y: -30, zoom: 0.75 };
    expect(matchViewportAcrossAffine(viewport, null, null)).toEqual(viewport);
  });

  it("returns the same viewport when both affines are identity", () => {
    const viewport: Viewport = { x: 10, y: 20, zoom: 2 };
    expect(matchViewportAcrossAffine(viewport, IDENTITY_AFFINE, IDENTITY_AFFINE)).toEqual(viewport);
  });
});

/**
 * Guarantee under test: drill-in and drill-out viewport matching round-trip to the
 * original camera when affines are reversed.
 */
describe("drill-in and drill-out viewport matching round-trips", () => {
  it("returns the original viewport after drill-in then drill-out", () => {
    const contentRect = planLinkContentRect(200, 100);
    const childBounds = boundsOf(
      [
        { x: 0, y: 0 },
        { x: 160, y: 90 },
      ],
      TASK_NODE_WIDTH,
      TASK_NODE_HEIGHT,
    );
    expect(childBounds).not.toBeNull();

    const drillInAffine = focusTransform(childBounds!, contentRect);
    const startViewport: Viewport = { x: -40, y: 60, zoom: 0.9 };

    const drilledIn = matchViewportAcrossAffine(startViewport, null, drillInAffine);
    const drilledOut = matchViewportAcrossAffine(drilledIn, drillInAffine, null);

    expect(drilledOut.x).toBeCloseTo(startViewport.x, FLOAT_TOLERANCE);
    expect(drilledOut.y).toBeCloseTo(startViewport.y, FLOAT_TOLERANCE);
    expect(drilledOut.zoom).toBeCloseTo(startViewport.zoom, FLOAT_TOLERANCE);
  });

  it("preserves screen position when drilling into a real focus transform", () => {
    const contentRect = planLinkContentRect(80, 40);
    const childBounds = boundsOf([{ x: 20, y: 30 }, { x: 140, y: 110 }], TASK_NODE_WIDTH, TASK_NODE_HEIGHT);
    expect(childBounds).not.toBeNull();

    const drillInAffine = focusTransform(childBounds!, contentRect);
    const fromViewport: Viewport = { x: 0, y: 0, zoom: 1 };
    const sampleFlow: Point = { x: 100, y: 70 };

    const matched = matchViewportAcrossAffine(fromViewport, null, drillInAffine);
    const oldScreen = flowToScreen(sampleFlow, fromViewport);
    const remappedFlow = applyAffine(sampleFlow, focusRemapMatrix(null, drillInAffine));
    const newScreen = flowToScreen(remappedFlow, matched);

    expect(newScreen.x).toBeCloseTo(oldScreen.x, FLOAT_TOLERANCE);
    expect(newScreen.y).toBeCloseTo(oldScreen.y, FLOAT_TOLERANCE);
  });
});

/**
 * Guarantee under test: degenerate focus remaps must not produce NaN or Inf viewports.
 */
describe("degenerate focus remap does not break viewport matching", () => {
  it("returns the original viewport when remap scale is near zero", () => {
    const fromViewport: Viewport = { x: 25, y: -10, zoom: 1.8 };
    const fromAffine = { scale: 1e-10, tx: 0, ty: 0 };
    const toAffine = { scale: 1, tx: 100, ty: 200 };

    const matched = matchViewportAcrossAffine(fromViewport, fromAffine, toAffine);

    expect(matched).toEqual(fromViewport);
    expect(Number.isFinite(matched.zoom)).toBe(true);
    expect(Number.isFinite(matched.x)).toBe(true);
    expect(Number.isFinite(matched.y)).toBe(true);
  });
});
