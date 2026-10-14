import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { db } from "@/db";
import {
  planHistory,
  edges,
  planLinks,
  plans,
  taskAssignees,
  tasks,
} from "@/db/schema";
import type { Viewer } from "@/lib/auth";
import type { LockKey } from "@/realtime/protocol";
import { assertUnlocked, linkKey, planKey, taskKey } from "./locks";
import type { EdgeDto, PlanVisibility, TaskDto, TaskEffort, TaskStatus } from "./types";
import {
  type DbExecutor,
  loadPlanSubtree,
  mapEdgeRows,
  mapTaskRows,
  MAX_SUBPLAN_DEPTH,
} from "./queries";

export type PlanSnapshotPlan = {
  id: string;
  name: string;
  description: string;
  status: TaskStatus;
  effort: TaskEffort | null;
  deadline: string | null;
  notes: string;
  ownerKerberos: string;
  /** Compared, but only written when undo re-creates a deleted plan: undo never un-shares. */
  visibility: PlanVisibility;
};

export type PlanLinkSnapshot = {
  id: string;
  parentPlanId: string;
  childPlanId: string;
  x: number;
  y: number;
  width: number;
  height: number;
  child: PlanSnapshot;
};

/** Recursive plan subtree for undo/redo (root plan + nested sub-plans). */
export type PlanSnapshot = {
  plan: PlanSnapshotPlan;
  tasks: TaskDto[];
  edges: EdgeDto[];
  links: PlanLinkSnapshot[];
};

export type GeometryTaskPose = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type GeometryLinkPose = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type GeometryPayload = {
  kind: "geometry";
  tasks: GeometryTaskPose[];
  links: GeometryLinkPose[];
};

export type SnapshotPayload = {
  kind: "snapshot";
  plan: PlanSnapshot;
};

export type HistoryPayload = GeometryPayload | SnapshotPayload;

export type UndoRedoState = {
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
};

export type EntityChange = {
  kind: "task" | "plan" | "edge" | "link";
  id: string;
};

export type CoalesceInput = {
  taskId?: string;
  planId?: string;
  linkId?: string;
};

const HISTORY_LIMIT = 50;

function isPlanSnapshot(raw: unknown): raw is PlanSnapshot {
  if (!raw || typeof raw !== "object") return false;
  const obj = raw as Record<string, unknown>;
  return (
    typeof obj.plan === "object" &&
    obj.plan !== null &&
    Array.isArray(obj.tasks) &&
    Array.isArray(obj.edges) &&
    Array.isArray(obj.links)
  );
}

/** Validate a stored history payload; null when it is not one. */
export function asHistoryPayload(raw: unknown): HistoryPayload | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (obj.kind === "geometry" && Array.isArray(obj.tasks) && Array.isArray(obj.links)) {
    return raw as GeometryPayload;
  }
  if (obj.kind === "snapshot" && isPlanSnapshot(obj.plan)) {
    return { kind: "snapshot", plan: obj.plan };
  }
  return null;
}

export function buildGeometryPayload(
  tasks: GeometryTaskPose[],
  links: GeometryLinkPose[],
): GeometryPayload {
  return { kind: "geometry", tasks, links };
}

function planFieldsFromRow(row: {
  id: string;
  name: string;
  description: string;
  status: string;
  effort: string | null;
  deadline: Date | null;
  notes: string;
  ownerKerberos: string;
  visibility: PlanVisibility;
}): PlanSnapshotPlan {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    status: row.status as TaskStatus,
    effort: row.effort as TaskEffort | null,
    deadline: row.deadline ? row.deadline.toISOString() : null,
    notes: row.notes,
    ownerKerberos: row.ownerKerberos,
    visibility: row.visibility,
  };
}

function buildSnapshotFromBundle(
  planId: string,
  bundles: Awaited<ReturnType<typeof loadPlanSubtree>>,
  pathPlanIds: Set<string>,
  depth: number,
): PlanSnapshot | null {
  const bundle = bundles.get(planId);
  if (!bundle) return null;

  const onCycle = pathPlanIds.has(planId);
  const atCap = depth >= MAX_SUBPLAN_DEPTH;
  const omitNested = onCycle || atCap;

  const plan = planFieldsFromRow(bundle.plan);
  const taskDtos = mapTaskRows(bundle.tasks, bundle.assigneesByTask);
  const edgeDtos = mapEdgeRows(bundle.edges);

  let links: PlanLinkSnapshot[] = [];
  if (!omitNested) {
    const nextPath = new Set(pathPlanIds);
    nextPath.add(planId);
    links = bundle.links
      .slice()
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((link) => {
        const child = buildSnapshotFromBundle(link.childPlanId, bundles, nextPath, depth + 1);
        if (!child) return null;
        return {
          id: link.id,
          parentPlanId: link.parentPlanId,
          childPlanId: link.childPlanId,
          x: link.x,
          y: link.y,
          width: link.width,
          height: link.height,
          child,
        };
      })
      .filter((l): l is PlanLinkSnapshot => l !== null);
  }

  return { plan, tasks: taskDtos, edges: edgeDtos, links };
}

/** Capture a plan subtree for undo/redo (depth cap + path-only cycle guard). */
export async function snapshotPlan(
  planId: string,
  executor: DbExecutor = db,
): Promise<PlanSnapshot | null> {
  const bundles = await loadPlanSubtree(planId, executor);
  return buildSnapshotFromBundle(planId, bundles, new Set(), 0);
}

type SnapshotIndex = {
  plans: Map<string, string>;
  tasks: Map<string, string>;
  edges: Map<string, string>;
  links: Map<string, string>;
};

function indexSnapshot(snap: PlanSnapshot | null): SnapshotIndex {
  const plansMap = new Map<string, string>();
  const tasksMap = new Map<string, string>();
  const edgesMap = new Map<string, string>();
  const linksMap = new Map<string, string>();

  function walk(s: PlanSnapshot) {
    plansMap.set(s.plan.id, JSON.stringify(s.plan));
    for (const t of s.tasks) {
      tasksMap.set(t.id, JSON.stringify(t));
    }
    for (const e of s.edges) {
      edgesMap.set(e.id, JSON.stringify(e));
    }
    for (const l of s.links) {
      linksMap.set(
        l.id,
        JSON.stringify({
          id: l.id,
          parentPlanId: l.parentPlanId,
          childPlanId: l.childPlanId,
          x: l.x,
          y: l.y,
          width: l.width,
          height: l.height,
        }),
      );
      walk(l.child);
    }
  }

  if (snap) walk(snap);
  return { plans: plansMap, tasks: tasksMap, edges: edgesMap, links: linksMap };
}

function diffMaps(before: Map<string, string>, after: Map<string, string>): string[] {
  const changed = new Set<string>();
  for (const [id, json] of after) {
    if (before.get(id) !== json) changed.add(id);
  }
  for (const id of before.keys()) {
    if (!after.has(id)) changed.add(id);
  }
  return [...changed];
}

/** Detect which entities differ between two snapshots (for coalesce eligibility). */
export function changedEntityKeys(
  before: PlanSnapshot | null,
  after: PlanSnapshot | null,
): EntityChange[] {
  const b = indexSnapshot(before);
  const a = indexSnapshot(after);
  const changes: EntityChange[] = [];
  for (const id of diffMaps(b.plans, a.plans)) changes.push({ kind: "plan", id });
  for (const id of diffMaps(b.tasks, a.tasks)) changes.push({ kind: "task", id });
  for (const id of diffMaps(b.edges, a.edges)) changes.push({ kind: "edge", id });
  for (const id of diffMaps(b.links, a.links)) changes.push({ kind: "link", id });
  return changes;
}

/** True when `next` can merge into `latest` (same single coalesce target). */
export function canCoalesceHistory(
  latest: {
    planId: string | null;
    beforeState: HistoryPayload | null;
    afterState: HistoryPayload | null;
  },
  next: {
    planId: string | null;
    beforeState: HistoryPayload | null;
    afterState: HistoryPayload | null;
    coalesce?: CoalesceInput;
  },
): boolean {
  if (!next.coalesce) return false;
  if (latest.planId !== next.planId) return false;
  if (
    latest.beforeState?.kind !== "snapshot" ||
    latest.afterState?.kind !== "snapshot" ||
    next.beforeState?.kind !== "snapshot" ||
    next.afterState?.kind !== "snapshot"
  ) {
    return false;
  }

  const latestChanged = changedEntityKeys(
    latest.beforeState.plan,
    latest.afterState.plan,
  );
  const nextChanged = changedEntityKeys(next.beforeState.plan, next.afterState.plan);
  if (latestChanged.length !== 1 || nextChanged.length !== 1) return false;

  const l = latestChanged[0]!;
  const n = nextChanged[0]!;
  if (l.kind !== n.kind || l.id !== n.id) return false;

  if (next.coalesce.taskId) {
    return l.kind === "task" && l.id === next.coalesce.taskId;
  }
  if (next.coalesce.planId) {
    return l.kind === "plan" && l.id === next.coalesce.planId;
  }
  if (next.coalesce.linkId) {
    return l.kind === "link" && l.id === next.coalesce.linkId;
  }
  return false;
}

function collectPlanIds(snap: PlanSnapshot): Set<string> {
  const ids = new Set<string>();
  function walk(s: PlanSnapshot) {
    ids.add(s.plan.id);
    for (const link of s.links) walk(link.child);
  }
  walk(snap);
  return ids;
}

async function upsertPlanRow(
  tx: Parameters<Parameters<typeof db.transaction>[0]>[0],
  plan: PlanSnapshotPlan,
): Promise<void> {
  await tx
    .insert(plans)
    .values({
      id: plan.id,
      name: plan.name,
      description: plan.description,
      ownerKerberos: plan.ownerKerberos,
      visibility: plan.visibility,
      status: plan.status,
      effort: plan.effort,
      deadline: plan.deadline ? new Date(plan.deadline) : null,
      notes: plan.notes,
    })
    .onConflictDoUpdate({
      target: plans.id,
      set: {
        name: plan.name,
        description: plan.description,
        status: plan.status,
        effort: plan.effort,
        deadline: plan.deadline ? new Date(plan.deadline) : null,
        notes: plan.notes,
      },
    });
}

async function syncTasksForPlan(
  tx: Parameters<Parameters<typeof db.transaction>[0]>[0],
  planId: string,
  snapTasks: TaskDto[],
): Promise<void> {
  const existing = await tx.query.tasks.findMany({ where: eq(tasks.planId, planId) });
  const snapIds = new Set(snapTasks.map((t) => t.id));
  for (const row of existing) {
    if (!snapIds.has(row.id)) {
      await tx.delete(tasks).where(eq(tasks.id, row.id));
    }
  }
  for (const t of snapTasks) {
    await tx
      .insert(tasks)
      .values({
        id: t.id,
        planId: t.planId,
        title: t.title,
        status: t.status,
        notes: t.notes,
        deadline: t.deadline ? new Date(t.deadline) : null,
        effort: t.effort,
        x: t.x,
        y: t.y,
        width: t.width,
        height: t.height,
      })
      .onConflictDoUpdate({
        target: tasks.id,
        set: {
          title: t.title,
          status: t.status,
          notes: t.notes,
          deadline: t.deadline ? new Date(t.deadline) : null,
          effort: t.effort,
          x: t.x,
          y: t.y,
          width: t.width,
          height: t.height,
        },
      });
    await tx.delete(taskAssignees).where(eq(taskAssignees.taskId, t.id));
    if (t.assignees.length > 0) {
      await tx
        .insert(taskAssignees)
        .values(t.assignees.map((kerberos) => ({ taskId: t.id, kerberos })));
    }
  }
}

async function syncEdgesForPlan(
  tx: Parameters<Parameters<typeof db.transaction>[0]>[0],
  planId: string,
  snapEdges: EdgeDto[],
): Promise<void> {
  const existing = await tx.query.edges.findMany({ where: eq(edges.planId, planId) });
  const snapIds = new Set(snapEdges.map((e) => e.id));
  for (const row of existing) {
    if (!snapIds.has(row.id)) {
      await tx.delete(edges).where(eq(edges.id, row.id));
    }
  }
  for (const e of snapEdges) {
    await tx
      .insert(edges)
      .values({
        id: e.id,
        planId,
        fromKind: e.fromKind,
        fromId: e.fromId,
        toKind: e.toKind,
        toId: e.toId,
      })
      .onConflictDoUpdate({
        target: edges.id,
        set: {
          fromKind: e.fromKind,
          fromId: e.fromId,
          toKind: e.toKind,
          toId: e.toId,
        },
      });
  }
}

async function syncLinksForPlan(
  tx: Parameters<Parameters<typeof db.transaction>[0]>[0],
  planId: string,
  snapLinks: PlanLinkSnapshot[],
): Promise<string[]> {
  const existing = await tx.query.planLinks.findMany({
    where: eq(planLinks.parentPlanId, planId),
  });
  const snapIds = new Set(snapLinks.map((l) => l.id));
  const removedChildIds: string[] = [];
  for (const row of existing) {
    if (!snapIds.has(row.id)) {
      removedChildIds.push(row.childPlanId);
      await tx.delete(planLinks).where(eq(planLinks.id, row.id));
    }
  }
  for (const l of snapLinks) {
    await tx
      .insert(planLinks)
      .values({
        id: l.id,
        parentPlanId: l.parentPlanId,
        childPlanId: l.childPlanId,
        x: l.x,
        y: l.y,
        width: l.width,
        height: l.height,
      })
      .onConflictDoUpdate({
        target: planLinks.id,
        set: {
          x: l.x,
          y: l.y,
          width: l.width,
          height: l.height,
        },
      });
  }
  return removedChildIds;
}

/** Delete unreferenced child plans removed from the snapshot tree (handles diamond orphans). */
async function deleteOrphanedPlans(
  tx: Parameters<Parameters<typeof db.transaction>[0]>[0],
  treePlanIds: Set<string>,
  initialCandidates: string[],
): Promise<void> {
  let candidates = new Set(initialCandidates.filter((id) => !treePlanIds.has(id)));
  while (candidates.size > 0) {
    const batch = [...candidates];
    candidates = new Set();
    for (const planId of batch) {
      if (treePlanIds.has(planId)) continue;
      const refs = await tx.query.planLinks.findMany({
        where: eq(planLinks.childPlanId, planId),
      });
      if (refs.length > 0) continue;

      const outgoing = await tx.query.planLinks.findMany({
        where: eq(planLinks.parentPlanId, planId),
      });
      for (const link of outgoing) {
        if (!treePlanIds.has(link.childPlanId)) {
          candidates.add(link.childPlanId);
        }
      }
      await tx.delete(plans).where(eq(plans.id, planId));
    }
  }
}

type HistoryTx = Parameters<Parameters<typeof db.transaction>[0]>[0];

async function applyPlanSnapshotInTx(tx: HistoryTx, snap: PlanSnapshot): Promise<void> {
  const treePlanIds = collectPlanIds(snap);

  async function upsertTree(s: PlanSnapshot): Promise<void> {
    await upsertPlanRow(tx, s.plan);
    for (const link of s.links) {
      await upsertTree(link.child);
    }
  }

  async function syncTree(s: PlanSnapshot): Promise<string[]> {
    await syncTasksForPlan(tx, s.plan.id, s.tasks);
    await syncEdgesForPlan(tx, s.plan.id, s.edges);
    const removed = await syncLinksForPlan(tx, s.plan.id, s.links);
    const nestedRemoved: string[] = [...removed];
    for (const link of s.links) {
      nestedRemoved.push(...(await syncTree(link.child)));
    }
    return nestedRemoved;
  }

  await upsertTree(snap);
  const removedChildIds = await syncTree(snap);
  await deleteOrphanedPlans(tx, treePlanIds, removedChildIds);
}

/** Restore a plan subtree to an exact snapshot. Does not touch plans outside the tree. */
export async function applyPlanSnapshot(snap: PlanSnapshot): Promise<void> {
  await db.transaction(async (tx) => applyPlanSnapshotInTx(tx, snap));
}

async function applyGeometryPayload(tx: HistoryTx, payload: GeometryPayload): Promise<void> {
  for (const task of payload.tasks) {
    await tx
      .update(tasks)
      .set({ x: task.x, y: task.y, width: task.width, height: task.height })
      .where(eq(tasks.id, task.id));
  }
  for (const link of payload.links) {
    await tx
      .update(planLinks)
      .set({ x: link.x, y: link.y, width: link.width, height: link.height })
      .where(eq(planLinks.id, link.id));
  }
}

async function applyHistoryPayload(tx: HistoryTx, payload: HistoryPayload): Promise<void> {
  if (payload.kind === "snapshot") {
    await applyPlanSnapshotInTx(tx, payload.plan);
    return;
  }
  await applyGeometryPayload(tx, payload);
}

async function commitHistoryEntry(entry: {
  actorKerberos: string;
  label: string;
  planId: string;
  beforeState: HistoryPayload;
  afterState: HistoryPayload;
}): Promise<void> {
  const mine = eq(planHistory.actorKerberos, entry.actorKerberos);
  await db.delete(planHistory).where(and(mine, eq(planHistory.undone, true)));
  await db.insert(planHistory).values({
    actorKerberos: entry.actorKerberos,
    label: entry.label,
    planId: entry.planId,
    beforeState: entry.beforeState,
    afterState: entry.afterState,
    undone: false,
  });

  const kept = await db.query.planHistory.findMany({
    where: and(mine, eq(planHistory.undone, false)),
    orderBy: desc(planHistory.id),
    limit: HISTORY_LIMIT,
  });
  const keepIds = new Set(kept.map((h) => h.id));
  const allCommitted = await db.query.planHistory.findMany({
    where: and(mine, eq(planHistory.undone, false)),
  });
  const toDrop = allCommitted.filter((h) => !keepIds.has(h.id)).map((h) => h.id);
  if (toDrop.length > 0) {
    await db.delete(planHistory).where(inArray(planHistory.id, toDrop));
  }
}

async function latestUndoable(actorKerberos: string, executor: DbExecutor = db) {
  const [entry] = await executor.query.planHistory.findMany({
    where: and(eq(planHistory.actorKerberos, actorKerberos), eq(planHistory.undone, false)),
    orderBy: desc(planHistory.id),
    limit: 1,
  });
  return entry;
}

async function earliestRedoable(actorKerberos: string, executor: DbExecutor = db) {
  const [entry] = await executor.query.planHistory.findMany({
    where: and(eq(planHistory.actorKerberos, actorKerberos), eq(planHistory.undone, true)),
    orderBy: asc(planHistory.id),
    limit: 1,
  });
  return entry;
}

/**
 * Record a structural mutation in the actor's own history; clears their redo
 * branch. Merges a burst of edits to one entity into one step, but only when
 * nothing else changed in between (so undoing the merged step cannot revert
 * someone else's edit).
 */
export async function pushSnapshotHistory(entry: {
  actorKerberos: string;
  label: string;
  planId: string;
  beforeState: PlanSnapshot;
  afterState: PlanSnapshot;
  coalesce?: CoalesceInput;
}): Promise<void> {
  const beforePayload: SnapshotPayload = { kind: "snapshot", plan: entry.beforeState };
  const afterPayload: SnapshotPayload = { kind: "snapshot", plan: entry.afterState };

  if (entry.coalesce) {
    const latest = await latestUndoable(entry.actorKerberos);
    const latestAfter = latest ? asHistoryPayload(latest.afterState) : null;
    if (
      latest &&
      latestAfter?.kind === "snapshot" &&
      changedEntityKeys(latestAfter.plan, entry.beforeState).length === 0 &&
      canCoalesceHistory(
        {
          planId: latest.planId,
          beforeState: asHistoryPayload(latest.beforeState),
          afterState: latestAfter,
        },
        {
          planId: entry.planId,
          beforeState: beforePayload,
          afterState: afterPayload,
          coalesce: entry.coalesce,
        },
      )
    ) {
      await db
        .update(planHistory)
        .set({ label: entry.label, afterState: afterPayload })
        .where(eq(planHistory.id, latest.id));
      return;
    }
  }

  await commitHistoryEntry({
    actorKerberos: entry.actorKerberos,
    label: entry.label,
    planId: entry.planId,
    beforeState: beforePayload,
    afterState: afterPayload,
  });
}

/** Record a move/resize; one undo step per call (no coalesce). */
export async function pushGeometryHistory(entry: {
  actorKerberos: string;
  label: string;
  planId: string;
  before: GeometryPayload;
  after: GeometryPayload;
}): Promise<void> {
  await commitHistoryEntry({
    actorKerberos: entry.actorKerberos,
    label: entry.label,
    planId: entry.planId,
    beforeState: entry.before,
    afterState: entry.after,
  });
}

export async function getUndoRedoState(actorKerberos: string): Promise<UndoRedoState> {
  const undoEntry = await latestUndoable(actorKerberos);
  const redoEntry = await earliestRedoable(actorKerberos);
  return {
    canUndo: Boolean(undoEntry),
    canRedo: Boolean(redoEntry),
    undoLabel: undoEntry?.label ?? null,
    redoLabel: redoEntry?.label ?? null,
  };
}

function collectTaskIdsFromSnapshot(snap: PlanSnapshot): Set<string> {
  const ids = new Set<string>();
  function walk(s: PlanSnapshot) {
    for (const t of s.tasks) ids.add(t.id);
    for (const l of s.links) walk(l.child);
  }
  walk(snap);
  return ids;
}

function collectLinkIdsFromSnapshot(snap: PlanSnapshot): Set<string> {
  const ids = new Set<string>();
  function walk(s: PlanSnapshot) {
    for (const l of s.links) {
      ids.add(l.id);
      walk(l.child);
    }
  }
  walk(snap);
  return ids;
}

function diffPoseIds<T extends { id: string }>(before: T[], after: T[]): string[] {
  const beforeMap = new Map(before.map((item) => [item.id, JSON.stringify(item)]));
  const afterMap = new Map(after.map((item) => [item.id, JSON.stringify(item)]));
  const changed = new Set<string>();
  for (const [id, json] of afterMap) {
    if (beforeMap.get(id) !== json) changed.add(id);
  }
  for (const id of beforeMap.keys()) {
    if (!afterMap.has(id)) changed.add(id);
  }
  return [...changed];
}

function preferSurvivingIds(changedIds: string[], preferredIds: Set<string>): string[] {
  const surviving = changedIds.filter((id) => preferredIds.has(id));
  return surviving.length > 0 ? surviving : changedIds;
}

export function focusFromHistoryPayloads(
  before: HistoryPayload | null,
  after: HistoryPayload | null,
  direction: "undo" | "redo",
): { focusTaskIds: string[]; focusLinkIds: string[] } {
  const preferred = direction === "undo" ? before : after;
  const other = direction === "undo" ? after : before;

  if (before?.kind === "snapshot" && after?.kind === "snapshot") {
    const changes = changedEntityKeys(before.plan, after.plan);
    const taskChanges = changes.filter((c) => c.kind === "task").map((c) => c.id);
    const linkChanges = changes.filter((c) => c.kind === "link").map((c) => c.id);

    const preferredSnap = preferred?.kind === "snapshot" ? preferred.plan : null;
    const preferredTasks = preferredSnap ? collectTaskIdsFromSnapshot(preferredSnap) : new Set<string>();
    const preferredLinks = preferredSnap ? collectLinkIdsFromSnapshot(preferredSnap) : new Set<string>();

    return {
      focusTaskIds: preferSurvivingIds(taskChanges, preferredTasks),
      focusLinkIds: preferSurvivingIds(linkChanges, preferredLinks),
    };
  }

  if (before?.kind === "geometry" && after?.kind === "geometry") {
    const taskChanges = diffPoseIds(before.tasks, after.tasks);
    const linkChanges = diffPoseIds(before.links, after.links);
    const preferredTasks = new Set(
      (preferred?.kind === "geometry" ? preferred.tasks : other?.kind === "geometry" ? other.tasks : []).map(
        (t) => t.id,
      ),
    );
    const preferredLinks = new Set(
      (preferred?.kind === "geometry" ? preferred.links : other?.kind === "geometry" ? other.links : []).map(
        (l) => l.id,
      ),
    );
    return {
      focusTaskIds: preferSurvivingIds(taskChanges, preferredTasks),
      focusLinkIds: preferSurvivingIds(linkChanges, preferredLinks),
    };
  }

  return { focusTaskIds: [], focusLinkIds: [] };
}

/** What an undo or redo did, for the canvas and for live updates. */
export type HistoryStepResult = {
  planId: string | null;
  label: string;
  focusTaskIds: string[];
  focusLinkIds: string[];
  /** Every plan whose contents the step may have changed. */
  touchedPlanIds: string[];
};

/** Does the database still look exactly like `expected` for what this entry covers? */
async function matchesCurrentState(
  tx: HistoryTx,
  planId: string | null,
  expected: HistoryPayload,
): Promise<boolean> {
  if (expected.kind === "snapshot") {
    if (!planId) return false;
    const current = await snapshotPlan(planId, tx);
    return current !== null && changedEntityKeys(current, expected.plan).length === 0;
  }
  const taskIds = expected.tasks.map((t) => t.id);
  const linkIds = expected.links.map((l) => l.id);
  const taskRows = taskIds.length > 0 ? await tx.query.tasks.findMany({ where: inArray(tasks.id, taskIds) }) : [];
  const linkRows =
    linkIds.length > 0 ? await tx.query.planLinks.findMany({ where: inArray(planLinks.id, linkIds) }) : [];
  const pose = (r: GeometryTaskPose | GeometryLinkPose) => JSON.stringify([r.x, r.y, r.width, r.height]);
  const currentTasks = new Map(taskRows.map((r) => [r.id, pose(r)]));
  const currentLinks = new Map(linkRows.map((r) => [r.id, pose(r)]));
  return (
    expected.tasks.every((t) => currentTasks.get(t.id) === pose(t)) &&
    expected.links.every((l) => currentLinks.get(l.id) === pose(l))
  );
}

/** Lock keys for everything a step changes (edges are not lockable). */
function stepLockKeys(before: HistoryPayload, after: HistoryPayload): LockKey[] {
  if (before.kind === "snapshot" && after.kind === "snapshot") {
    const keys: LockKey[] = [];
    for (const change of changedEntityKeys(before.plan, after.plan)) {
      if (change.kind === "task") keys.push(taskKey(change.id));
      else if (change.kind === "link") keys.push(linkKey(change.id));
      else if (change.kind === "plan") keys.push(planKey(change.id));
    }
    return keys;
  }
  const poses = [before, after].filter((p): p is GeometryPayload => p.kind === "geometry");
  return [
    ...new Set(poses.flatMap((p) => [...p.tasks.map((t) => taskKey(t.id)), ...p.links.map((l) => linkKey(l.id))])),
  ];
}

function touchedPlanIds(planId: string | null, before: HistoryPayload, after: HistoryPayload): string[] {
  const ids = new Set<string>(planId ? [planId] : []);
  for (const payload of [before, after]) {
    if (payload.kind === "snapshot") for (const id of collectPlanIds(payload.plan)) ids.add(id);
  }
  return [...ids];
}

/**
 * Apply one of the viewer's own history steps, in `direction`. Refuses if the
 * affected items are locked by someone else, and refuses (dropping the entry)
 * if anyone changed them since: applying it would overwrite their work.
 */
async function performStep(
  viewer: Viewer,
  direction: "undo" | "redo",
): Promise<HistoryStepResult> {
  const conflict = await db.transaction(async (tx) => {
    const entry =
      direction === "undo"
        ? await latestUndoable(viewer.kerberos, tx)
        : await earliestRedoable(viewer.kerberos, tx);
    if (!entry) throw new Error(direction === "undo" ? "Nothing to undo." : "Nothing to redo.");

    const before = asHistoryPayload(entry.beforeState);
    const after = asHistoryPayload(entry.afterState);
    if (!before || !after) throw new Error(`History entry ${entry.id} has an invalid payload.`);
    const [expected, target] = direction === "undo" ? [after, before] : [before, after];

    assertUnlocked(viewer, stepLockKeys(before, after));
    if (!(await matchesCurrentState(tx, entry.planId, expected))) {
      await tx.delete(planHistory).where(eq(planHistory.id, entry.id));
      return entry.label;
    }

    const focus = focusFromHistoryPayloads(before, after, direction);
    await applyHistoryPayload(tx, target);
    await tx
      .update(planHistory)
      .set({ undone: direction === "undo" })
      .where(eq(planHistory.id, entry.id));
    const result: HistoryStepResult = {
      planId: entry.planId,
      label: entry.label,
      focusTaskIds: focus.focusTaskIds,
      focusLinkIds: focus.focusLinkIds,
      touchedPlanIds: touchedPlanIds(entry.planId, before, after),
    };
    return result;
  });
  if (typeof conflict === "string") {
    throw new Error(
      `Can't ${direction} "${conflict}": someone else has changed it since. It was removed from your history.`,
    );
  }
  return conflict;
}

export async function performUndo(viewer: Viewer): Promise<HistoryStepResult> {
  return performStep(viewer, "undo");
}

export async function performRedo(viewer: Viewer): Promise<HistoryStepResult> {
  return performStep(viewer, "redo");
}
