import { and, asc, eq, inArray, or } from "drizzle-orm";
import { db } from "@/db";
import { edges, planLinks, plans, taskAssignees, tasks } from "@/db/schema";
import type { Viewer } from "@/lib/auth";
import { requirePlanAccess } from "./access";
import { nestPlans } from "./plan-tree";
import type {
  EdgeDto,
  PlanMetaDto,
  PlanRollup,
  PlanSummary,
  PlanTreeNodeDto,
  SubPlanDto,
  TaskDto,
  TaskEffort,
  TaskStatus,
} from "./types";
import { computeRecursiveRollup, unionAssignees } from "./plan-scene";

/** Every plan: all admins see everything (see access.ts). */
export async function listPlans(_viewer: Viewer): Promise<PlanTreeNodeDto[]> {
  const visiblePlans = await db.query.plans.findMany({ orderBy: asc(plans.createdAt) });

  const summaries: PlanSummary[] = [];
  for (const plan of visiblePlans) {
    const planTasks = await db.query.tasks.findMany({ where: eq(tasks.planId, plan.id) });
    summaries.push({
      id: plan.id,
      name: plan.name,
      description: plan.description,
      ownerKerberos: plan.ownerKerberos,
      taskCount: planTasks.length,
      doneCount: planTasks.filter((t) => t.status === "done").length,
    });
  }

  if (summaries.length === 0) return [];

  const visibleIds = summaries.map((s) => s.id);
  const visibleLinks = await db.query.planLinks.findMany({
    where: and(
      inArray(planLinks.parentPlanId, visibleIds),
      inArray(planLinks.childPlanId, visibleIds),
    ),
  });

  return nestPlans(
    summaries,
    visibleLinks.map((link) => ({
      parentPlanId: link.parentPlanId,
      childPlanId: link.childPlanId,
    })),
  );
}

/** Max nesting depth below the root plan for recursive subtree expansion (BFS levels 0..8). */
export const MAX_SUBPLAN_DEPTH = 8;

type PlanRow = typeof plans.$inferSelect;
type TaskRow = typeof tasks.$inferSelect;
type EdgeRow = typeof edges.$inferSelect;
type LinkRow = typeof planLinks.$inferSelect;

export type LoadedPlanBundle = {
  plan: PlanRow;
  tasks: TaskRow[];
  edges: EdgeRow[];
  links: LinkRow[];
  assigneesByTask: Map<string, string[]>;
};

function planMetaFromRow(row: PlanRow): PlanMetaDto {
  return {
    status: row.status as TaskStatus,
    effort: row.effort as TaskEffort | null,
    deadline: row.deadline ? row.deadline.toISOString() : null,
    notes: row.notes,
  };
}

export function mapTaskRows(taskRows: TaskRow[], assigneesByTask: Map<string, string[]>): TaskDto[] {
  return taskRows.map((t) => ({
    id: t.id,
    planId: t.planId,
    title: t.title,
    status: t.status as TaskStatus,
    notes: t.notes,
    deadline: t.deadline ? t.deadline.toISOString() : null,
    effort: t.effort as TaskEffort | null,
    x: t.x,
    y: t.y,
    width: t.width,
    height: t.height,
    assignees: assigneesByTask.get(t.id) ?? [],
  }));
}

export function mapEdgeRows(edgeRows: EdgeRow[]): EdgeDto[] {
  return edgeRows.map((e) => ({
    id: e.id,
    fromKind: e.fromKind,
    fromId: e.fromId,
    toKind: e.toKind,
    toId: e.toId,
  }));
}

/**
 * BFS-load plan rows, tasks, edges, links, and assignees by depth level (batched inArray).
 * Stops expanding child plans below MAX_SUBPLAN_DEPTH; loaded rows at the cap remain available
 * for rollup/assignee derivation even when the tree builder omits nested contents.
 */
export async function loadPlanSubtree(rootPlanId: string): Promise<Map<string, LoadedPlanBundle>> {
  const bundles = new Map<string, LoadedPlanBundle>();
  let frontier = [rootPlanId];
  let depth = 0;
  const loaded = new Set<string>();

  while (frontier.length > 0 && depth <= MAX_SUBPLAN_DEPTH) {
    const batch = frontier.filter((id) => !loaded.has(id));
    if (batch.length === 0) break;
    for (const id of batch) loaded.add(id);

    const [planRows, taskRows, edgeRows, linkRows] = await Promise.all([
      db.query.plans.findMany({ where: inArray(plans.id, batch) }),
      db.query.tasks.findMany({ where: inArray(tasks.planId, batch), orderBy: asc(tasks.createdAt) }),
      db.query.edges.findMany({ where: inArray(edges.planId, batch) }),
      db.query.planLinks.findMany({ where: inArray(planLinks.parentPlanId, batch) }),
    ]);

    const taskIds = taskRows.map((t) => t.id);
    const assigneeRows =
      taskIds.length > 0
        ? await db.query.taskAssignees.findMany({ where: inArray(taskAssignees.taskId, taskIds) })
        : [];

    const assigneesByTask = new Map<string, string[]>();
    for (const row of assigneeRows) {
      assigneesByTask.set(row.taskId, [...(assigneesByTask.get(row.taskId) ?? []), row.kerberos]);
    }

    const tasksByPlan = new Map<string, TaskRow[]>();
    for (const row of taskRows) {
      tasksByPlan.set(row.planId, [...(tasksByPlan.get(row.planId) ?? []), row]);
    }
    const edgesByPlan = new Map<string, EdgeRow[]>();
    for (const row of edgeRows) {
      edgesByPlan.set(row.planId, [...(edgesByPlan.get(row.planId) ?? []), row]);
    }
    const linksByPlan = new Map<string, LinkRow[]>();
    for (const row of linkRows) {
      linksByPlan.set(row.parentPlanId, [...(linksByPlan.get(row.parentPlanId) ?? []), row]);
    }

    for (const planRow of planRows) {
      bundles.set(planRow.id, {
        plan: planRow,
        tasks: tasksByPlan.get(planRow.id) ?? [],
        edges: edgesByPlan.get(planRow.id) ?? [],
        links: linksByPlan.get(planRow.id) ?? [],
        assigneesByTask,
      });
    }

    if (depth >= MAX_SUBPLAN_DEPTH) break;

    const next = new Set<string>();
    for (const link of linkRows) {
      next.add(link.childPlanId);
    }
    frontier = [...next];
    depth += 1;
  }

  return bundles;
}

function buildSubPlanDto(
  link: LinkRow,
  bundles: Map<string, LoadedPlanBundle>,
  pathPlanIds: Set<string>,
  childDepth: number,
): SubPlanDto | null {
  const childBundle = bundles.get(link.childPlanId);
  if (!childBundle) return null;

  const onCycle = pathPlanIds.has(link.childPlanId);
  const atCap = childDepth >= MAX_SUBPLAN_DEPTH;
  const omitContents = onCycle || atCap;

  let tasks: TaskDto[] = [];
  let nestedEdges: EdgeDto[] = [];
  let subPlans: SubPlanDto[] = [];

  if (!omitContents) {
    tasks = mapTaskRows(childBundle.tasks, childBundle.assigneesByTask);
    nestedEdges = mapEdgeRows(childBundle.edges);
    const nextPath = new Set(pathPlanIds);
    nextPath.add(link.childPlanId);
    subPlans = childBundle.links
      .slice()
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((nestedLink) => buildSubPlanDto(nestedLink, bundles, nextPath, childDepth + 1))
      .filter((sp): sp is SubPlanDto => sp !== null);
  }

  const rollupSourceTasks =
    omitContents && !onCycle
      ? mapTaskRows(childBundle.tasks, childBundle.assigneesByTask)
      : tasks;
  const rollup = computeRecursiveRollup(
    rollupSourceTasks.map((t) => ({ status: t.status, deadline: t.deadline })),
    subPlans.map((sp) => sp.rollup),
  );

  const assigneeSourceTasks =
    omitContents && !onCycle
      ? mapTaskRows(childBundle.tasks, childBundle.assigneesByTask)
      : tasks;
  const assignees = unionAssignees(
    assigneeSourceTasks.map((t) => t.assignees),
    subPlans.map((sp) => sp.assignees),
  );

  return {
    linkId: link.id,
    parentPlanId: link.parentPlanId,
    childPlanId: link.childPlanId,
    name: childBundle.plan.name,
    description: childBundle.plan.description,
    x: link.x,
    y: link.y,
    width: link.width,
    height: link.height,
    assignees,
    rollup,
    tasks,
    edges: nestedEdges,
    subPlans,
    ...planMetaFromRow(childBundle.plan),
  };
}

function buildRootSubPlans(
  rootBundle: LoadedPlanBundle,
  bundles: Map<string, LoadedPlanBundle>,
): SubPlanDto[] {
  const path = new Set([rootBundle.plan.id]);
  return rootBundle.links
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((link) => buildSubPlanDto(link, bundles, path, 1))
    .filter((sp): sp is SubPlanDto => sp !== null);
}

function rootRollup(tasks: TaskDto[], subPlans: SubPlanDto[]): PlanRollup {
  return computeRecursiveRollup(
    tasks.map((t) => ({ status: t.status, deadline: t.deadline })),
    subPlans.map((sp) => sp.rollup),
  );
}

function rootAssignees(rootTasks: TaskDto[], subPlans: SubPlanDto[]): string[] {
  return unionAssignees(
    rootTasks.map((t) => t.assignees),
    subPlans.map((sp) => sp.assignees),
  );
}
export type PlanGraph = {
  plan: {
    id: string;
    name: string;
    description: string;
    ownerKerberos: string;
  } & PlanMetaDto & { assignees: string[]; rollup: PlanRollup };
  tasks: TaskDto[];
  edges: EdgeDto[];
  subPlans: SubPlanDto[];
  /** True when upward parent-plan prerequisites targeting this plan are incomplete. */
  externallyBlocked: boolean;
};

async function resolveEdgeSourceStatuses(
  parentPlanId: string,
  incomingEdges: EdgeRow[],
): Promise<Map<string, TaskStatus>> {
  const statuses = new Map<string, TaskStatus>();
  if (incomingEdges.length === 0) return statuses;

  const taskIds = incomingEdges.filter((e) => e.fromKind === "task").map((e) => e.fromId);
  if (taskIds.length > 0) {
    const taskRows = await db.query.tasks.findMany({
      where: and(eq(tasks.planId, parentPlanId), inArray(tasks.id, taskIds)),
    });
    for (const row of taskRows) {
      statuses.set(row.id, row.status as TaskStatus);
    }
  }

  const linkIds = incomingEdges.filter((e) => e.fromKind === "plan_link").map((e) => e.fromId);
  if (linkIds.length > 0) {
    const linkRows = await db.query.planLinks.findMany({
      where: and(eq(planLinks.parentPlanId, parentPlanId), inArray(planLinks.id, linkIds)),
    });
    const childPlanIds = linkRows.map((l) => l.childPlanId);
    const childPlans =
      childPlanIds.length > 0
        ? await db.query.plans.findMany({ where: inArray(plans.id, childPlanIds) })
        : [];
    const childById = new Map(childPlans.map((p) => [p.id, p]));
    for (const link of linkRows) {
      const child = childById.get(link.childPlanId);
      statuses.set(link.id, (child?.status ?? "todo") as TaskStatus);
    }
  }

  return statuses;
}

async function parentLinkHasIncompletePrereqs(link: LinkRow): Promise<boolean> {
  const incomingEdges = await db.query.edges.findMany({
    where: and(
      eq(edges.planId, link.parentPlanId),
      eq(edges.toKind, "plan_link"),
      eq(edges.toId, link.id),
    ),
  });
  if (incomingEdges.length === 0) return false;

  const statuses = await resolveEdgeSourceStatuses(link.parentPlanId, incomingEdges);
  for (const edge of incomingEdges) {
    if (statuses.get(edge.fromId) !== "done") return true;
  }
  return false;
}

async function checkExternalBlockUpward(planId: string, seen: Set<string>): Promise<boolean> {
  if (seen.has(planId)) return false;
  seen.add(planId);

  const parentLinks = await db.query.planLinks.findMany({
    where: eq(planLinks.childPlanId, planId),
  });

  for (const link of parentLinks) {
    if (await parentLinkHasIncompletePrereqs(link)) return true;
    if (await checkExternalBlockUpward(link.parentPlanId, seen)) return true;
  }

  return false;
}

/** BFS upward: blocked when a parent-plan edge into this plan's link has an incomplete source. */
export async function planIsExternallyBlocked(planId: string): Promise<boolean> {
  return checkExternalBlockUpward(planId, new Set());
}

export async function getPlanGraph(planId: string, viewer: Viewer): Promise<PlanGraph> {
  const plan = await requirePlanAccess(planId, viewer, "read");
  // Child plans in the subtree load without per-child access checks (same posture as preview).

  const rootBundleMap = await loadPlanSubtree(planId);

  const rootBundle = rootBundleMap.get(planId);
  if (!rootBundle) {
    throw new Error("Plan not found.");
  }

  const tasks = mapTaskRows(rootBundle.tasks, rootBundle.assigneesByTask);
  const subPlans = buildRootSubPlans(rootBundle, rootBundleMap);
  const externallyBlocked = await planIsExternallyBlocked(planId);

  return {
    plan: {
      id: plan.id,
      name: plan.name,
      description: plan.description,
      ownerKerberos: plan.ownerKerberos,
      ...planMetaFromRow(plan),
      assignees: rootAssignees(tasks, subPlans),
      rollup: rootRollup(tasks, subPlans),
    },
    tasks,
    edges: mapEdgeRows(rootBundle.edges),
    subPlans,
    externallyBlocked,
  };
}

/** True if linking `candidateChildId` under `parentPlanId` would cycle the plan tree. */
export async function wouldCreatePlanAncestorCycle(
  parentPlanId: string,
  candidateChildId: string,
): Promise<boolean> {
  if (parentPlanId === candidateChildId) return true;
  const queue = [parentPlanId];
  const seen = new Set<string>();
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || seen.has(current)) continue;
    seen.add(current);
    if (current === candidateChildId) return true;
    const parents = await db.query.planLinks.findMany({
      where: eq(planLinks.childPlanId, current),
    });
    for (const link of parents) {
      queue.push(link.parentPlanId);
    }
  }
  return false;
}

/** Delete dependency edges that reference a removed plan_link id on a plan. */
export async function deleteEdgesTouchingNode(planId: string, nodeId: string): Promise<void> {
  await db
    .delete(edges)
    .where(
      and(eq(edges.planId, planId), or(eq(edges.fromId, nodeId), eq(edges.toId, nodeId))),
    );
}
