/** Pure affine helpers for mapping child-plan coordinates into plan-link preview/focus space. */

export const PLAN_LINK_WIDTH = 360;
export const PLAN_LINK_HEIGHT = 240;

/** Chrome header above the nested preview pane (~py-2 + two text lines). */
export const PLAN_LINK_HEADER_HEIGHT = 44;

export const TASK_NODE_WIDTH = 240;
export const TASK_NODE_HEIGHT = 84;

export const PREVIEW_NODE_WIDTH = 72;
export const PREVIEW_NODE_HEIGHT = 28;

export const DEFAULT_FOCUS_PADDING = 8;

export type Point = { x: number; y: number };

export type Rect = { x: number; y: number; width: number; height: number };

/** Uniform scale plus translate: parent = scale * child + (tx, ty). */
export type Affine = { scale: number; tx: number; ty: number };

export const IDENTITY_AFFINE: Affine = { scale: 1, tx: 0, ty: 0 };

export type Viewport = { x: number; y: number; zoom: number };

const EPS = 1e-9;

function asAffine(affine: Affine | null): Affine {
  return affine ?? IDENTITY_AFFINE;
}

/** Content rect of a plan-link node at parent flow position (link.x, link.y). */
export function planLinkContentRect(linkX: number, linkY: number): Rect {
  return {
    x: linkX,
    y: linkY + PLAN_LINK_HEADER_HEIGHT,
    width: PLAN_LINK_WIDTH,
    height: PLAN_LINK_HEIGHT - PLAN_LINK_HEADER_HEIGHT,
  };
}

/** Bounding box of task anchor points plus node width/height. */
export function boundsOf(points: Point[], nodeW: number, nodeH: number): Rect | null {
  if (points.length === 0) {
    return null;
  }

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const point of points) {
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x + nodeW);
    maxY = Math.max(maxY, point.y + nodeH);
  }

  return {
    x: minX,
    y: minY,
    width: maxX - minX,
    height: maxY - minY,
  };
}

function centeredIdentity(contentRect: Rect): Affine {
  return {
    scale: 1,
    tx: contentRect.x + contentRect.width / 2,
    ty: contentRect.y + contentRect.height / 2,
  };
}

function paddedRect(rect: Rect, padding: number): Rect {
  const inset = Math.max(0, padding);
  const width = Math.max(0, rect.width - inset * 2);
  const height = Math.max(0, rect.height - inset * 2);
  return {
    x: rect.x + inset,
    y: rect.y + inset,
    width,
    height,
  };
}

function isDegenerateArea(width: number, height: number): boolean {
  return width <= EPS || height <= EPS;
}

/**
 * Uniform scale + translate mapping child-local coords into the content rect (fit with padding).
 * Degenerate child bounds or target area map the child origin to the content center at scale 1.
 */
export function focusTransform(
  childBounds: Rect,
  contentRect: Rect,
  padding: number = DEFAULT_FOCUS_PADDING,
): Affine {
  const target = paddedRect(contentRect, padding);

  if (
    isDegenerateArea(childBounds.width, childBounds.height) ||
    isDegenerateArea(target.width, target.height)
  ) {
    return centeredIdentity(contentRect);
  }

  const scale = Math.min(target.width / childBounds.width, target.height / childBounds.height);
  const childCenterX = childBounds.x + childBounds.width / 2;
  const childCenterY = childBounds.y + childBounds.height / 2;
  const targetCenterX = target.x + target.width / 2;
  const targetCenterY = target.y + target.height / 2;

  return {
    scale,
    tx: targetCenterX - scale * childCenterX,
    ty: targetCenterY - scale * childCenterY,
  };
}

export function applyAffine(point: Point, affine: Affine): Point {
  return {
    x: affine.scale * point.x + affine.tx,
    y: affine.scale * point.y + affine.ty,
  };
}

export function invertAffine(affine: Affine): Affine {
  const invScale = 1 / affine.scale;
  return {
    scale: invScale,
    tx: -affine.tx * invScale,
    ty: -affine.ty * invScale,
  };
}

/** Apply inner, then outer: composeAffine(outer, inner)(p) === applyAffine(p, outer(inner(p))). */
export function composeAffine(outer: Affine, inner: Affine): Affine {
  return {
    scale: outer.scale * inner.scale,
    tx: outer.scale * inner.tx + outer.tx,
    ty: outer.scale * inner.ty + outer.ty,
  };
}

export function applyAffineToPoints(points: Point[], affine: Affine): Point[] {
  return points.map((point) => applyAffine(point, affine));
}

export function applyAffineToCoords<T extends { id: string; x: number; y: number }>(
  items: T[],
  affine: Affine,
): T[] {
  return items.map((item) => {
    const mapped = applyAffine({ x: item.x, y: item.y }, affine);
    return { ...item, x: mapped.x, y: mapped.y };
  });
}

/**
 * Given a React Flow viewport in the old focus-local flow space, return the
 * viewport in the new focus-local flow space that paints the same pixels.
 * Flow remapping is M = invert(toAffine) ∘ fromAffine (null affine = identity).
 * screen = flow * zoom + pan must be preserved for all flow points.
 */
export function matchViewportAcrossAffine(
  fromViewport: Viewport,
  fromAffine: Affine | null,
  toAffine: Affine | null,
): Viewport {
  const M = composeAffine(invertAffine(asAffine(toAffine)), asAffine(fromAffine));

  if (Math.abs(M.scale) <= EPS) {
    return fromViewport;
  }

  const zoom = fromViewport.zoom / M.scale;
  return {
    x: fromViewport.x - M.tx * zoom,
    y: fromViewport.y - M.ty * zoom,
    zoom,
  };
}
