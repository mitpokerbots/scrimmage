"use server";

import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { db } from "@/db";
import {
  edges,
  planHistory,
  planLinks,
  plans,
  taskAssignees,
  tasks,
} from "@/db/schema";
import { getViewer, listPeople } from "@/lib/auth";
import { requirePlanAccess } from "./access";
import { buildAdjacency, findPath } from "./graph";
import {
  buildGeometryPayload,
  getUndoRedoState,
  performRedo,
  performUndo,
  pushGeometryHistory,
  pushSnapshotHistory,
  snapshotPlan,
  type CoalesceInput,
  type UndoRedoState,
} from "./history";
import {
  deleteEdgesTouchingNode,
  getPlanGraph,
  type PlanGraph,
  wouldCreatePlanAncestorCycle,
} from "./queries";
import {
  findHistoryRootPlanId,
  partitionEdgesForMove,
  partitionEdgesForPackage,
  remapPositionsNearAnchor,
  rewriteBoundaryEdgeToPlanLink,
  wouldMoveLinkIntoDescendant,
} from "./organize";
import { GRAPH_NODE_KINDS, TASK_EFFORTS, TASK_STATUSES } from "./types";

/**
 * Expected, user-facing failures return { ok: false, error } because Next.js
 * masks thrown server-action errors in production.
 */
export type ActionResult<T = undefined> =
  | { ok: true; data: T }
  | { ok: false; error: string };

function failure(error: unknown): { ok: false; error: string } {
  return { ok: false, error: error instanceof Error ? error.message : "Something went wrong." };
}

async function recordPlanHistory(
  planId: string,
  label: string,
  mutate: () => Promise<void>,
  coalesce?: CoalesceInput,
): Promise<void> {
  const before = await snapshotPlan(planId);
  if (!before) throw new Error("Plan not found.");
  await mutate();
  const after = await snapshotPlan(planId);
  if (!after) return;
  await pushSnapshotHistory({ label, planId, beforeState: before, afterState: after, coalesce });
}

function taskPositionLabel(
  positions: Array<{ width?: number; height?: number }>,
): string {
  if (positions.length === 1) {
    const pos = positions[0]!;
    if (pos.width !== undefined || pos.height !== undefined) return "Resize task";
    return "Move task";
  }
  return "Move tasks";
}

function planLinkPositionLabel(
  positions: Array<{ width?: number; height?: number }>,
): string {
  if (positions.length === 1) {
    const pos = positions[0]!;
    if (pos.width !== undefined || pos.height !== undefined) return "Resize sub-plan";
    return "Move sub-plan";
  }
  return "Move sub-plans";
}

function revalidatePlan(planId: string): void {
  revalidatePath("/");
  revalidatePath(`/plans/${planId}`);
}

export async function getUndoRedo(): Promise<ActionResult<UndoRedoState>> {
  try {
    await getViewer();
    const undoRedo = await getUndoRedoState();
    return { ok: true, data: undoRedo };
  } catch (error) {
    return failure(error);
  }
}

export async function undoChange(
  viewingPlanId: string,
): Promise<
  ActionResult<{
    graph: PlanGraph;
    undoRedo: UndoRedoState;
    focusPlanId: string | null;
    focusTaskIds: string[];
    focusLinkIds: string[];
  }>
> {
  try {
    const viewer = await getViewer();
    const planId = z.uuid().parse(viewingPlanId);
    await requirePlanAccess(planId, viewer, "read");

    const [entry] = await db.query.planHistory.findMany({
      where: eq(planHistory.undone, false),
      orderBy: desc(planHistory.id),
      limit: 1,
    });
    if (!entry) throw new Error("Nothing to undo.");
    if (entry.planId) {
      await requirePlanAccess(entry.planId, viewer, "write");
    }

    const undoResult = await performUndo(entry.id);
    const graph = await getPlanGraph(planId, viewer);
    const undoRedo = await getUndoRedoState();
    revalidatePlan(planId);
    if (undoResult.planId && undoResult.planId !== planId) {
      revalidatePath(`/plans/${undoResult.planId}`);
    }
    return {
      ok: true,
      data: {
        graph,
        undoRedo,
        focusPlanId: undoResult.planId,
        focusTaskIds: undoResult.focusTaskIds,
        focusLinkIds: undoResult.focusLinkIds,
      },
    };
  } catch (error) {
    return failure(error);
  }
}

export async function redoChange(
  viewingPlanId: string,
): Promise<
  ActionResult<{
    graph: PlanGraph;
    undoRedo: UndoRedoState;
    focusPlanId: string | null;
    focusTaskIds: string[];
    focusLinkIds: string[];
  }>
> {
  try {
    const viewer = await getViewer();
    const planId = z.uuid().parse(viewingPlanId);
    await requirePlanAccess(planId, viewer, "read");

    const [entry] = await db.query.planHistory.findMany({
      where: eq(planHistory.undone, true),
      orderBy: [asc(planHistory.id)],
      limit: 1,
    });
    if (!entry) throw new Error("Nothing to redo.");
    if (entry.planId) {
      await requirePlanAccess(entry.planId, viewer, "write");
    }

    const redoResult = await performRedo(entry.id);
    const graph = await getPlanGraph(planId, viewer);
    const undoRedo = await getUndoRedoState();
    revalidatePlan(planId);
    if (redoResult.planId && redoResult.planId !== planId) {
      revalidatePath(`/plans/${redoResult.planId}`);
    }
    return {
      ok: true,
      data: {
        graph,
        undoRedo,
        focusPlanId: redoResult.planId,
        focusTaskIds: redoResult.focusTaskIds,
        focusLinkIds: redoResult.focusLinkIds,
      },
    };
  } catch (error) {
    return failure(error);
  }
}

// --- Plans ---------------------------------------------------------------

const createPlanSchema = z.object({
  name: z.string().trim().min(1, "Plan name is required.").max(200),
  description: z.string().trim().max(2000).default(""),
});

export async function createPlan(
  input: z.input<typeof createPlanSchema>,
): Promise<ActionResult<{ planId: string }>> {
  try {
    const viewer = await getViewer();
    const parsed = createPlanSchema.parse(input);
    const [plan] = await db
      .insert(plans)
      .values({ ...parsed, ownerKerberos: viewer.kerberos })
      .returning({ id: plans.id });
    if (!plan) throw new Error("Plan insert returned no row.");
    revalidatePath("/");
    return { ok: true, data: { planId: plan.id } };
  } catch (error) {
    return failure(error);
  }
}

const updatePlanMetaSchema = z.object({
  planId: z.uuid(),
  patch: z.object({
    name: z.string().trim().min(1).max(200).optional(),
    description: z.string().trim().max(2000).optional(),
    status: z.enum(TASK_STATUSES).optional(),
    notes: z.string().max(20000).optional(),
    deadline: z.iso.datetime().nullable().optional(),
    effort: z.enum(TASK_EFFORTS).nullable().optional(),
  }),
});

export async function updatePlanMeta(
  input: z.input<typeof updatePlanMetaSchema>,
): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const parsed = updatePlanMetaSchema.parse(input);
    await requirePlanAccess(parsed.planId, viewer, "write");
    const { deadline, ...rest } = parsed.patch;
    await recordPlanHistory(
      parsed.planId,
      "Update plan",
      async () => {
        await db
          .update(plans)
          .set({
            ...rest,
            ...(deadline !== undefined ? { deadline: deadline ? new Date(deadline) : null } : {}),
          })
          .where(eq(plans.id, parsed.planId));
      },
      { planId: parsed.planId },
    );
    revalidatePlan(parsed.planId);
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

// --- Tasks ---------------------------------------------------------------

const createTaskSchema = z.object({
  planId: z.uuid(),
  title: z.string().trim().min(1, "Task title is required.").max(300),
  x: z.number().finite(),
  y: z.number().finite(),
});

export async function createTask(
  input: z.input<typeof createTaskSchema>,
): Promise<ActionResult<{ taskId: string }>> {
  try {
    const viewer = await getViewer();
    const parsed = createTaskSchema.parse(input);
    await requirePlanAccess(parsed.planId, viewer, "write");
    let taskId = "";
    await recordPlanHistory(parsed.planId, "Create task", async () => {
      const [task] = await db.insert(tasks).values(parsed).returning({ id: tasks.id });
      if (!task) throw new Error("Task insert returned no row.");
      taskId = task.id;
    });
    revalidatePath(`/plans/${parsed.planId}`);
    return { ok: true, data: { taskId } };
  } catch (error) {
    return failure(error);
  }
}

const updateTaskSchema = z.object({
  taskId: z.uuid(),
  patch: z.object({
    title: z.string().trim().min(1).max(300).optional(),
    status: z.enum(TASK_STATUSES).optional(),
    notes: z.string().max(20000).optional(),
    deadline: z.iso.datetime().nullable().optional(),
    effort: z.enum(TASK_EFFORTS).nullable().optional(),
  }),
});

export async function updateTask(
  input: z.input<typeof updateTaskSchema>,
): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const parsed = updateTaskSchema.parse(input);
    const task = await db.query.tasks.findFirst({ where: eq(tasks.id, parsed.taskId) });
    if (!task) throw new Error("Task not found.");
    await requirePlanAccess(task.planId, viewer, "write");
    const { deadline, ...rest } = parsed.patch;
    await recordPlanHistory(
      task.planId,
      "Update task",
      async () => {
        await db
          .update(tasks)
          .set({
            ...rest,
            ...(deadline !== undefined ? { deadline: deadline ? new Date(deadline) : null } : {}),
          })
          .where(eq(tasks.id, parsed.taskId));
      },
      { taskId: parsed.taskId },
    );
    revalidatePath(`/plans/${task.planId}`);
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

const taskSizeSchema = z.number().finite();

const positionsSchema = z.object({
  planId: z.uuid(),
  positions: z
    .array(
      z.object({
        taskId: z.uuid(),
        x: z.number().finite(),
        y: z.number().finite(),
        width: taskSizeSchema.min(120).optional(),
        height: taskSizeSchema.min(60).optional(),
      }),
    )
    .min(1),
});

/** Batch position write: used by drag-stop and resize persistence. */
export async function updateTaskPositions(
  input: z.input<typeof positionsSchema>,
): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const parsed = positionsSchema.parse(input);
    await requirePlanAccess(parsed.planId, viewer, "write");
    const taskIds = parsed.positions.map((p) => p.taskId);
    const beforeRows = await db.query.tasks.findMany({
      where: and(eq(tasks.planId, parsed.planId), inArray(tasks.id, taskIds)),
    });
    const before = buildGeometryPayload(
      beforeRows.map((row) => ({
        id: row.id,
        x: row.x,
        y: row.y,
        width: row.width,
        height: row.height,
      })),
      [],
    );

    await db.transaction(async (tx) => {
      for (const { taskId, x, y, width, height } of parsed.positions) {
        await tx
          .update(tasks)
          .set({
            x,
            y,
            ...(width !== undefined ? { width } : {}),
            ...(height !== undefined ? { height } : {}),
          })
          .where(and(eq(tasks.id, taskId), eq(tasks.planId, parsed.planId)));
      }
    });

    const afterRows = await db.query.tasks.findMany({
      where: and(eq(tasks.planId, parsed.planId), inArray(tasks.id, taskIds)),
    });
    const after = buildGeometryPayload(
      afterRows.map((row) => ({
        id: row.id,
        x: row.x,
        y: row.y,
        width: row.width,
        height: row.height,
      })),
      [],
    );

    await pushGeometryHistory({
      label: taskPositionLabel(parsed.positions),
      planId: parsed.planId,
      before,
      after,
    });
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

const setAssigneesSchema = z.object({
  taskId: z.uuid(),
  kerberos: z.array(z.string().trim().toLowerCase()),
});

export async function setTaskAssignees(
  input: z.input<typeof setAssigneesSchema>,
): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const parsed = setAssigneesSchema.parse(input);
    const task = await db.query.tasks.findFirst({ where: eq(tasks.id, parsed.taskId) });
    if (!task) throw new Error("Task not found.");
    await requirePlanAccess(task.planId, viewer, "write");
    // Only new assignees must be admins; someone who has since lost admin stays removable.
    const current = await db.query.taskAssignees.findMany({
      where: eq(taskAssignees.taskId, parsed.taskId),
    });
    const alreadyAssigned = new Set(current.map((row) => row.kerberos));
    const people = new Set((await listPeople()).map((p) => p.kerberos));
    const unknown = parsed.kerberos.filter((k) => !alreadyAssigned.has(k) && !people.has(k));
    if (unknown.length > 0) {
      throw new Error(`Not a scrimmage admin: ${unknown.join(", ")}`);
    }
    await recordPlanHistory(
      task.planId,
      "Set assignees",
      async () => {
        await db.transaction(async (tx) => {
          await tx.delete(taskAssignees).where(eq(taskAssignees.taskId, parsed.taskId));
          if (parsed.kerberos.length > 0) {
            await tx
              .insert(taskAssignees)
              .values(parsed.kerberos.map((k) => ({ taskId: parsed.taskId, kerberos: k })));
          }
        });
      },
      { taskId: parsed.taskId },
    );
    revalidatePath(`/plans/${task.planId}`);
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

export async function deleteTask(taskId: string): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const id = z.uuid().parse(taskId);
    const task = await db.query.tasks.findFirst({ where: eq(tasks.id, id) });
    if (!task) throw new Error("Task not found.");
    await requirePlanAccess(task.planId, viewer, "write");
    await recordPlanHistory(task.planId, "Delete task", async () => {
      await deleteEdgesTouchingNode(task.planId, id);
      await db.delete(tasks).where(eq(tasks.id, id));
    });
    revalidatePath(`/plans/${task.planId}`);
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

// --- Edges (dependencies) ------------------------------------------------

const createEdgeSchema = z
  .object({
    planId: z.uuid(),
    fromKind: z.enum(GRAPH_NODE_KINDS),
    fromId: z.uuid(),
    toKind: z.enum(GRAPH_NODE_KINDS),
    toId: z.uuid(),
  })
  .refine((e) => !(e.fromKind === e.toKind && e.fromId === e.toId), {
    message: "A node cannot depend on itself.",
  });

async function assertNodeOnPlan(
  planId: string,
  kind: "task" | "plan_link",
  id: string,
): Promise<string> {
  if (kind === "task") {
    const task = await db.query.tasks.findFirst({
      where: and(eq(tasks.id, id), eq(tasks.planId, planId)),
    });
    if (!task) throw new Error("Both endpoints must belong to this plan.");
    return task.title;
  }
  const link = await db.query.planLinks.findFirst({
    where: and(eq(planLinks.id, id), eq(planLinks.parentPlanId, planId)),
  });
  if (!link) throw new Error("Both endpoints must belong to this plan.");
  const child = await db.query.plans.findFirst({ where: eq(plans.id, link.childPlanId) });
  return child?.name ?? "Sub-plan";
}

export async function createEdge(
  input: z.input<typeof createEdgeSchema>,
): Promise<ActionResult<{ edgeId: string }>> {
  try {
    const viewer = await getViewer();
    const parsed = createEdgeSchema.parse(input);
    await requirePlanAccess(parsed.planId, viewer, "write");

    const fromLabel = await assertNodeOnPlan(parsed.planId, parsed.fromKind, parsed.fromId);
    const toLabel = await assertNodeOnPlan(parsed.planId, parsed.toKind, parsed.toId);
    const labels = new Map<string, string>([
      [parsed.fromId, fromLabel],
      [parsed.toId, toLabel],
    ]);

    const planEdges = await db.query.edges.findMany({ where: eq(edges.planId, parsed.planId) });
    const path = findPath(
      parsed.toId,
      parsed.fromId,
      buildAdjacency(planEdges.map((e) => ({ fromId: e.fromId, toId: e.toId }))),
    );
    if (path) {
      // Load titles for path nodes we don't already have.
      for (const id of path) {
        if (labels.has(id)) continue;
        const task = await db.query.tasks.findFirst({ where: eq(tasks.id, id) });
        if (task) {
          labels.set(id, task.title);
          continue;
        }
        const link = await db.query.planLinks.findFirst({ where: eq(planLinks.id, id) });
        if (link) {
          const child = await db.query.plans.findFirst({ where: eq(plans.id, link.childPlanId) });
          labels.set(id, child?.name ?? "Sub-plan");
        }
      }
      const titles = [...path, parsed.toId].map((id) => labels.get(id) ?? "?");
      return { ok: false, error: `That would create a cycle: ${titles.join(" → ")}` };
    }

    let edgeId = "";
    await recordPlanHistory(parsed.planId, "Add dependency", async () => {
      const [edge] = await db
        .insert(edges)
        .values({
          planId: parsed.planId,
          fromKind: parsed.fromKind,
          fromId: parsed.fromId,
          toKind: parsed.toKind,
          toId: parsed.toId,
        })
        .onConflictDoNothing()
        .returning({ id: edges.id });
      if (!edge) throw new Error("That dependency already exists.");
      edgeId = edge.id;
    });
    revalidatePath(`/plans/${parsed.planId}`);
    return { ok: true, data: { edgeId } };
  } catch (error) {
    if (error instanceof Error && error.message === "That dependency already exists.") {
      return { ok: false, error: error.message };
    }
    return failure(error);
  }
}

export async function deleteEdge(edgeId: string): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const id = z.uuid().parse(edgeId);
    const edge = await db.query.edges.findFirst({ where: eq(edges.id, id) });
    if (!edge) throw new Error("Dependency not found.");
    await requirePlanAccess(edge.planId, viewer, "write");
    await recordPlanHistory(edge.planId, "Remove dependency", async () => {
      await db.delete(edges).where(eq(edges.id, id));
    });
    revalidatePath(`/plans/${edge.planId}`);
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

// --- Sub-plans (plan links) ----------------------------------------------

const createSubPlanSchema = z.object({
  parentPlanId: z.uuid(),
  name: z.string().trim().min(1, "Sub-plan name is required.").max(200),
  x: z.number().finite(),
  y: z.number().finite(),
});

/** Create a child plan and embed it as a plan-link node on the parent canvas. */
export async function createSubPlan(
  input: z.input<typeof createSubPlanSchema>,
): Promise<ActionResult<{ planLinkId: string; childPlanId: string }>> {
  try {
    const viewer = await getViewer();
    const parsed = createSubPlanSchema.parse(input);
    await requirePlanAccess(parsed.parentPlanId, viewer, "write");

    let planLinkId = "";
    let childPlanId = "";
    await recordPlanHistory(parsed.parentPlanId, "Create sub-plan", async () => {
      const [child] = await db
        .insert(plans)
        .values({ name: parsed.name, ownerKerberos: viewer.kerberos, description: "" })
        .returning({ id: plans.id });
      if (!child) throw new Error("Sub-plan insert returned no row.");

      const [link] = await db
        .insert(planLinks)
        .values({
          parentPlanId: parsed.parentPlanId,
          childPlanId: child.id,
          x: parsed.x,
          y: parsed.y,
        })
        .returning({ id: planLinks.id });
      if (!link) throw new Error("Plan link insert returned no row.");
      planLinkId = link.id;
      childPlanId = child.id;
    });

    revalidatePlan(parsed.parentPlanId);
    return { ok: true, data: { planLinkId, childPlanId } };
  } catch (error) {
    return failure(error);
  }
}

const createPlanLinkSchema = z.object({
  parentPlanId: z.uuid(),
  childPlanId: z.uuid(),
  x: z.number().finite(),
  y: z.number().finite(),
});

/** Embed an existing plan as a node on the parent canvas. */
export async function createPlanLink(
  input: z.input<typeof createPlanLinkSchema>,
): Promise<ActionResult<{ planLinkId: string }>> {
  try {
    const viewer = await getViewer();
    const parsed = createPlanLinkSchema.parse(input);
    await requirePlanAccess(parsed.parentPlanId, viewer, "write");
    await requirePlanAccess(parsed.childPlanId, viewer, "read");

    if (await wouldCreatePlanAncestorCycle(parsed.parentPlanId, parsed.childPlanId)) {
      return { ok: false, error: "That would create a cycle in the plan hierarchy." };
    }

    let planLinkId = "";
    await recordPlanHistory(parsed.parentPlanId, "Link plan", async () => {
      const [link] = await db
        .insert(planLinks)
        .values({
          parentPlanId: parsed.parentPlanId,
          childPlanId: parsed.childPlanId,
          x: parsed.x,
          y: parsed.y,
        })
        .onConflictDoNothing()
        .returning({ id: planLinks.id });
      if (!link) throw new Error("That sub-plan is already on this canvas.");
      planLinkId = link.id;
    });

    revalidatePath(`/plans/${parsed.parentPlanId}`);
    return { ok: true, data: { planLinkId } };
  } catch (error) {
    if (error instanceof Error && error.message === "That sub-plan is already on this canvas.") {
      return { ok: false, error: error.message };
    }
    return failure(error);
  }
}

const planLinkPositionsSchema = z.object({
  planId: z.uuid(),
  positions: z
    .array(
      z.object({
        planLinkId: z.uuid(),
        x: z.number().finite(),
        y: z.number().finite(),
        width: z.number().finite().min(120).optional(),
        height: z.number().finite().min(80).optional(),
      }),
    )
    .min(1),
});

export async function updatePlanLinkPositions(
  input: z.input<typeof planLinkPositionsSchema>,
): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const parsed = planLinkPositionsSchema.parse(input);
    await requirePlanAccess(parsed.planId, viewer, "write");
    const linkIds = parsed.positions.map((p) => p.planLinkId);
    const beforeRows = await db.query.planLinks.findMany({
      where: and(eq(planLinks.parentPlanId, parsed.planId), inArray(planLinks.id, linkIds)),
    });
    const before = buildGeometryPayload(
      [],
      beforeRows.map((row) => ({
        id: row.id,
        x: row.x,
        y: row.y,
        width: row.width,
        height: row.height,
      })),
    );

    await db.transaction(async (tx) => {
      for (const { planLinkId, x, y, width, height } of parsed.positions) {
        await tx
          .update(planLinks)
          .set({
            x,
            y,
            ...(width !== undefined ? { width } : {}),
            ...(height !== undefined ? { height } : {}),
          })
          .where(
            and(eq(planLinks.id, planLinkId), eq(planLinks.parentPlanId, parsed.planId)),
          );
      }
    });

    const afterRows = await db.query.planLinks.findMany({
      where: and(eq(planLinks.parentPlanId, parsed.planId), inArray(planLinks.id, linkIds)),
    });
    const after = buildGeometryPayload(
      [],
      afterRows.map((row) => ({
        id: row.id,
        x: row.x,
        y: row.y,
        width: row.width,
        height: row.height,
      })),
    );

    await pushGeometryHistory({
      label: planLinkPositionLabel(parsed.positions),
      planId: parsed.planId,
      before,
      after,
    });
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

export async function deletePlanLink(planLinkId: string): Promise<ActionResult> {
  try {
    const viewer = await getViewer();
    const id = z.uuid().parse(planLinkId);
    const link = await db.query.planLinks.findFirst({ where: eq(planLinks.id, id) });
    if (!link) throw new Error("Sub-plan node not found.");
    await requirePlanAccess(link.parentPlanId, viewer, "write");
    await recordPlanHistory(link.parentPlanId, "Remove sub-plan", async () => {
      await deleteEdgesTouchingNode(link.parentPlanId, id);
      await db.delete(planLinks).where(eq(planLinks.id, id));
    });
    revalidatePath(`/plans/${link.parentPlanId}`);
    return { ok: true, data: undefined };
  } catch (error) {
    return failure(error);
  }
}

// --- Organize (move / package) -------------------------------------------

function buildPlanLinkAdjacency(links: Array<{ parentPlanId: string; childPlanId: string }>): {
  childPlanIdsByParent: Map<string, string[]>;
  parentPlanIdsByChild: Map<string, string[]>;
} {
  const childPlanIdsByParent = new Map<string, string[]>();
  const parentPlanIdsByChild = new Map<string, string[]>();
  for (const link of links) {
    const children = childPlanIdsByParent.get(link.parentPlanId) ?? [];
    children.push(link.childPlanId);
    childPlanIdsByParent.set(link.parentPlanId, children);

    const parents = parentPlanIdsByChild.get(link.childPlanId) ?? [];
    parents.push(link.parentPlanId);
    parentPlanIdsByChild.set(link.childPlanId, parents);
  }
  return { childPlanIdsByParent, parentPlanIdsByChild };
}

async function validateAndLoadNodesOnPlan(
  planId: string,
  taskIds: string[],
  planLinkIds: string[],
): Promise<{
  taskRows: Array<{ id: string; x: number; y: number }>;
  linkRows: Array<{ id: string; childPlanId: string; x: number; y: number }>;
}> {
  const taskRows =
    taskIds.length > 0
      ? await db.query.tasks.findMany({
          where: and(eq(tasks.planId, planId), inArray(tasks.id, taskIds)),
          columns: { id: true, x: true, y: true },
        })
      : [];
  if (taskRows.length !== taskIds.length) {
    throw new Error("One or more tasks are not on the source plan.");
  }

  const linkRows =
    planLinkIds.length > 0
      ? await db.query.planLinks.findMany({
          where: and(eq(planLinks.parentPlanId, planId), inArray(planLinks.id, planLinkIds)),
          columns: { id: true, childPlanId: true, x: true, y: true },
        })
      : [];
  if (linkRows.length !== planLinkIds.length) {
    throw new Error("One or more sub-plans are not on the source plan.");
  }

  return { taskRows, linkRows };
}

async function assertLinksCanReparentTo(
  targetPlanId: string,
  linkRows: Array<{ childPlanId: string }>,
  childMap: Map<string, string[]>,
): Promise<void> {
  for (const link of linkRows) {
    if (await wouldCreatePlanAncestorCycle(targetPlanId, link.childPlanId)) {
      throw new Error("That would create a cycle in the plan hierarchy.");
    }
    if (wouldMoveLinkIntoDescendant(targetPlanId, link.childPlanId, childMap)) {
      throw new Error("Cannot move a sub-plan into its own descendant.");
    }
  }
}

const organizeSelectionSchema = z
  .object({
    taskIds: z.array(z.uuid()),
    planLinkIds: z.array(z.uuid()),
  })
  .refine((d) => d.taskIds.length + d.planLinkIds.length > 0, {
    message: "Select at least one node to move.",
  });

const moveNodesToPlanSchema = organizeSelectionSchema.extend({
  sourcePlanId: z.uuid(),
  targetPlanId: z.uuid(),
  anchorX: z.number().finite().optional(),
  anchorY: z.number().finite().optional(),
});

/** Move tasks and embedded sub-plan links from one plan canvas to another. */
export async function moveNodesToPlan(
  input: z.input<typeof moveNodesToPlanSchema>,
): Promise<
  ActionResult<{ movedTasks: number; movedLinks: number; droppedEdges: number }>
> {
  try {
    const viewer = await getViewer();
    const parsed = moveNodesToPlanSchema.parse(input);
    if (parsed.sourcePlanId === parsed.targetPlanId) {
      return { ok: false, error: "Source and target must be different plans." };
    }
    await requirePlanAccess(parsed.sourcePlanId, viewer, "write");
    await requirePlanAccess(parsed.targetPlanId, viewer, "write");

    const { taskRows, linkRows } = await validateAndLoadNodesOnPlan(
      parsed.sourcePlanId,
      parsed.taskIds,
      parsed.planLinkIds,
    );
    const allLinks = await db.query.planLinks.findMany();
    const { childPlanIdsByParent, parentPlanIdsByChild } = buildPlanLinkAdjacency(allLinks);
    await assertLinksCanReparentTo(parsed.targetPlanId, linkRows, childPlanIdsByParent);

    const historyRoot = findHistoryRootPlanId(
      parsed.sourcePlanId,
      parsed.targetPlanId,
      parentPlanIdsByChild,
    );
    if (!historyRoot) {
      return {
        ok: false,
        error: "Source and target plans must share a common ancestor.",
      };
    }

    const movedTaskSet = new Set(parsed.taskIds);
    const movedLinkSet = new Set(parsed.planLinkIds);
    const anchor = { x: parsed.anchorX ?? 0, y: parsed.anchorY ?? 0 };
    const remapped = remapPositionsNearAnchor(
      [
        ...taskRows.map((t) => ({ id: t.id, x: t.x, y: t.y })),
        ...linkRows.map((l) => ({ id: l.id, x: l.x, y: l.y })),
      ],
      anchor,
    );

    const sourceEdges = await db.query.edges.findMany({
      where: eq(edges.planId, parsed.sourcePlanId),
    });
    const { migrate, drop } = partitionEdgesForMove(sourceEdges, movedTaskSet, movedLinkSet);

    let movedTasks = 0;
    let movedLinks = 0;
    const droppedEdges = drop.length;

    await recordPlanHistory(historyRoot, "Move nodes", async () => {
      await db.transaction(async (tx) => {
        for (const task of taskRows) {
          const pos = remapped.get(task.id)!;
          await tx
            .update(tasks)
            .set({ planId: parsed.targetPlanId, x: pos.x, y: pos.y })
            .where(eq(tasks.id, task.id));
          movedTasks += 1;
        }
        for (const link of linkRows) {
          const pos = remapped.get(link.id)!;
          await tx
            .update(planLinks)
            .set({ parentPlanId: parsed.targetPlanId, x: pos.x, y: pos.y })
            .where(eq(planLinks.id, link.id));
          movedLinks += 1;
        }
        if (migrate.length > 0) {
          await tx
            .update(edges)
            .set({ planId: parsed.targetPlanId })
            .where(inArray(edges.id, migrate.map((e) => e.id)));
        }
        if (drop.length > 0) {
          await tx.delete(edges).where(inArray(edges.id, drop.map((e) => e.id)));
        }
      });
    });

    revalidatePlan(parsed.sourcePlanId);
    revalidatePlan(parsed.targetPlanId);
    return { ok: true, data: { movedTasks, movedLinks, droppedEdges } };
  } catch (error) {
    return failure(error);
  }
}

const packageNodesIntoSubPlanSchema = organizeSelectionSchema.extend({
  parentPlanId: z.uuid(),
  name: z.string().trim().min(1, "Sub-plan name is required.").max(200),
  x: z.number().finite(),
  y: z.number().finite(),
});

/** Wrap a selection into a new sub-plan on the parent canvas. */
export async function packageNodesIntoSubPlan(
  input: z.input<typeof packageNodesIntoSubPlanSchema>,
): Promise<
  ActionResult<{ planLinkId: string; childPlanId: string; droppedEdges: number }>
> {
  try {
    const viewer = await getViewer();
    const parsed = packageNodesIntoSubPlanSchema.parse(input);
    await requirePlanAccess(parsed.parentPlanId, viewer, "write");

    const { taskRows, linkRows } = await validateAndLoadNodesOnPlan(
      parsed.parentPlanId,
      parsed.taskIds,
      parsed.planLinkIds,
    );

    const movedTaskSet = new Set(parsed.taskIds);
    const movedLinkSet = new Set(parsed.planLinkIds);
    const remapped = remapPositionsNearAnchor(
      [
        ...taskRows.map((t) => ({ id: t.id, x: t.x, y: t.y })),
        ...linkRows.map((l) => ({ id: l.id, x: l.x, y: l.y })),
      ],
      { x: 0, y: 0 },
    );

    const parentEdges = await db.query.edges.findMany({
      where: eq(edges.planId, parsed.parentPlanId),
    });
    const { migrate, rewrite } = partitionEdgesForPackage(
      parentEdges,
      movedTaskSet,
      movedLinkSet,
    );

    let planLinkId = "";
    let childPlanId = "";
    let droppedEdges = 0;

    await recordPlanHistory(parsed.parentPlanId, "Package into sub-plan", async () => {
      await db.transaction(async (tx) => {
        const [child] = await tx
          .insert(plans)
          .values({ name: parsed.name, ownerKerberos: viewer.kerberos, description: "" })
          .returning({ id: plans.id });
        if (!child) throw new Error("Sub-plan insert returned no row.");

        const [link] = await tx
          .insert(planLinks)
          .values({
            parentPlanId: parsed.parentPlanId,
            childPlanId: child.id,
            x: parsed.x,
            y: parsed.y,
          })
          .returning({ id: planLinks.id });
        if (!link) throw new Error("Plan link insert returned no row.");
        planLinkId = link.id;
        childPlanId = child.id;

        for (const task of taskRows) {
          const pos = remapped.get(task.id)!;
          await tx
            .update(tasks)
            .set({ planId: child.id, x: pos.x, y: pos.y })
            .where(eq(tasks.id, task.id));
        }
        for (const embedded of linkRows) {
          const pos = remapped.get(embedded.id)!;
          await tx
            .update(planLinks)
            .set({ parentPlanId: child.id, x: pos.x, y: pos.y })
            .where(eq(planLinks.id, embedded.id));
        }

        if (migrate.length > 0) {
          await tx
            .update(edges)
            .set({ planId: child.id })
            .where(inArray(edges.id, migrate.map((e) => e.id)));
        }

        for (const edge of rewrite) {
          const rewritten = rewriteBoundaryEdgeToPlanLink(
            edge,
            link.id,
            movedTaskSet,
            movedLinkSet,
          );
          await tx.delete(edges).where(eq(edges.id, edge.id));
          if (!rewritten) {
            droppedEdges += 1;
            continue;
          }
          const [inserted] = await tx
            .insert(edges)
            .values({
              planId: parsed.parentPlanId,
              fromKind: rewritten.fromKind,
              fromId: rewritten.fromId,
              toKind: rewritten.toKind,
              toId: rewritten.toId,
            })
            .onConflictDoNothing()
            .returning({ id: edges.id });
          if (!inserted) droppedEdges += 1;
        }
      });
    });

    revalidatePlan(parsed.parentPlanId);
    return { ok: true, data: { planLinkId, childPlanId, droppedEdges } };
  } catch (error) {
    return failure(error);
  }
}
