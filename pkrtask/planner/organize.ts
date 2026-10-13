import type { GraphNodeKind } from "./types";

/** Minimal edge shape for partition helpers (matches `edges` rows / EdgeDto). */
export type OrganizeEdge = {
  id: string;
  fromKind: GraphNodeKind;
  fromId: string;
  toKind: GraphNodeKind;
  toId: string;
};

export type PositionedNode = {
  id: string;
  x: number;
  y: number;
};

export type EdgePartitionMove = {
  migrate: OrganizeEdge[];
  drop: OrganizeEdge[];
};

export type EdgePartitionPackage = {
  migrate: OrganizeEdge[];
  rewrite: OrganizeEdge[];
  unchanged: OrganizeEdge[];
};

export type RewrittenEdge = {
  fromKind: GraphNodeKind;
  fromId: string;
  toKind: GraphNodeKind;
  toId: string;
};

export function isNodeInMovedSet(
  kind: GraphNodeKind,
  id: string,
  movedTaskIds: ReadonlySet<string>,
  movedPlanLinkIds: ReadonlySet<string>,
): boolean {
  return kind === "task" ? movedTaskIds.has(id) : movedPlanLinkIds.has(id);
}

/** Both endpoints inside the moved set → migrate; otherwise drop (boundary). */
export function partitionEdgesForMove(
  edges: OrganizeEdge[],
  movedTaskIds: ReadonlySet<string>,
  movedPlanLinkIds: ReadonlySet<string>,
): EdgePartitionMove {
  const migrate: OrganizeEdge[] = [];
  const drop: OrganizeEdge[] = [];

  for (const edge of edges) {
    const fromInside = isNodeInMovedSet(edge.fromKind, edge.fromId, movedTaskIds, movedPlanLinkIds);
    const toInside = isNodeInMovedSet(edge.toKind, edge.toId, movedTaskIds, movedPlanLinkIds);
    if (fromInside && toInside) {
      migrate.push(edge);
    } else if (fromInside || toInside) {
      drop.push(edge);
    }
    // Edges wholly outside the moved set stay on the source plan untouched.
  }

  return { migrate, drop };
}

/** Classify parent-plan edges when packaging a selection into a new sub-plan. */
export function partitionEdgesForPackage(
  edges: OrganizeEdge[],
  movedTaskIds: ReadonlySet<string>,
  movedPlanLinkIds: ReadonlySet<string>,
): EdgePartitionPackage {
  const migrate: OrganizeEdge[] = [];
  const rewrite: OrganizeEdge[] = [];
  const unchanged: OrganizeEdge[] = [];

  for (const edge of edges) {
    const fromInside = isNodeInMovedSet(edge.fromKind, edge.fromId, movedTaskIds, movedPlanLinkIds);
    const toInside = isNodeInMovedSet(edge.toKind, edge.toId, movedTaskIds, movedPlanLinkIds);
    if (fromInside && toInside) {
      migrate.push(edge);
    } else if (fromInside || toInside) {
      rewrite.push(edge);
    } else {
      unchanged.push(edge);
    }
  }

  return { migrate, rewrite, unchanged };
}

/**
 * Rewrite a boundary edge so the packaged side becomes the new plan_link, leaving a
 * single endpoint on the parent. Returns null when the rewrite would be invalid.
 */
export function rewriteBoundaryEdgeToPlanLink(
  edge: OrganizeEdge,
  planLinkId: string,
  movedTaskIds: ReadonlySet<string>,
  movedPlanLinkIds: ReadonlySet<string>,
): RewrittenEdge | null {
  const fromInside = isNodeInMovedSet(edge.fromKind, edge.fromId, movedTaskIds, movedPlanLinkIds);
  const toInside = isNodeInMovedSet(edge.toKind, edge.toId, movedTaskIds, movedPlanLinkIds);
  if (fromInside === toInside) return null;

  let rewritten: RewrittenEdge;
  if (fromInside) {
    rewritten = {
      fromKind: "plan_link",
      fromId: planLinkId,
      toKind: edge.toKind,
      toId: edge.toId,
    };
  } else {
    rewritten = {
      fromKind: edge.fromKind,
      fromId: edge.fromId,
      toKind: "plan_link",
      toId: planLinkId,
    };
  }

  if (
    rewritten.fromKind === rewritten.toKind &&
    rewritten.fromId === rewritten.toId
  ) {
    return null;
  }

  return rewritten;
}

/** Translate positions so the selection bbox min corner lands on `anchor`. */
export function remapPositionsNearAnchor(
  nodes: PositionedNode[],
  anchor: { x: number; y: number },
): Map<string, { x: number; y: number }> {
  const result = new Map<string, { x: number; y: number }>();
  if (nodes.length === 0) return result;

  let minX = Infinity;
  let minY = Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
  }

  const offsetX = anchor.x - minX;
  const offsetY = anchor.y - minY;
  for (const node of nodes) {
    result.set(node.id, { x: node.x + offsetX, y: node.y + offsetY });
  }
  return result;
}

/** Walk up plan_links from `startPlanId` (includes self). */
function collectPlanAncestors(
  startPlanId: string,
  parentPlanIdsByChild: ReadonlyMap<string, readonly string[]>,
): Set<string> {
  const ancestors = new Set<string>();
  const queue = [startPlanId];
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || ancestors.has(current)) continue;
    ancestors.add(current);
    for (const parent of parentPlanIdsByChild.get(current) ?? []) {
      queue.push(parent);
    }
  }
  return ancestors;
}

/**
 * Lowest common ancestor plan for undo history when moving between canvases.
 * Returns null when source and target are not in the same plan tree.
 */
export function findHistoryRootPlanId(
  sourcePlanId: string,
  targetPlanId: string,
  parentPlanIdsByChild: ReadonlyMap<string, readonly string[]>,
): string | null {
  if (sourcePlanId === targetPlanId) return sourcePlanId;

  const sourceAncestors = collectPlanAncestors(sourcePlanId, parentPlanIdsByChild);

  const queue = [targetPlanId];
  const seen = new Set<string>();
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || seen.has(current)) continue;
    seen.add(current);
    if (sourceAncestors.has(current)) return current;
    for (const parent of parentPlanIdsByChild.get(current) ?? []) {
      queue.push(parent);
    }
  }
  return null;
}

/** True when `targetPlanId` is the child plan or nested under it (invalid reparent target). */
export function wouldMoveLinkIntoDescendant(
  targetPlanId: string,
  linkChildPlanId: string,
  childPlanIdsByParent: ReadonlyMap<string, readonly string[]>,
): boolean {
  if (targetPlanId === linkChildPlanId) return true;
  const queue = [linkChildPlanId];
  const seen = new Set<string>();
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || seen.has(current)) continue;
    seen.add(current);
    if (current === targetPlanId) return true;
    const children = childPlanIdsByParent.get(current) ?? [];
    for (const child of children) {
      queue.push(child);
    }
  }
  return false;
}
