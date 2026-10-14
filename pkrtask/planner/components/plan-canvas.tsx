"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  PanOnScrollMode,
  SelectionMode,
  getViewportForBounds,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  useStoreApi,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type OnConnect,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/button";
import { useRegisterHistoryDock } from "@/components/history-dock";
import { BASE_PATH } from "@/lib/base-path";
import { replaceAppUrl } from "@/lib/history-restore";
import {
  createEdge,
  createSubPlan,
  createTask,
  deleteEdge,
  deletePlanLink,
  deleteTask,
  getUndoRedo,
  moveNodesToPlan,
  packageNodesIntoSubPlan,
  redoChange,
  setTaskAssignees,
  loadPlan,
  sharePlan,
  undoChange,
  updatePlanLinkPositions,
  updatePlanMeta,
  updateTask,
  updateTaskPositions,
} from "../actions";
import type { UndoRedoState } from "../history";
import {
  applyStartableOnlyFilter,
  computeRecursiveRollup,
  containerAffine,
  flattenScene,
  nonStartableSceneIds,
  applyExternalBlock,
  refitVisibleFrameContents,
  unionAssignees,
  sceneTaskId,
  visibleSceneIds,
  type Scene,
  type SceneFrame,
  type ScenePath,
  type SceneTask,
} from "../plan-scene";
import {
  TASK_NODE_HEIGHT,
  TASK_NODE_WIDTH,
  applyAffine,
  invertAffine,
  matchViewportAcrossAffine,
  type Affine,
  type Point,
  type Rect,
  type Viewport,
} from "../plan-focus";
import { wouldMoveLinkIntoDescendant } from "../organize";
import type { PlanGraph } from "../queries";
import { EMPTY_FILTERS, taskMatchesFilters, type TaskFilters } from "../task-filters";
import type { Person } from "@/lib/auth";
import type { EdgeDto, GraphNodeKind, SubPlanDto, TaskDto } from "../types";
import {
  DEFAULT_CANVAS_SETTINGS,
  panScrollSpeed,
  readCanvasSettings,
  writeCanvasSettings,
  type CanvasSettings,
} from "../canvas-settings";
import { CanvasSettingsPanel } from "./canvas-settings-panel";
import { FilterBar, readStoredFilters, writeStoredFilters } from "./filter-bar";
import { editorColor } from "./editing-outline";
import type { ChangedEvent, LockKey, LockView } from "@/realtime/protocol";
import { useRealtime } from "@/realtime/use-realtime";
import { OrganizeBar, type OrganizeDestination } from "./organize-bar";
import { SubPlanFrame, type SubPlanFrameNode, PlanCanvasActionsContext } from "./sub-plan-frame";
import { SubPlanInspector, type SubPlanPatch } from "./sub-plan-inspector";
import { TaskInspector, type TaskPatch } from "./task-inspector";
import { CanvasPrefsContext } from "./focus-point";
import { TaskNode, type TaskFlowNode } from "./task-node";
import { useFocusPoints } from "./use-focus-points";

type FlowNode = TaskFlowNode | SubPlanFrameNode;

const nodeTypes = { task: TaskNode, subPlanFrame: SubPlanFrame };

const MIN_ZOOM = 0.02;
const DEFAULT_MAX_ZOOM = 8;
const ABSOLUTE_MAX_ZOOM = 64;
function pathsEqual(a: ScenePath, b: ScenePath): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

function assigneesEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((kerberos, i) => kerberos === b[i]);
}

function rollupEqual(a: { total: number; done: number; blocked: number; nearestDeadline: string | null }, b: typeof a): boolean {
  return a.total === b.total && a.done === b.done && a.blocked === b.blocked && a.nearestDeadline === b.nearestDeadline;
}

/** Keep the same array when every item comes back unchanged, so untouched branches stay referentially equal. */
function mapIfChanged<T>(items: readonly T[], map: (item: T) => T): T[] {
  let changed = false;
  const next = items.map((item) => {
    const mapped = map(item);
    if (mapped !== item) changed = true;
    return mapped;
  });
  return changed ? next : (items as T[]);
}

function pathStartsWith(path: ScenePath, prefix: ScenePath): boolean {
  if (prefix.length > path.length) return false;
  return prefix.every((id, i) => path[i] === id);
}

function patchTaskList(tasks: TaskDto[], taskId: string, patch: Partial<TaskDto>): TaskDto[] {
  const index = tasks.findIndex((t) => t.id === taskId);
  if (index < 0) return tasks;
  const next = tasks.slice();
  next[index] = { ...tasks[index]!, ...patch };
  return next;
}

function patchTasksInSubPlans(
  subPlans: SubPlanDto[],
  taskId: string,
  patch: Partial<TaskDto>,
): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const tasks = patchTaskList(sp.tasks, taskId, patch);
    const nested = patchTasksInSubPlans(sp.subPlans, taskId, patch);
    if (tasks === sp.tasks && nested === sp.subPlans) return sp;
    return { ...sp, tasks, subPlans: nested };
  });
}

function patchTaskEverywhere(graph: PlanGraph, taskId: string, patch: Partial<TaskDto>): PlanGraph {
  const tasks = patchTaskList(graph.tasks, taskId, patch);
  const subPlans = patchTasksInSubPlans(graph.subPlans, taskId, patch);
  if (tasks === graph.tasks && subPlans === graph.subPlans) return graph;
  return recomputeGraphDerived({ ...graph, tasks, subPlans });
}

function recomputeSubPlanDerived(subPlans: SubPlanDto[]): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const nested = recomputeSubPlanDerived(sp.subPlans);
    const rollup = computeRecursiveRollup(
      sp.tasks.map((t) => ({ status: t.status, deadline: t.deadline })),
      nested.map((s) => s.rollup),
    );
    const assignees = unionAssignees(
      sp.tasks.map((t) => t.assignees),
      nested.map((s) => s.assignees),
    );
    if (nested === sp.subPlans && rollupEqual(rollup, sp.rollup) && assigneesEqual(assignees, sp.assignees)) {
      return sp;
    }
    return { ...sp, subPlans: nested, rollup, assignees };
  });
}

function recomputeGraphDerived(graph: PlanGraph): PlanGraph {
  const subPlans = recomputeSubPlanDerived(graph.subPlans);
  const assignees = unionAssignees(
    graph.tasks.map((t) => t.assignees),
    subPlans.map((sp) => sp.assignees),
  );
  const rollup = computeRecursiveRollup(
    graph.tasks.map((t) => ({ status: t.status, deadline: t.deadline })),
    subPlans.map((sp) => sp.rollup),
  );
  const planSame = assigneesEqual(assignees, graph.plan.assignees) && rollupEqual(rollup, graph.plan.rollup);
  if (subPlans === graph.subPlans && planSame) return graph;
  return {
    ...graph,
    subPlans,
    plan: planSame ? graph.plan : { ...graph.plan, assignees, rollup },
  };
}

function patchSubPlanMetaByChildId(
  subPlans: SubPlanDto[],
  childPlanId: string,
  patch: SubPlanPatch,
): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const nested = patchSubPlanMetaByChildId(sp.subPlans, childPlanId, patch);
    if (sp.childPlanId !== childPlanId && nested === sp.subPlans) return sp;
    return { ...(sp.childPlanId === childPlanId ? { ...sp, ...patch } : sp), subPlans: nested };
  });
}

function patchPlanMetaEverywhere(graph: PlanGraph, childPlanId: string, patch: SubPlanPatch): PlanGraph {
  const plan = graph.plan.id === childPlanId ? { ...graph.plan, ...patch } : graph.plan;
  const subPlans = patchSubPlanMetaByChildId(graph.subPlans, childPlanId, patch);
  if (plan === graph.plan && subPlans === graph.subPlans) return graph;
  return { ...graph, plan, subPlans };
}

function moveTaskList(tasks: TaskDto[], taskId: string, x: number, y: number): TaskDto[] {
  const index = tasks.findIndex((t) => t.id === taskId);
  if (index < 0) return tasks;
  const current = tasks[index]!;
  if (current.x === x && current.y === y) return tasks;
  const next = tasks.slice();
  next[index] = { ...current, x, y };
  return next;
}

function updateTaskPosInSubPlans(
  subPlans: SubPlanDto[],
  taskId: string,
  planId: string,
  x: number,
  y: number,
): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const tasks = sp.childPlanId === planId ? moveTaskList(sp.tasks, taskId, x, y) : sp.tasks;
    const nested = updateTaskPosInSubPlans(sp.subPlans, taskId, planId, x, y);
    if (tasks === sp.tasks && nested === sp.subPlans) return sp;
    return { ...sp, tasks, subPlans: nested };
  });
}

function updateTaskPositionEverywhere(
  graph: PlanGraph,
  taskId: string,
  planId: string,
  x: number,
  y: number,
): PlanGraph {
  const tasks = planId === graph.plan.id ? moveTaskList(graph.tasks, taskId, x, y) : graph.tasks;
  const subPlans = updateTaskPosInSubPlans(graph.subPlans, taskId, planId, x, y);
  if (tasks === graph.tasks && subPlans === graph.subPlans) return graph;
  return { ...graph, tasks, subPlans };
}

function updateFrameByLinkId(
  subPlans: SubPlanDto[],
  linkId: string,
  patch: Partial<Pick<SubPlanDto, "x" | "y" | "width" | "height">>,
): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const nested = updateFrameByLinkId(sp.subPlans, linkId, patch);
    if (sp.linkId !== linkId && nested === sp.subPlans) return sp;
    return { ...(sp.linkId === linkId ? { ...sp, ...patch } : sp), subPlans: nested };
  });
}

function updateFrameEverywhere(
  graph: PlanGraph,
  linkId: string,
  patch: Partial<Pick<SubPlanDto, "x" | "y" | "width" | "height">>,
): PlanGraph {
  const subPlans = updateFrameByLinkId(graph.subPlans, linkId, patch);
  if (subPlans === graph.subPlans) return graph;
  return { ...graph, subPlans };
}

function insertTaskIntoSubPlans(subPlans: SubPlanDto[], planId: string, task: TaskDto): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const nested = insertTaskIntoSubPlans(sp.subPlans, planId, task);
    if (sp.childPlanId !== planId && nested === sp.subPlans) return sp;
    return {
      ...sp,
      tasks: sp.childPlanId === planId ? [...sp.tasks, task] : sp.tasks,
      subPlans: nested,
    };
  });
}

function insertTaskIntoPlan(graph: PlanGraph, planId: string, task: TaskDto): PlanGraph {
  if (planId === graph.plan.id) {
    return recomputeGraphDerived({ ...graph, tasks: [...graph.tasks, task] });
  }
  const subPlans = insertTaskIntoSubPlans(graph.subPlans, planId, task);
  if (subPlans === graph.subPlans) return graph;
  return recomputeGraphDerived({ ...graph, subPlans });
}

function insertSubPlanIntoSubPlans(
  subPlans: SubPlanDto[],
  parentPlanId: string,
  subPlan: SubPlanDto,
): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const nested = insertSubPlanIntoSubPlans(sp.subPlans, parentPlanId, subPlan);
    if (sp.childPlanId !== parentPlanId && nested === sp.subPlans) return sp;
    const base = nested === sp.subPlans ? sp.subPlans : nested;
    return {
      ...sp,
      subPlans: sp.childPlanId === parentPlanId ? [...base, subPlan] : base,
    };
  });
}

type PlanScope = Pick<SubPlanDto, "visibility" | "ownerKerberos">;

/** Visibility and owner of a plan shown on this canvas (the root or any nested sub-plan). */
function scopeOfPlan(graph: PlanGraph, planId: string): PlanScope | null {
  if (graph.plan.id === planId) return graph.plan;
  const pending = [...graph.subPlans];
  while (pending.length > 0) {
    const sp = pending.pop();
    if (!sp) continue;
    if (sp.childPlanId === planId) return sp;
    pending.push(...sp.subPlans);
  }
  return null;
}

function visibilityOfPlan(graph: PlanGraph, planId: string): SubPlanDto["visibility"] | null {
  return scopeOfPlan(graph, planId)?.visibility ?? null;
}

/** Every plan whose contents this canvas shows. */
function planIdsInGraph(graph: PlanGraph): Set<string> {
  const ids = new Set([graph.plan.id]);
  const pending = [...graph.subPlans];
  while (pending.length > 0) {
    const sp = pending.pop();
    if (!sp) continue;
    ids.add(sp.childPlanId);
    pending.push(...sp.subPlans);
  }
  return ids;
}

/** What selecting (or dragging) these nodes locks: tasks, and sub-plans with their details. */
function lockKeysForSceneIds(scene: Scene, sceneIds: Iterable<string>): LockKey[] {
  const keys: LockKey[] = [];
  for (const id of sceneIds) {
    const task = scene.tasks.find((t) => t.sceneId === id);
    if (task) {
      keys.push(`task:${task.task.id}`);
      continue;
    }
    const frame = scene.frames.find((f) => f.sceneId === id);
    if (frame) keys.push(`link:${frame.linkId}`, `plan:${frame.subPlan.childPlanId}`);
  }
  return keys;
}

function frameEditor(locks: ReadonlyMap<LockKey, LockView>, frame: SceneFrame): LockView | null {
  return locks.get(`link:${frame.linkId}`) ?? locks.get(`plan:${frame.subPlan.childPlanId}`) ?? null;
}

function insertSubPlanIntoPlan(graph: PlanGraph, parentPlanId: string, subPlan: SubPlanDto): PlanGraph {
  if (parentPlanId === graph.plan.id) {
    return { ...graph, subPlans: [...graph.subPlans, subPlan] };
  }
  const subPlans = insertSubPlanIntoSubPlans(graph.subPlans, parentPlanId, subPlan);
  if (subPlans === graph.subPlans) return graph;
  return { ...graph, subPlans };
}

function removeTaskFromSubPlans(subPlans: SubPlanDto[], taskId: string): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const tasks = sp.tasks.some((t) => t.id === taskId) ? sp.tasks.filter((t) => t.id !== taskId) : sp.tasks;
    const edges = sp.edges.some((e) => e.fromId === taskId || e.toId === taskId)
      ? sp.edges.filter((e) => e.fromId !== taskId && e.toId !== taskId)
      : sp.edges;
    const nested = removeTaskFromSubPlans(sp.subPlans, taskId);
    if (tasks === sp.tasks && edges === sp.edges && nested === sp.subPlans) return sp;
    return { ...sp, tasks, edges, subPlans: nested };
  });
}

function removeTaskEverywhere(graph: PlanGraph, taskId: string): PlanGraph {
  const tasks = graph.tasks.some((t) => t.id === taskId) ? graph.tasks.filter((t) => t.id !== taskId) : graph.tasks;
  const edges = graph.edges.some((e) => e.fromId === taskId || e.toId === taskId)
    ? graph.edges.filter((e) => e.fromId !== taskId && e.toId !== taskId)
    : graph.edges;
  const subPlans = removeTaskFromSubPlans(graph.subPlans, taskId);
  if (tasks === graph.tasks && edges === graph.edges && subPlans === graph.subPlans) return graph;
  return recomputeGraphDerived({ ...graph, tasks, edges, subPlans });
}

function removeSubPlanByLinkId(subPlans: SubPlanDto[], linkId: string): SubPlanDto[] {
  const removed = subPlans.some((sp) => sp.linkId === linkId);
  const kept = removed ? subPlans.filter((sp) => sp.linkId !== linkId) : subPlans;
  return mapIfChanged(kept, (sp) => {
    const nested = removeSubPlanByLinkId(sp.subPlans, linkId);
    if (nested === sp.subPlans) return sp;
    return { ...sp, subPlans: nested };
  });
}

function removeEdgesTouchingLinkInSubPlans(subPlans: SubPlanDto[], linkId: string): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const edges = sp.edges.some((e) => e.fromId === linkId || e.toId === linkId)
      ? sp.edges.filter((e) => e.fromId !== linkId && e.toId !== linkId)
      : sp.edges;
    const nested = removeEdgesTouchingLinkInSubPlans(sp.subPlans, linkId);
    if (edges === sp.edges && nested === sp.subPlans) return sp;
    return { ...sp, edges, subPlans: nested };
  });
}

function removeSubPlanEverywhere(graph: PlanGraph, linkId: string): PlanGraph {
  const subPlans = removeEdgesTouchingLinkInSubPlans(removeSubPlanByLinkId(graph.subPlans, linkId), linkId);
  const edges = graph.edges.some((e) => e.fromId === linkId || e.toId === linkId)
    ? graph.edges.filter((e) => e.fromId !== linkId && e.toId !== linkId)
    : graph.edges;
  if (subPlans === graph.subPlans && edges === graph.edges) return graph;
  return recomputeGraphDerived({ ...graph, subPlans, edges });
}

function addEdgeInSubPlans(subPlans: SubPlanDto[], planId: string, edge: EdgeDto): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const nested = addEdgeInSubPlans(sp.subPlans, planId, edge);
    if (sp.childPlanId !== planId && nested === sp.subPlans) return sp;
    return {
      ...sp,
      edges: sp.childPlanId === planId ? [...sp.edges, edge] : sp.edges,
      subPlans: nested,
    };
  });
}

function addEdgeEverywhere(graph: PlanGraph, planId: string, edge: EdgeDto): PlanGraph {
  if (planId === graph.plan.id) return { ...graph, edges: [...graph.edges, edge] };
  const subPlans = addEdgeInSubPlans(graph.subPlans, planId, edge);
  if (subPlans === graph.subPlans) return graph;
  return { ...graph, subPlans };
}

function removeEdgeInSubPlans(subPlans: SubPlanDto[], edgeId: string): SubPlanDto[] {
  return mapIfChanged(subPlans, (sp) => {
    const edges = sp.edges.some((e) => e.id === edgeId) ? sp.edges.filter((e) => e.id !== edgeId) : sp.edges;
    const nested = removeEdgeInSubPlans(sp.subPlans, edgeId);
    if (edges === sp.edges && nested === sp.subPlans) return sp;
    return { ...sp, edges, subPlans: nested };
  });
}

function removeEdgeEverywhere(graph: PlanGraph, edgeId: string): PlanGraph {
  const edges = graph.edges.some((e) => e.id === edgeId) ? graph.edges.filter((e) => e.id !== edgeId) : graph.edges;
  const subPlans = removeEdgeInSubPlans(graph.subPlans, edgeId);
  if (edges === graph.edges && subPlans === graph.subPlans) return graph;
  return { ...graph, edges, subPlans };
}

function resolveFocusPath(focusParam: string | null | undefined, graph: PlanGraph): ScenePath {
  if (!focusParam) return [];
  const ids = focusParam
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  let subPlans = graph.subPlans;
  const valid: string[] = [];
  for (const linkId of ids) {
    const sp = subPlans.find((s) => s.linkId === linkId);
    if (!sp) return [];
    valid.push(linkId);
    subPlans = sp.subPlans;
  }
  return valid;
}

function validateFocusPath(graph: PlanGraph, focusPath: ScenePath): ScenePath {
  let subPlans = graph.subPlans;
  const valid: string[] = [];
  for (const linkId of focusPath) {
    const sp = subPlans.find((s) => s.linkId === linkId);
    if (!sp) break;
    valid.push(linkId);
    subPlans = sp.subPlans;
  }
  return valid;
}

function resolveFocusedPlanId(graph: PlanGraph, focusPath: ScenePath): string {
  if (focusPath.length === 0) return graph.plan.id;
  let subPlans = graph.subPlans;
  let planId = graph.plan.id;
  for (const linkId of focusPath) {
    const sp = subPlans.find((s) => s.linkId === linkId);
    if (!sp) return planId;
    planId = sp.childPlanId;
    subPlans = sp.subPlans;
  }
  return planId;
}

function findSubPlanByFocusPath(graph: PlanGraph, focusPath: ScenePath): SubPlanDto | null {
  if (focusPath.length === 0) return null;
  let subPlans = graph.subPlans;
  let current: SubPlanDto | null = null;
  for (const linkId of focusPath) {
    const sp = subPlans.find((s) => s.linkId === linkId);
    if (!sp) return current;
    current = sp;
    subPlans = sp.subPlans;
  }
  return current;
}

/** Direct tasks/frames/edges of the focused plan, plus the plan id for position writes. */
type FocusedContainer = {
  ownerPlanId: string;
  tasks: TaskDto[];
  subPlans: SubPlanDto[];
  edges: EdgeDto[];
};

function getFocusedContainer(graph: PlanGraph, focusPath: ScenePath): FocusedContainer {
  const focused = findSubPlanByFocusPath(graph, focusPath);
  if (!focused) {
    return {
      ownerPlanId: graph.plan.id,
      tasks: graph.tasks,
      subPlans: graph.subPlans,
      edges: graph.edges,
    };
  }
  return {
    ownerPlanId: focused.childPlanId,
    tasks: focused.tasks,
    subPlans: focused.subPlans,
    edges: focused.edges,
  };
}

function collectPlanDestinations(graph: PlanGraph): OrganizeDestination[] {
  const out: OrganizeDestination[] = [{ planId: graph.plan.id, label: graph.plan.name }];

  function walk(subPlans: SubPlanDto[], prefix: string) {
    for (const sp of subPlans) {
      const label = `${prefix} › ${sp.name}`;
      out.push({ planId: sp.childPlanId, label });
      walk(sp.subPlans, label);
    }
  }

  walk(graph.subPlans, graph.plan.name);
  return out;
}

function buildChildPlanIdsByParent(graph: PlanGraph): Map<string, string[]> {
  const map = new Map<string, string[]>();

  function walk(subPlans: SubPlanDto[], parentPlanId: string) {
    for (const sp of subPlans) {
      const existing = map.get(parentPlanId);
      if (existing) existing.push(sp.childPlanId);
      else map.set(parentPlanId, [sp.childPlanId]);
      walk(sp.subPlans, sp.childPlanId);
    }
  }

  walk(graph.subPlans, graph.plan.id);
  return map;
}

function childPlanIdsForSelectedLinks(graph: PlanGraph, planLinkIds: readonly string[]): string[] {
  const wanted = new Set(planLinkIds);
  const ids: string[] = [];

  function walk(subPlans: SubPlanDto[]) {
    for (const sp of subPlans) {
      if (wanted.has(sp.linkId)) ids.push(sp.childPlanId);
      walk(sp.subPlans);
    }
  }

  walk(graph.subPlans);
  return ids;
}

function filterMoveDestinations(
  destinations: OrganizeDestination[],
  sourcePlanId: string,
  selectedLinkIds: readonly string[],
  childMap: Map<string, string[]>,
  graph: PlanGraph,
): OrganizeDestination[] {
  const linkChildIds = childPlanIdsForSelectedLinks(graph, selectedLinkIds);
  return destinations.filter((dest) => {
    if (dest.planId === sourcePlanId) return false;
    for (const linkChildId of linkChildIds) {
      if (wouldMoveLinkIntoDescendant(dest.planId, linkChildId, childMap)) return false;
    }
    return true;
  });
}

function selectionAnchorCanonical(
  scene: Scene,
  focusPath: ScenePath,
  selectedIds: ReadonlySet<string>,
): { x: number; y: number } {
  let minX = Infinity;
  let minY = Infinity;
  let found = false;

  for (const sceneId of selectedIds) {
    const task = findSceneTask(scene, sceneId);
    if (task && pathsEqual(task.path, focusPath)) {
      minX = Math.min(minX, task.x);
      minY = Math.min(minY, task.y);
      found = true;
      continue;
    }
    const frame = findSceneFrame(scene, sceneId);
    if (frame && pathsEqual(frame.path, focusPath)) {
      minX = Math.min(minX, frame.x);
      minY = Math.min(minY, frame.y);
      found = true;
    }
  }

  if (!found) return { x: 0, y: 0 };
  return { x: minX, y: minY };
}

function resolveOrganizeIds(
  scene: Scene,
  focusPath: ScenePath,
  selectedIds: ReadonlySet<string>,
): { taskIds: string[]; planLinkIds: string[] } {
  const taskIds: string[] = [];
  const planLinkIds: string[] = [];

  for (const sceneId of selectedIds) {
    const task = findSceneTask(scene, sceneId);
    if (task && pathsEqual(task.path, focusPath)) {
      taskIds.push(task.task.id);
      continue;
    }
    const frame = findSceneFrame(scene, sceneId);
    if (frame && pathsEqual(frame.path, focusPath)) {
      planLinkIds.push(frame.linkId);
    }
  }

  return { taskIds, planLinkIds };
}

function focusBreadcrumbs(graph: PlanGraph, focusPath: ScenePath): { name: string; path: ScenePath }[] {
  const crumbs: { name: string; path: ScenePath }[] = [
    { name: graph.plan.name, path: [] },
  ];
  let subPlans = graph.subPlans;
  let path: ScenePath = [];
  for (const linkId of focusPath) {
    const sp = subPlans.find((s) => s.linkId === linkId);
    if (!sp) break;
    path = [...path, linkId];
    crumbs.push({ name: sp.name, path: [...path] });
    subPlans = sp.subPlans;
  }
  return crumbs;
}

function nodeZIndex(path: ScenePath, isFrame: boolean): number {
  return path.length * 2 + (isFrame ? 0 : 1);
}

function endpointScale(scene: Scene, sceneId: string): number {
  const task = scene.tasks.find((t) => t.sceneId === sceneId);
  if (task) return task.scale;
  const frame = scene.frames.find((f) => f.sceneId === sceneId);
  if (frame) return frame.scale;
  return 1;
}

function flowToWorld(pos: Point, focusAffine: Affine | null): Point {
  return focusAffine ? applyAffine(pos, focusAffine) : pos;
}

function computeMaxZoom(scene: Scene, focusPath: ScenePath, focusAffine: Affine | null): number {
  const focusScale = focusAffine?.scale ?? 1;
  const { visible } = visibleSceneIds(scene, focusPath);
  let smallestViewScale = 1;

  for (const task of scene.tasks) {
    if (visible.has(task.sceneId) && pathsEqual(task.path, focusPath)) {
      smallestViewScale = Math.min(smallestViewScale, task.scale / focusScale);
    }
  }
  for (const frame of scene.frames) {
    if (visible.has(frame.sceneId) && pathsEqual(frame.path, focusPath)) {
      smallestViewScale = Math.min(smallestViewScale, frame.scale / focusScale);
    }
  }

  return Math.min(
    ABSOLUTE_MAX_ZOOM,
    Math.max(DEFAULT_MAX_ZOOM, 4 / Math.min(1, smallestViewScale)),
  );
}

function projectToView(
  worldX: number,
  worldY: number,
  worldW: number,
  worldH: number,
  worldScale: number,
  focusAffine: Affine | null,
): { x: number; y: number; width: number; height: number; scale: number } {
  const focusScale = focusAffine?.scale ?? 1;
  const inv = focusAffine ? invertAffine(focusAffine) : null;
  const p = inv ? applyAffine({ x: worldX, y: worldY }, inv) : { x: worldX, y: worldY };
  return {
    x: p.x,
    y: p.y,
    width: worldW / focusScale,
    height: worldH / focusScale,
    scale: worldScale / focusScale,
  };
}

function nodeWorldSize(node: FlowNode): { w: number; h: number } | null {
  const style = node.style;
  if (style?.width != null && style?.height != null) {
    const w = typeof style.width === "number" ? style.width : Number.parseFloat(String(style.width));
    const h = typeof style.height === "number" ? style.height : Number.parseFloat(String(style.height));
    if (Number.isFinite(w) && Number.isFinite(h)) return { w, h };
  }
  if (node.width != null && node.height != null) {
    return { w: node.width, h: node.height };
  }
  if (node.measured?.width != null && node.measured?.height != null) {
    return { w: node.measured.width, h: node.measured.height };
  }
  return null;
}

const SIZE_EPSILON = 0.5;

function sizesMatch(a: { w: number; h: number }, b: { w: number; h: number }): boolean {
  return Math.abs(a.w - b.w) < SIZE_EPSILON && Math.abs(a.h - b.h) < SIZE_EPSILON;
}

function editorSame(
  a: { kerberos: string; name: string } | null,
  b: { kerberos: string; name: string } | null,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.kerberos === b.kerberos && a.name === b.name;
}

function taskDtoSame(a: TaskDto, b: TaskDto): boolean {
  if (a === b) return true;
  return (
    a.id === b.id &&
    a.planId === b.planId &&
    a.title === b.title &&
    a.status === b.status &&
    a.notes === b.notes &&
    a.deadline === b.deadline &&
    a.effort === b.effort &&
    a.x === b.x &&
    a.y === b.y &&
    a.width === b.width &&
    a.height === b.height &&
    assigneesEqual(a.assignees, b.assignees)
  );
}

function frameChromeSame(a: SubPlanDto, b: SubPlanDto): boolean {
  if (a === b) return true;
  return (
    a.linkId === b.linkId &&
    a.name === b.name &&
    a.status === b.status &&
    rollupEqual(a.rollup, b.rollup) &&
    assigneesEqual(a.assignees, b.assignees)
  );
}

function flowNodeSame(built: FlowNode, existing: FlowNode): boolean {
  if (built.type !== existing.type) return false;
  if (
    built.hidden !== existing.hidden ||
    built.draggable !== existing.draggable ||
    built.selectable !== existing.selectable ||
    built.selected !== existing.selected ||
    built.zIndex !== existing.zIndex ||
    built.position.x !== existing.position.x ||
    built.position.y !== existing.position.y
  ) {
    return false;
  }
  const builtSize = nodeWorldSize(built);
  const existingSize = nodeWorldSize(existing);
  if (!builtSize || !existingSize || !sizesMatch(builtSize, existingSize)) return false;

  if (built.type === "task" && existing.type === "task") {
    const a = built.data;
    const b = existing.data;
    return (
      taskDtoSame(a.task, b.task) &&
      a.scale === b.scale &&
      pathsEqual(a.path, b.path) &&
      a.ownerPlanId === b.ownerPlanId &&
      a.resizable === b.resizable &&
      a.startable === b.startable &&
      a.focused === b.focused &&
      a.dimmed === b.dimmed &&
      a.onToggleFocus === b.onToggleFocus &&
      editorSame(a.editor, b.editor)
    );
  }
  if (built.type === "subPlanFrame" && existing.type === "subPlanFrame") {
    const a = built.data;
    const b = existing.data;
    return (
      frameChromeSame(a.subPlan, b.subPlan) &&
      a.linkId === b.linkId &&
      pathsEqual(a.path, b.path) &&
      a.scale === b.scale &&
      a.chromeless === b.chromeless &&
      a.resizable === b.resizable &&
      a.focused === b.focused &&
      a.onToggleFocus === b.onToggleFocus &&
      editorSame(a.editor, b.editor)
    );
  }
  return false;
}

function withMeasured(node: FlowNode, existing: FlowNode | undefined): FlowNode {
  if (!existing) return node;
  const builtSize = nodeWorldSize(node);
  const existingSize = nodeWorldSize(existing);
  if (builtSize && existingSize && sizesMatch(builtSize, existingSize)) {
    return {
      ...node,
      measured: existing.measured,
      width: existing.width,
      height: existing.height,
    };
  }
  return {
    ...node,
    width: builtSize?.w,
    height: builtSize?.h,
    measured: undefined,
  };
}

function mergeFlowNodes(built: FlowNode[], prev: FlowNode[]): FlowNode[] {
  if (built.length !== prev.length && prev.length === 0) return built;
  const prevById = new Map(prev.map((n) => [n.id, n]));
  let changed = built.length !== prev.length;
  const next = built.map((node, i) => {
    const existing = prevById.get(node.id);
    if (existing && flowNodeSame(node, existing)) {
      if (prev[i] !== existing) changed = true;
      return existing;
    }
    changed = true;
    return withMeasured(node, existing);
  });
  return changed ? next : prev;
}

function edgeSame(a: Edge, b: Edge): boolean {
  if (a.source !== b.source || a.target !== b.target || a.hidden !== b.hidden) return false;
  if (a.style?.strokeWidth !== b.style?.strokeWidth) return false;
  const am = a.markerEnd;
  const bm = b.markerEnd;
  if (!am || !bm || typeof am !== "object" || typeof bm !== "object") return am === bm;
  return am.width === bm.width && am.height === bm.height;
}

function mergeFlowEdges(built: Edge[], prev: Edge[]): Edge[] {
  if (built.length !== prev.length) return built;
  let changed = false;
  const prevById = new Map(prev.map((edge) => [edge.id, edge]));
  const next = built.map((edge, i) => {
    const existing = prevById.get(edge.id);
    if (existing && edgeSame(existing, edge)) {
      if (prev[i] !== existing) changed = true;
      return existing;
    }
    changed = true;
    return edge;
  });
  return changed ? next : prev;
}

function resolveExitSelection(
  prevFocusPath: ScenePath,
  nextFocusPath: ScenePath,
  scene: Scene,
): string | null {
  if (prevFocusPath.length <= nextFocusPath.length) return null;

  const exitedLinkId = prevFocusPath[nextFocusPath.length];
  if (!exitedLinkId) return null;

  const frame = scene.frames.find(
    (f) => f.linkId === exitedLinkId && pathsEqual(f.path, nextFocusPath),
  );
  return frame?.sceneId ?? null;
}

function unionFlowRects(rects: Rect[]): Rect | null {
  if (rects.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const rect of rects) {
    minX = Math.min(minX, rect.x);
    minY = Math.min(minY, rect.y);
    maxX = Math.max(maxX, rect.x + rect.width);
    maxY = Math.max(maxY, rect.y + rect.height);
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function boundsOfVisibleFlowNodes(nodes: FlowNode[], chromeless: Set<string>): Rect | null {
  const rects: Rect[] = [];
  for (const node of nodes) {
    if (node.hidden) continue;
    if (node.type === "subPlanFrame" && chromeless.has(node.id)) continue;
    const size = nodeWorldSize(node);
    if (!size) continue;
    rects.push({
      x: node.position.x,
      y: node.position.y,
      width: size.w,
      height: size.h,
    });
  }
  return unionFlowRects(rects);
}

function viewportForVisibleNodes(
  nodes: FlowNode[],
  chromeless: Set<string>,
  width: number,
  height: number,
  maxZoom: number,
): Viewport | null {
  const bounds = boundsOfVisibleFlowNodes(nodes, chromeless);
  if (!bounds || bounds.width <= 0 || bounds.height <= 0) return null;
  if (width <= 0 || height <= 0) return null;
  return getViewportForBounds(bounds, width, height, MIN_ZOOM, maxZoom, 0.15);
}

function buildFlowNodes(
  scene: Scene,
  focusPath: ScenePath,
  focusAffine: Affine | null,
  visible: Set<string>,
  chromeless: Set<string>,
  selectedSceneIds: ReadonlySet<string>,
  startableOnly: boolean,
  externallyBlocked: boolean,
  focusedIds: ReadonlySet<string>,
  onToggleFocus: (nodeId: string) => void,
  filters: TaskFilters,
  viewerKerberos: string,
  lockedByOthers: ReadonlyMap<LockKey, LockView>,
): FlowNode[] {
  const nodes: FlowNode[] = [];
  const nonStartable = applyExternalBlock(nonStartableSceneIds(scene), scene, externallyBlocked);

  for (const frame of scene.frames) {
    const hidden = !visible.has(frame.sceneId);
    const isChromeless = chromeless.has(frame.sceneId);
    const atFocusLevel = pathsEqual(frame.path, focusPath);
    const view = projectToView(frame.x, frame.y, frame.width, frame.height, frame.scale, focusAffine);
    const editor = isChromeless ? null : frameEditor(lockedByOthers, frame);
    nodes.push({
      id: frame.sceneId,
      type: "subPlanFrame",
      position: { x: view.x, y: view.y },
      style: { width: view.width, height: view.height },
      zIndex: nodeZIndex(frame.path, true),
      hidden,
      draggable: !hidden && !isChromeless && !startableOnly && !editor,
      selectable: !hidden && !isChromeless,
      selected: selectedSceneIds.has(frame.sceneId),
      data: {
        subPlan: frame.subPlan,
        linkId: frame.linkId,
        path: frame.path,
        scale: view.scale,
        chromeless: isChromeless,
        resizable: atFocusLevel && !isChromeless && !startableOnly && !editor,
        focused: !isChromeless && focusedIds.has(frame.linkId),
        onToggleFocus,
        editor,
      },
    });
  }

  for (const task of scene.tasks) {
    const hidden = !visible.has(task.sceneId);
    const atFocusLevel = pathsEqual(task.path, focusPath);
    const view = projectToView(task.x, task.y, task.width, task.height, task.scale, focusAffine);
    const editor = lockedByOthers.get(`task:${task.task.id}`) ?? null;
    nodes.push({
      id: task.sceneId,
      type: "task",
      position: { x: view.x, y: view.y },
      style: { width: view.width, height: view.height },
      zIndex: nodeZIndex(task.path, false),
      hidden,
      draggable: !hidden && !startableOnly && !editor,
      selectable: !hidden,
      selected: selectedSceneIds.has(task.sceneId),
      data: {
        task: task.task,
        scale: view.scale,
        path: task.path,
        ownerPlanId: task.ownerPlanId,
        resizable: atFocusLevel && !startableOnly && !editor,
        startable: !nonStartable.has(task.sceneId),
        focused: focusedIds.has(task.task.id),
        onToggleFocus,
        dimmed: !taskMatchesFilters(task.task, filters, viewerKerberos),
        editor,
      },
    });
  }

  nodes.sort((a, b) => (a.zIndex ?? 0) - (b.zIndex ?? 0));
  return nodes;
}

function buildFlowEdges(scene: Scene, visible: Set<string>, focusAffine: Affine | null): Edge[] {
  const focusScale = focusAffine?.scale ?? 1;
  return scene.edges.map((edge) => {
    const edgeScale = Math.min(
      endpointScale(scene, edge.sourceSceneId) / focusScale,
      endpointScale(scene, edge.targetSceneId) / focusScale,
    );
    const strokeWidth = Math.max(1e-6, 1.5 * edgeScale);
    const markerSize = Math.max(1e-6, 18 * edgeScale);
    return {
      id: edge.sceneId,
      source: edge.sourceSceneId,
      target: edge.targetSceneId,
      type: "default" as const,
      hidden: !visible.has(edge.sceneId),
      data: { edgeId: edge.edgeId, ownerPlanId: edge.ownerPlanId, path: edge.path },
      style: { strokeWidth },
      markerEnd: { type: MarkerType.ArrowClosed, width: markerSize, height: markerSize },
    };
  });
}

function endpointFromNode(node: FlowNode | undefined): { kind: GraphNodeKind; id: string } | null {
  if (!node) return null;
  if (node.type === "task") return { kind: "task", id: node.data.task.id };
  if (node.type === "subPlanFrame") return { kind: "plan_link", id: node.data.linkId };
  return null;
}

function findSceneTask(scene: Scene, sceneId: string): SceneTask | undefined {
  return scene.tasks.find((t) => t.sceneId === sceneId);
}

function findSceneFrame(scene: Scene, sceneId: string): SceneFrame | undefined {
  return scene.frames.find((f) => f.sceneId === sceneId);
}

function sceneIdsFromFocus(
  scene: Scene,
  focusTaskIds: readonly string[],
  focusLinkIds: readonly string[],
): Set<string> {
  const ids = new Set<string>();
  const taskIdSet = new Set(focusTaskIds);
  const linkIdSet = new Set(focusLinkIds);
  for (const task of scene.tasks) {
    if (taskIdSet.has(task.task.id)) ids.add(task.sceneId);
  }
  for (const frame of scene.frames) {
    if (linkIdSet.has(frame.linkId)) ids.add(frame.sceneId);
  }
  return ids;
}

function focusHref(planId: string, focusPath: ScenePath): string {
  if (focusPath.length === 0) return `${BASE_PATH}/plans/${planId}`;
  return `${BASE_PATH}/plans/${planId}?focus=${focusPath.join(",")}`;
}

export function PlanCanvas({
  graph,
  people,
  focusParam,
  undoRedo,
  viewerKerberos,
}: {
  graph: PlanGraph;
  people: Person[];
  focusParam?: string | null;
  undoRedo: UndoRedoState;
  viewerKerberos: string;
}) {
  return (
    <ReactFlowProvider>
      <CanvasInner
        key={graph.plan.id}
        graph={graph}
        people={people}
        focusParam={focusParam ?? null}
        undoRedo={undoRedo}
        viewerKerberos={viewerKerberos}
      />
    </ReactFlowProvider>
  );
}

function CanvasInner({
  graph: initialGraph,
  people,
  focusParam,
  undoRedo: initialUndoRedo,
  viewerKerberos,
}: {
  graph: PlanGraph;
  people: Person[];
  focusParam: string | null;
  undoRedo: UndoRedoState;
  viewerKerberos: string;
}) {
  const router = useRouter();
  const { screenToFlowPosition, fitView, setViewport, getViewport } = useReactFlow();
  const storeApi = useStoreApi();
  const rfRef = useRef({ fitView, setViewport, getViewport });
  useEffect(() => {
    rfRef.current = { fitView, setViewport, getViewport };
  }, [fitView, setViewport, getViewport]);

  const [clientGraph, setClientGraph] = useState<PlanGraph>(() => initialGraph);
  const [focusPath, setFocusPath] = useState<ScenePath>(() =>
    resolveFocusPath(focusParam, initialGraph),
  );
  const [selectedSceneIds, setSelectedSceneIds] = useState<ReadonlySet<string>>(() => new Set());
  const [organizeBusy, setOrganizeBusy] = useState(false);
  const [nodes, setNodes] = useState<FlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const historyBusyRef = useRef(false);
  const [undoRedoState, setUndoRedoState] = useState<UndoRedoState>(() => initialUndoRedo);
  const [settings, setSettings] = useState<CanvasSettings>(DEFAULT_CANVAS_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  useEffect(() => {
    // After mount, so the server HTML and the first client render match.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read this browser's saved settings
    setSettings(readCanvasSettings(viewerKerberos, window.localStorage));
  }, [viewerKerberos]);
  const startableOnly = settings.startableOnly;
  const updateSettings = useCallback(
    (next: CanvasSettings) => {
      setSettings(next);
      writeCanvasSettings(viewerKerberos, next, window.localStorage);
    },
    [viewerKerberos],
  );
  const [filters, setFilters] = useState<TaskFilters>(readStoredFilters);
  const [graphSyncTick, setGraphSyncTick] = useState(0);
  const [pendingFocusSceneIds, setPendingFocusSceneIds] = useState<Set<string> | null>(null);
  const { focusedIds, toggleFocus } = useFocusPoints(initialGraph.plan.id);

  const clientGraphRef = useRef(clientGraph);
  useEffect(() => {
    clientGraphRef.current = clientGraph;
  }, [clientGraph]);

  const undoRefreshTimerRef = useRef<number | null>(null);
  const onHistoryMutationSuccess = useCallback(() => {
    setUndoRedoState((prev) => ({
      ...prev,
      canUndo: true,
      canRedo: false,
      redoLabel: null,
    }));
    // The label is only for the dock; one fetch covers a burst of edits.
    if (undoRefreshTimerRef.current !== null) window.clearTimeout(undoRefreshTimerRef.current);
    undoRefreshTimerRef.current = window.setTimeout(() => {
      undoRefreshTimerRef.current = null;
      void getUndoRedo().then((result) => {
        if (result.ok) setUndoRedoState(result.data);
      });
    }, 400);
  }, []);

  useEffect(() => {
    const raw = sessionStorage.getItem("pkrtask:focus");
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as {
        planId?: string;
        focusTaskIds?: string[];
        focusLinkIds?: string[];
      };
      if (parsed.planId !== undefined && parsed.planId !== initialGraph.plan.id) {
        return;
      }
      sessionStorage.removeItem("pkrtask:focus");
      const focusSceneIds = sceneIdsFromFocus(
        flattenScene(initialGraph),
        parsed.focusTaskIds ?? [],
        parsed.focusLinkIds ?? [],
      );
      if (focusSceneIds.size === 0) return;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot sessionStorage focus restore
      setSelectedSceneIds(focusSceneIds);
      setPendingFocusSceneIds(focusSceneIds);
    } catch {
      // ignore malformed focus payload
    }
  }, [initialGraph]);

  const scene = useMemo(() => flattenScene(clientGraph), [clientGraph]);
  const taskFilterStats = useMemo(() => {
    let matched = 0;
    for (const entry of scene.tasks) {
      if (taskMatchesFilters(entry.task, filters, viewerKerberos)) matched += 1;
    }
    return { matched, total: scene.tasks.length };
  }, [scene.tasks, filters, viewerKerberos]);
  const sceneRef = useRef(scene);
  useEffect(() => {
    sceneRef.current = scene;
  }, [scene]);

  const { visible, chromeless } = useMemo(
    () =>
      applyStartableOnlyFilter(
        scene,
        visibleSceneIds(scene, focusPath),
        startableOnly,
        clientGraph.externallyBlocked,
      ),
    [scene, focusPath, startableOnly, clientGraph.externallyBlocked],
  );

  const renderScene = useMemo(
    () => (startableOnly ? refitVisibleFrameContents(scene, visible, chromeless) : scene),
    [scene, visible, chromeless, startableOnly],
  );

  const effectiveSelectedSceneIds = useMemo(() => {
    const next = new Set<string>();
    for (const id of selectedSceneIds) {
      if (!visible.has(id)) continue;
      const task = scene.tasks.find((t) => t.sceneId === id);
      const frame = scene.frames.find((f) => f.sceneId === id);
      const path = task?.path ?? frame?.path;
      if (path && pathsEqual(path, focusPath)) next.add(id);
    }
    return next;
  }, [selectedSceneIds, visible, scene, focusPath]);

  const singleSelectedSceneId = useMemo(
    () => (effectiveSelectedSceneIds.size === 1 ? [...effectiveSelectedSceneIds][0] ?? null : null),
    [effectiveSelectedSceneIds],
  );

  const focusAffine = useMemo(
    () => (focusPath.length > 0 ? containerAffine(clientGraph, focusPath) : null),
    [clientGraph, focusPath],
  );

  const focusAffineRef = useRef<Affine | null>(null);
  useEffect(() => {
    focusAffineRef.current = focusAffine;
  }, [focusAffine]);

  type PendingFocusTransition = {
    fromAffine: Affine | null;
    fromViewport: Viewport;
    generation: number;
  };
  const pendingFocusTransitionRef = useRef<PendingFocusTransition | null>(null);
  const focusTransitionGenerationRef = useRef(0);
  const focusFitViewFrameRef = useRef<number | null>(null);

  const stashFocusTransition = useCallback(() => {
    if (focusFitViewFrameRef.current !== null) {
      window.cancelAnimationFrame(focusFitViewFrameRef.current);
      focusFitViewFrameRef.current = null;
    }
    void rfRef.current.setViewport(rfRef.current.getViewport(), { duration: 0 });
    focusTransitionGenerationRef.current += 1;
    pendingFocusTransitionRef.current = {
      fromAffine: focusAffineRef.current,
      fromViewport: rfRef.current.getViewport(),
      generation: focusTransitionGenerationRef.current,
    };
  }, []);

  const maxZoom = useMemo(
    () => computeMaxZoom(scene, focusPath, focusAffine),
    [scene, focusPath, focusAffine],
  );

  const skipMountFitRef = useRef(false);
  const mountFitInitRef = useRef(false);
  const mountFitRef = useRef<{
    scene: Scene;
    focusPath: ScenePath;
    focusAffine: Affine | null;
    maxZoom: number;
    startableOnly: boolean;
    externallyBlocked: boolean;
  } | null>(null);

  useLayoutEffect(() => {
    if (mountFitInitRef.current) return;
    mountFitInitRef.current = true;

    const raw = sessionStorage.getItem("pkrtask:focus");
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as {
          planId?: string;
          focusTaskIds?: string[];
          focusLinkIds?: string[];
        };
        const payloadPlanId = parsed.planId;
        if (payloadPlanId === undefined || payloadPlanId === initialGraph.plan.id) {
          skipMountFitRef.current = true;
          return;
        }
      } catch {
        // ignore malformed focus payload
      }
    }

    mountFitRef.current = {
      scene,
      focusPath,
      focusAffine,
      maxZoom,
      startableOnly,
      externallyBlocked: clientGraph.externallyBlocked,
    };
  }, [
    scene,
    focusPath,
    focusAffine,
    maxZoom,
    startableOnly,
    clientGraph.externallyBlocked,
    initialGraph.plan.id,
  ]);

  const isDraggingRef = useRef(false);
  const frameDragRef = useRef<{
    frameId: string;
    prefix: ScenePath;
    start: { x: number; y: number };
    nodeStarts: Map<string, { x: number; y: number }>;
  } | null>(null);
  const nodesRef = useRef<FlowNode[]>([]);

  // --- Live collaboration -------------------------------------------------
  const reloadSeqRef = useRef(0);
  const reloadDeferredRef = useRef(false);
  const reloadTimerRef = useRef<number | null>(null);

  const applyServerGraph = useCallback((next: PlanGraph) => {
    setClientGraph(next);
    setFocusPath((prev) => {
      const valid = validateFocusPath(next, prev);
      return pathsEqual(valid, prev) ? prev : valid;
    });
    setGraphSyncTick((tick) => tick + 1);
  }, []);

  /** Replace local state with the server's; never mid-drag (it would snap nodes back). */
  const reloadFromServer = useCallback(async () => {
    if (isDraggingRef.current || frameDragRef.current) {
      reloadDeferredRef.current = true;
      return;
    }
    const seq = ++reloadSeqRef.current;
    const result = await loadPlan(clientGraphRef.current.plan.id);
    if (seq !== reloadSeqRef.current) return;
    if (!result.ok) {
      setError(result.error);
      return;
    }
    if (isDraggingRef.current || frameDragRef.current) {
      reloadDeferredRef.current = true;
      return;
    }
    applyServerGraph(result.data);
  }, [applyServerGraph]);

  const onPlansChanged = useCallback(
    (event: ChangedEvent) => {
      // This tab already applied its own edit. Reloading here repaints the canvas
      // a round trip later. Other people still reload.
      if (event.by === viewerKerberos) return;
      const shown = planIdsInGraph(clientGraphRef.current);
      if (!event.planIds.some((id) => shown.has(id))) return;
      if (reloadTimerRef.current !== null) window.clearTimeout(reloadTimerRef.current);
      reloadTimerRef.current = window.setTimeout(() => {
        reloadTimerRef.current = null;
        void reloadFromServer();
      }, 150);
    },
    [reloadFromServer, viewerKerberos],
  );

  useEffect(
    () => () => {
      if (reloadTimerRef.current !== null) window.clearTimeout(reloadTimerRef.current);
      if (undoRefreshTimerRef.current !== null) window.clearTimeout(undoRefreshTimerRef.current);
    },
    [],
  );

  const realtime = useRealtime(initialGraph.plan.id, viewerKerberos, onPlansChanged);
  const { claim } = realtime;

  const handleFrameResizeEnd = useCallback(
    (linkId: string, path: ScenePath, frameScale: number, size: Rect) => {
      const graph = clientGraphRef.current;
      const worldPos = flowToWorld({ x: size.x, y: size.y }, focusAffineRef.current);
      const inv = invertAffine(containerAffine(graph, path));
      const canonicalPos = applyAffine(worldPos, inv);
      let canonicalW = size.width / frameScale;
      let canonicalH = size.height / frameScale;
      canonicalW = Math.max(120, canonicalW);
      canonicalH = Math.max(80, canonicalH);

      const frame = sceneRef.current.frames.find((f) => f.linkId === linkId && pathsEqual(f.path, path));
      const parentPlanId = frame?.parentPlanId ?? resolveFocusedPlanId(graph, path);

      const prev = frame?.subPlan;
      setClientGraph((g) =>
        updateFrameEverywhere(g, linkId, {
          x: canonicalPos.x,
          y: canonicalPos.y,
          width: canonicalW,
          height: canonicalH,
        }),
      );

      void updatePlanLinkPositions({
        planId: parentPlanId,
        positions: [
          {
            planLinkId: linkId,
            x: canonicalPos.x,
            y: canonicalPos.y,
            width: canonicalW,
            height: canonicalH,
          },
        ],
      }).then((result) => {
        if (!result.ok) {
          if (prev) {
            setClientGraph((g) =>
              updateFrameEverywhere(g, linkId, {
                x: prev.x,
                y: prev.y,
                width: prev.width,
                height: prev.height,
              }),
            );
          }
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [onHistoryMutationSuccess],
  );

  const handleTaskResizeEnd = useCallback(
    (taskId: string, path: ScenePath, taskScale: number, size: Rect) => {
      const graph = clientGraphRef.current;
      const worldPos = flowToWorld({ x: size.x, y: size.y }, focusAffineRef.current);
      const inv = invertAffine(containerAffine(graph, path));
      const canonicalPos = applyAffine(worldPos, inv);
      let canonicalW = size.width / taskScale;
      let canonicalH = size.height / taskScale;
      canonicalW = Math.max(120, canonicalW);
      canonicalH = Math.max(60, canonicalH);

      const sceneTask = sceneRef.current.tasks.find(
        (t) => t.taskId === taskId && pathsEqual(t.path, path),
      );
      const ownerPlanId = sceneTask?.ownerPlanId ?? resolveFocusedPlanId(graph, path);
      const prev = sceneTask?.task;

      setClientGraph((g) =>
        patchTaskEverywhere(g, taskId, {
          x: canonicalPos.x,
          y: canonicalPos.y,
          width: canonicalW,
          height: canonicalH,
        }),
      );

      void updateTaskPositions({
        planId: ownerPlanId,
        positions: [
          {
            taskId,
            x: canonicalPos.x,
            y: canonicalPos.y,
            width: canonicalW,
            height: canonicalH,
          },
        ],
      }).then((result) => {
        if (!result.ok) {
          if (prev) {
            setClientGraph((g) =>
              patchTaskEverywhere(g, taskId, {
                x: prev.x,
                y: prev.y,
                width: prev.width,
                height: prev.height,
              }),
            );
          }
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [onHistoryMutationSuccess],
  );

  const canvasActions = useMemo(
    () => ({ onFrameResize: handleFrameResizeEnd, onTaskResize: handleTaskResizeEnd }),
    [handleFrameResizeEnd, handleTaskResizeEnd],
  );

  useLayoutEffect(() => {
    if (isDraggingRef.current || frameDragRef.current) return;
    const built = buildFlowNodes(
      renderScene,
      focusPath,
      focusAffine,
      visible,
      chromeless,
      effectiveSelectedSceneIds,
      startableOnly,
      clientGraph.externallyBlocked,
      focusedIds,
      toggleFocus,
      filters,
      viewerKerberos,
      realtime.lockedByOthers,
    );
    setNodes((prev) => mergeFlowNodes(built, prev));
    setEdges((prev) => mergeFlowEdges(buildFlowEdges(renderScene, visible, focusAffine), prev));

    const pending = pendingFocusTransitionRef.current;
    if (pending) {
      pendingFocusTransitionRef.current = null;
      const matched = matchViewportAcrossAffine(
        pending.fromViewport,
        pending.fromAffine,
        focusAffine,
      );
      const generation = pending.generation;

      void rfRef.current.setViewport(matched, { duration: 0 });
      focusFitViewFrameRef.current = window.requestAnimationFrame(() => {
        focusFitViewFrameRef.current = null;
        if (focusTransitionGenerationRef.current !== generation) return;
        const { width, height } = storeApi.getState();
        const target = viewportForVisibleNodes(built, chromeless, width, height, maxZoom);
        if (target) {
          void rfRef.current.setViewport(target, { duration: 400 });
        } else {
          void rfRef.current.fitView({ padding: 0.15, duration: 400 });
        }
      });
    }
  }, [renderScene, focusPath, focusAffine, visible, chromeless, effectiveSelectedSceneIds, graphSyncTick, maxZoom, storeApi, startableOnly, clientGraph.externallyBlocked, focusedIds, toggleFocus, filters, viewerKerberos, realtime.lockedByOthers]);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    if (skipMountFitRef.current) return;
    const ctx = mountFitRef.current;
    if (!ctx) return;
    const frame = window.requestAnimationFrame(() => {
      const { width, height } = storeApi.getState();
      const { visible: mountVisible, chromeless: mountChromeless } = applyStartableOnlyFilter(
        ctx.scene,
        visibleSceneIds(ctx.scene, ctx.focusPath),
        ctx.startableOnly,
        ctx.externallyBlocked,
      );
      const mountRenderScene = ctx.startableOnly
        ? refitVisibleFrameContents(ctx.scene, mountVisible, mountChromeless)
        : ctx.scene;
      const mountNodes = buildFlowNodes(
        mountRenderScene,
        ctx.focusPath,
        ctx.focusAffine,
        mountVisible,
        mountChromeless,
        new Set<string>(),
        ctx.startableOnly,
        ctx.externallyBlocked,
        new Set<string>(),
        () => {},
        // Mount fit only reads geometry. Empty filters leave dimming off.
        EMPTY_FILTERS,
        "",
        new Map(),
      );
      const target = viewportForVisibleNodes(
        mountNodes,
        mountChromeless,
        width,
        height,
        ctx.maxZoom,
      );
      if (target) {
        void rfRef.current.setViewport(target, { duration: 0 });
      } else {
        void rfRef.current.fitView({ padding: 0.15, duration: 0 });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [storeApi]);

  useEffect(() => {
    if (!pendingFocusSceneIds || pendingFocusSceneIds.size === 0) return;

    const timer = window.setTimeout(() => {
      setPendingFocusSceneIds(null);
    }, 2000);

    return () => window.clearTimeout(timer);
  }, [pendingFocusSceneIds]);

  useEffect(() => {
    if (!pendingFocusSceneIds || pendingFocusSceneIds.size === 0) return;

    const ids = [...pendingFocusSceneIds];
    const allPresent = ids.every((id) => nodes.some((node) => node.id === id));
    if (!allPresent) return;

    void rfRef.current.fitView({
      nodes: ids.map((id) => ({ id })),
      padding: 0.3,
      duration: 300,
    });
    // eslint-disable-next-line react-hooks/set-state-in-effect -- clear pending after fitView sync
    setPendingFocusSceneIds(null);
  }, [pendingFocusSceneIds, nodes]);

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), 6000);
    return () => window.clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    const href = focusHref(initialGraph.plan.id, focusPath);
    replaceAppUrl(href);
  }, [focusPath, initialGraph.plan.id]);

  const enterFocus = useCallback(
    (framePath: ScenePath, linkId: string) => {
      stashFocusTransition();
      const next = [...framePath, linkId];
      setFocusPath(next);
      setSelectedSceneIds(new Set());
    },
    [stashFocusTransition],
  );

  const exitToFocusPath = useCallback(
    (next: ScenePath) => {
      stashFocusTransition();
      const prevFocusPath = focusPath;
      setFocusPath(next);

      const currentScene = flattenScene(clientGraphRef.current);
      const frameSceneId = resolveExitSelection(prevFocusPath, next, currentScene);
      setSelectedSceneIds(frameSceneId ? new Set([frameSceneId]) : new Set());
    },
    [focusPath, stashFocusTransition],
  );

  const focusedPlanId = useMemo(
    () => resolveFocusedPlanId(clientGraph, focusPath),
    [clientGraph, focusPath],
  );

  const focusedSubPlan = useMemo(
    () => findSubPlanByFocusPath(clientGraph, focusPath),
    [clientGraph, focusPath],
  );

  const breadcrumbs = useMemo(
    () => focusBreadcrumbs(clientGraph, focusPath),
    [clientGraph, focusPath],
  );

  const headerRollup = useMemo(() => {
    if (focusedSubPlan) return focusedSubPlan.rollup;
    return clientGraph.plan.rollup;
  }, [clientGraph, focusedSubPlan]);

  const directSubPlanCount = useMemo(() => {
    const container = getFocusedContainer(clientGraph, focusPath);
    return container.subPlans.length;
  }, [clientGraph, focusPath]);

  const selectedTask = useMemo(() => {
    if (!singleSelectedSceneId) return null;
    const st = findSceneTask(scene, singleSelectedSceneId);
    return st?.task ?? null;
  }, [scene, singleSelectedSceneId]);

  const selectedFrame = useMemo(() => {
    if (!singleSelectedSceneId) return null;
    return findSceneFrame(scene, singleSelectedSceneId) ?? null;
  }, [scene, singleSelectedSceneId]);

  // Tasks in a private plan can only go to its owner (the server enforces this too).
  const assignablePeople = useMemo(() => {
    const scope = selectedTask ? scopeOfPlan(clientGraph, selectedTask.planId) : null;
    if (scope?.visibility !== "private") return people;
    return people.filter((p) => p.kerberos === scope.ownerKerberos);
  }, [clientGraph, people, selectedTask]);

  // Hold locks on whatever is selected, so nobody else edits it meanwhile.
  const selectionLockKeys = useMemo(
    () => lockKeysForSceneIds(scene, effectiveSelectedSceneIds),
    [scene, effectiveSelectedSceneIds],
  );
  const selectionLockKeysRef = useRef(selectionLockKeys);
  useEffect(() => {
    selectionLockKeysRef.current = selectionLockKeys;
    claim(selectionLockKeys);
  }, [claim, selectionLockKeys]);

  const organizeSelection = useMemo(
    () => resolveOrganizeIds(scene, focusPath, effectiveSelectedSceneIds),
    [scene, focusPath, effectiveSelectedSceneIds],
  );

  const moveDestinations = useMemo(() => {
    const all = collectPlanDestinations(clientGraph);
    const childMap = buildChildPlanIdsByParent(clientGraph);
    return filterMoveDestinations(
      all,
      focusedPlanId,
      organizeSelection.planLinkIds,
      childMap,
      clientGraph,
    );
  }, [clientGraph, focusedPlanId, organizeSelection.planLinkIds]);

  const runHistory = useCallback(
    async (kind: "undo" | "redo") => {
      if (historyBusyRef.current) return;
      if (kind === "undo" && !undoRedoState.canUndo) return;
      if (kind === "redo" && !undoRedoState.canRedo) return;

      historyBusyRef.current = true;
      setHistoryBusy(true);
      try {
        const planId = clientGraphRef.current.plan.id;
        const result = kind === "undo" ? await undoChange(planId) : await redoChange(planId);
        if (!result.ok) {
          setError(result.error);
          return;
        }

        const {
          graph: newGraph,
          undoRedo: nextUndoRedo,
          focusPlanId,
          focusTaskIds,
          focusLinkIds,
        } = result.data;

        if (focusPlanId && focusPlanId !== planId) {
          sessionStorage.setItem(
            "pkrtask:focus",
            JSON.stringify({ planId: focusPlanId, focusTaskIds, focusLinkIds }),
          );
          router.push(`/plans/${focusPlanId}`);
          return;
        }

        setClientGraph(newGraph);
        setUndoRedoState(nextUndoRedo);

        const nextScene = flattenScene(newGraph);
        const validFocus = validateFocusPath(newGraph, focusPath);
        if (validFocus.length !== focusPath.length || !pathsEqual(validFocus, focusPath)) {
          setFocusPath(validFocus);
        }

        const focusSceneIds = sceneIdsFromFocus(nextScene, focusTaskIds, focusLinkIds);
        setSelectedSceneIds(focusSceneIds);
        if (focusSceneIds.size > 0) {
          setPendingFocusSceneIds(focusSceneIds);
        }

        setGraphSyncTick((tick) => tick + 1);
      } finally {
        historyBusyRef.current = false;
        setHistoryBusy(false);
      }
    },
    [focusPath, router, undoRedoState.canRedo, undoRedoState.canUndo],
  );

  useRegisterHistoryDock({
    canUndo: undoRedoState.canUndo,
    canRedo: undoRedoState.canRedo,
    undoLabel: undoRedoState.undoLabel ?? undefined,
    redoLabel: undoRedoState.redoLabel ?? undefined,
    busy: historyBusy,
    onUndo: () => void runHistory("undo"),
    onRedo: () => void runHistory("redo"),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.target instanceof HTMLSelectElement) return;
      if (e.target instanceof HTMLElement && e.target.isContentEditable) return;

      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) {
          if (undoRedoState.canRedo) void runHistory("redo");
        } else if (undoRedoState.canUndo) {
          void runHistory("undo");
        }
        return;
      }
      if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        if (undoRedoState.canRedo) void runHistory("redo");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [runHistory, undoRedoState.canRedo, undoRedoState.canUndo]);

  const patchLocalTask = useCallback((taskId: string, patch: Partial<TaskDto>) => {
    setClientGraph((g) => patchTaskEverywhere(g, taskId, patch));
  }, []);

  const handlePatch = useCallback(
    (taskId: string, patch: TaskPatch) => {
      const prev = selectedTask;
      patchLocalTask(taskId, patch);
      void updateTask({ taskId, patch }).then((result) => {
        if (!result.ok) {
          if (prev) patchLocalTask(taskId, prev);
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [onHistoryMutationSuccess, patchLocalTask, selectedTask],
  );

  const handleSetAssignees = useCallback(
    (taskId: string, kerberos: string[]) => {
      const prev = selectedTask?.assignees ?? [];
      patchLocalTask(taskId, { assignees: kerberos });
      void setTaskAssignees({ taskId, kerberos }).then((result) => {
        if (!result.ok) {
          patchLocalTask(taskId, { assignees: prev });
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [onHistoryMutationSuccess, patchLocalTask, selectedTask],
  );

  const handleDeleteTask = useCallback((taskId: string) => {
    if (!window.confirm("Delete this task and its connections?")) return;
    void deleteTask(taskId).then((result) => {
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setSelectedSceneIds(new Set());
      setClientGraph((g) => removeTaskEverywhere(g, taskId));
      onHistoryMutationSuccess();
    });
  }, [onHistoryMutationSuccess]);

  const handleSubPlanPatch = useCallback((childPlanId: string, patch: SubPlanPatch) => {
    const prevGraph = clientGraphRef.current;
    setClientGraph((g) => patchPlanMetaEverywhere(g, childPlanId, patch));
    void updatePlanMeta({ planId: childPlanId, patch }).then((result) => {
      if (!result.ok) {
        setClientGraph(prevGraph);
        setError(result.error);
        return;
      }
      onHistoryMutationSuccess();
    });
  }, [onHistoryMutationSuccess]);

  const handleShareSubPlan = useCallback((childPlanId: string) => {
    void sharePlan(childPlanId).then((result) => {
      if (!result.ok) {
        setError(result.error);
        return;
      }
      void reloadFromServer();
    });
  }, [reloadFromServer]);

  const handleDeleteSubPlan = useCallback((linkId: string) => {
    const prevGraph = clientGraphRef.current;
    setClientGraph((g) => removeSubPlanEverywhere(g, linkId));
    setSelectedSceneIds(new Set());
    void deletePlanLink(linkId).then((result) => {
      if (!result.ok) {
        setClientGraph(prevGraph);
        setError(result.error);
        return;
      }
      onHistoryMutationSuccess();
    });
  }, [onHistoryMutationSuccess]);

  const addTaskAtWorld = useCallback(
    (flowPos: { x: number; y: number }) => {
      if (startableOnly) return;
      const world = flowToWorld(flowPos, focusAffineRef.current);
      const inv = invertAffine(containerAffine(clientGraphRef.current, focusPath));
      const canonical = applyAffine(world, inv);
      const x = canonical.x - TASK_NODE_WIDTH / 2;
      const y = canonical.y - TASK_NODE_HEIGHT / 2;

      const taskId = crypto.randomUUID();
      const task: TaskDto = {
        id: taskId,
        planId: focusedPlanId,
        title: "New task",
        status: "todo",
        notes: "",
        deadline: null,
        effort: null,
        x,
        y,
        width: TASK_NODE_WIDTH,
        height: TASK_NODE_HEIGHT,
        assignees: [],
      };
      setClientGraph((g) => insertTaskIntoPlan(g, focusedPlanId, task));
      setSelectedSceneIds(new Set([sceneTaskId(focusPath, taskId)]));
      void createTask({
        planId: focusedPlanId,
        taskId,
        title: "New task",
        x,
        y,
      }).then((result) => {
        if (!result.ok) {
          setClientGraph((g) => removeTaskEverywhere(g, taskId));
          setSelectedSceneIds(new Set());
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [focusPath, focusedPlanId, onHistoryMutationSuccess, startableOnly],
  );

  const addSubPlanAtWorld = useCallback(
    (flowPos: { x: number; y: number }) => {
      if (startableOnly) return;
      const name = window.prompt("Sub-plan name?", "New sub-plan");
      if (!name?.trim()) return;

      const world = flowToWorld(flowPos, focusAffineRef.current);
      const inv = invertAffine(containerAffine(clientGraphRef.current, focusPath));
      const canonical = applyAffine(world, inv);

      const parentVisibility = visibilityOfPlan(clientGraphRef.current, focusedPlanId);
      if (!parentVisibility) {
        setError("Couldn't read the parent plan.");
        void reloadFromServer();
        return;
      }
      const childPlanId = crypto.randomUUID();
      const planLinkId = crypto.randomUUID();
      const subPlan: SubPlanDto = {
        linkId: planLinkId,
        parentPlanId: focusedPlanId,
        childPlanId,
        name: name.trim(),
        description: "",
        ownerKerberos: viewerKerberos,
        visibility: parentVisibility,
        x: canonical.x,
        y: canonical.y,
        width: 360,
        height: 240,
        assignees: [],
        rollup: { total: 0, done: 0, blocked: 0, nearestDeadline: null },
        tasks: [],
        edges: [],
        subPlans: [],
        status: "todo",
        effort: null,
        deadline: null,
        notes: "",
      };
      setClientGraph((g) => insertSubPlanIntoPlan(g, focusedPlanId, subPlan));
      void createSubPlan({
        parentPlanId: focusedPlanId,
        childPlanId,
        planLinkId,
        name: name.trim(),
        x: canonical.x,
        y: canonical.y,
      }).then((result) => {
        if (!result.ok) {
          setClientGraph((g) => removeSubPlanEverywhere(g, planLinkId));
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [focusPath, focusedPlanId, onHistoryMutationSuccess, reloadFromServer, startableOnly, viewerKerberos],
  );

  const handleOrganizeMove = useCallback(
    (targetPlanId: string) => {
      if (organizeBusy || startableOnly) return;
      const { taskIds, planLinkIds } = resolveOrganizeIds(
        sceneRef.current,
        focusPath,
        effectiveSelectedSceneIds,
      );
      if (taskIds.length + planLinkIds.length === 0) return;

      const anchor = selectionAnchorCanonical(sceneRef.current, focusPath, effectiveSelectedSceneIds);
      setOrganizeBusy(true);
      void moveNodesToPlan({
        sourcePlanId: focusedPlanId,
        targetPlanId,
        taskIds,
        planLinkIds,
        anchorX: anchor.x,
        anchorY: anchor.y,
      }).then((result) => {
        setOrganizeBusy(false);
        if (!result.ok) {
          setError(result.error);
          return;
        }
        setSelectedSceneIds(new Set());
        if (result.data.droppedEdges > 0) {
          setError(
            `${result.data.droppedEdges} dependency edge${result.data.droppedEdges === 1 ? "" : "s"} removed at the plan boundary.`,
          );
        }
        onHistoryMutationSuccess();
        void reloadFromServer();
      });
    },
    [
      effectiveSelectedSceneIds,
      focusPath,
      focusedPlanId,
      onHistoryMutationSuccess,
      organizeBusy,
      reloadFromServer,
      startableOnly,
    ],
  );

  const handleOrganizePackage = useCallback(() => {
    if (organizeBusy || startableOnly) return;
    const { taskIds, planLinkIds } = resolveOrganizeIds(
      sceneRef.current,
      focusPath,
      effectiveSelectedSceneIds,
    );
    if (taskIds.length + planLinkIds.length === 0) return;

    const name = window.prompt("Sub-plan name?", "New sub-plan");
    if (!name?.trim()) return;

    const anchor = selectionAnchorCanonical(sceneRef.current, focusPath, effectiveSelectedSceneIds);
    setOrganizeBusy(true);
    void packageNodesIntoSubPlan({
      parentPlanId: focusedPlanId,
      name: name.trim(),
      taskIds,
      planLinkIds,
      x: anchor.x,
      y: anchor.y,
    }).then((result) => {
      setOrganizeBusy(false);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setSelectedSceneIds(new Set());
      if (result.data.droppedEdges > 0) {
        setError(
          `${result.data.droppedEdges} dependency edge${result.data.droppedEdges === 1 ? "" : "s"} removed at the plan boundary.`,
        );
      }
      onHistoryMutationSuccess();
      void reloadFromServer();
    });
  }, [
    effectiveSelectedSceneIds,
    focusPath,
    focusedPlanId,
    onHistoryMutationSuccess,
    organizeBusy,
    reloadFromServer,
    startableOnly,
  ]);

  const onNodeDragStart = useCallback((_event: MouseEvent | TouchEvent, node: FlowNode, draggedNodes: FlowNode[]) => {
    isDraggingRef.current = true;
    claim([
      ...selectionLockKeysRef.current,
      ...lockKeysForSceneIds(sceneRef.current, [node.id, ...draggedNodes.map((n) => n.id)]),
    ]);
    if (node.type !== "subPlanFrame") return;
    const prefix: ScenePath = [...node.data.path, node.data.linkId];
    const nodeStarts = new Map<string, { x: number; y: number }>();
    for (const n of nodesRef.current) {
      const path = n.type === "task" || n.type === "subPlanFrame" ? n.data.path : [];
      if (n.id === node.id || pathStartsWith(path, prefix)) {
        nodeStarts.set(n.id, { ...n.position });
      }
    }
    frameDragRef.current = {
      frameId: node.id,
      prefix,
      start: { ...node.position },
      nodeStarts,
    };
  }, [claim]);

  const onNodeDragStop = useCallback(
    (_event: MouseEvent | TouchEvent, node: FlowNode, draggedNodes: FlowNode[]) => {
      const moved = draggedNodes.length > 0 ? draggedNodes : [node];
      const graph = clientGraphRef.current;
      const taskMoves: {
        taskId: string;
        ownerPlanId: string;
        x: number;
        y: number;
        prevX: number;
        prevY: number;
      }[] = [];
      const frameMoves: {
        linkId: string;
        parentPlanId: string;
        x: number;
        y: number;
        prevX: number;
        prevY: number;
      }[] = [];

      for (const n of moved) {
        if (n.type === "task") {
          const { task, path, ownerPlanId } = n.data;
          const canonical = applyAffine(
            flowToWorld(n.position, focusAffineRef.current),
            invertAffine(containerAffine(graph, path)),
          );
          if (Math.abs(canonical.x - task.x) < 0.5 && Math.abs(canonical.y - task.y) < 0.5) continue;
          taskMoves.push({
            taskId: task.id,
            ownerPlanId,
            x: canonical.x,
            y: canonical.y,
            prevX: task.x,
            prevY: task.y,
          });
        } else if (n.type === "subPlanFrame") {
          const frame = sceneRef.current.frames.find(
            (f) => f.linkId === n.data.linkId && pathsEqual(f.path, n.data.path),
          );
          if (!frame) continue;
          const canonical = applyAffine(
            flowToWorld(n.position, focusAffineRef.current),
            invertAffine(containerAffine(graph, n.data.path)),
          );
          if (Math.abs(canonical.x - frame.subPlan.x) < 0.5 && Math.abs(canonical.y - frame.subPlan.y) < 0.5) {
            continue;
          }
          frameMoves.push({
            linkId: n.data.linkId,
            parentPlanId: frame.parentPlanId,
            x: canonical.x,
            y: canonical.y,
            prevX: frame.subPlan.x,
            prevY: frame.subPlan.y,
          });
        }
      }

      if (taskMoves.length > 0 || frameMoves.length > 0) {
        setClientGraph((g) => {
          let next = g;
          for (const move of taskMoves) {
            next = updateTaskPositionEverywhere(next, move.taskId, move.ownerPlanId, move.x, move.y);
          }
          for (const move of frameMoves) {
            next = updateFrameEverywhere(next, move.linkId, { x: move.x, y: move.y });
          }
          return next;
        });
      }

      const tasksByPlan = new Map<string, typeof taskMoves>();
      for (const move of taskMoves) {
        const group = tasksByPlan.get(move.ownerPlanId) ?? [];
        group.push(move);
        tasksByPlan.set(move.ownerPlanId, group);
      }
      const framesByPlan = new Map<string, typeof frameMoves>();
      for (const move of frameMoves) {
        const group = framesByPlan.get(move.parentPlanId) ?? [];
        group.push(move);
        framesByPlan.set(move.parentPlanId, group);
      }

      const writes: Promise<boolean>[] = [];
      for (const [planId, group] of tasksByPlan) {
        writes.push(
          updateTaskPositions({
            planId,
            positions: group.map((move) => ({ taskId: move.taskId, x: move.x, y: move.y })),
          }).then((result) => {
            if (result.ok) return true;
            setClientGraph((g) => {
              let next = g;
              for (const move of group) {
                next = updateTaskPositionEverywhere(next, move.taskId, move.ownerPlanId, move.prevX, move.prevY);
              }
              return next;
            });
            setError(result.error);
            return false;
          }),
        );
      }
      for (const [planId, group] of framesByPlan) {
        writes.push(
          updatePlanLinkPositions({
            planId,
            positions: group.map((move) => ({ planLinkId: move.linkId, x: move.x, y: move.y })),
          }).then((result) => {
            if (result.ok) return true;
            setClientGraph((g) => {
              let next = g;
              for (const move of group) {
                next = updateFrameEverywhere(next, move.linkId, { x: move.prevX, y: move.prevY });
              }
              return next;
            });
            setError(result.error);
            return false;
          }),
        );
      }
      if (writes.length > 0) {
        void Promise.all(writes).then((oks) => {
          if (oks.some(Boolean)) onHistoryMutationSuccess();
        });
      }

      frameDragRef.current = null;
      isDraggingRef.current = false;
      setGraphSyncTick((tick) => tick + 1);
      claim(selectionLockKeysRef.current);
      if (reloadDeferredRef.current) {
        reloadDeferredRef.current = false;
        void reloadFromServer();
      }
    },
    [claim, onHistoryMutationSuccess, reloadFromServer],
  );

  const onConnect = useCallback<OnConnect>(
    (connection) => {
      if (!connection.source || !connection.target) return;
      const sourceNode = nodesRef.current.find((n) => n.id === connection.source) as FlowNode | undefined;
      const targetNode = nodesRef.current.find((n) => n.id === connection.target) as FlowNode | undefined;
      if (!sourceNode || !targetNode) return;

      const srcPath = sourceNode.type === "task" ? sourceNode.data.path : sourceNode.data.path;
      const tgtPath = targetNode.type === "task" ? targetNode.data.path : targetNode.data.path;
      const srcFrame = sourceNode.type === "subPlanFrame" ? findSceneFrame(sceneRef.current, sourceNode.id) : null;
      const tgtFrame = targetNode.type === "subPlanFrame" ? findSceneFrame(sceneRef.current, targetNode.id) : null;
      const srcOwner =
        sourceNode.type === "task" ? sourceNode.data.ownerPlanId : srcFrame?.parentPlanId;
      const tgtOwner =
        targetNode.type === "task" ? targetNode.data.ownerPlanId : tgtFrame?.parentPlanId;

      if (!srcOwner || !tgtOwner || srcOwner !== tgtOwner || !pathsEqual(srcPath, tgtPath)) {
        setError("Dependencies must stay within one plan.");
        return;
      }

      const from = endpointFromNode(sourceNode);
      const to = endpointFromNode(targetNode);
      if (!from || !to) return;

      const edgeId = crypto.randomUUID();
      const edge: EdgeDto = {
        id: edgeId,
        fromKind: from.kind,
        fromId: from.id,
        toKind: to.kind,
        toId: to.id,
      };
      setClientGraph((g) => addEdgeEverywhere(g, srcOwner, edge));
      void createEdge({
        planId: srcOwner,
        edgeId,
        fromKind: from.kind,
        fromId: from.id,
        toKind: to.kind,
        toId: to.id,
      }).then((result) => {
        if (!result.ok) {
          setClientGraph((g) => removeEdgeEverywhere(g, edgeId));
          setError(result.error);
          return;
        }
        onHistoryMutationSuccess();
      });
    },
    [onHistoryMutationSuccess],
  );

  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    const selected = new Map<string, boolean>();
    for (const change of changes) {
      if (change.type === "select") selected.set(change.id, change.selected);
    }
    if (selected.size > 0) {
      setSelectedSceneIds((prev) => {
        const next = new Set(prev);
        for (const [id, on] of selected) {
          if (on) next.add(id);
          else next.delete(id);
        }
        if (next.size === prev.size && [...next].every((id) => prev.has(id))) return prev;
        return next;
      });
    }
    setNodes((prev) => {
      const next = applyNodeChanges(changes, prev);
      const drag = frameDragRef.current;
      if (!drag) return next;

      const frameNode = next.find((n) => n.id === drag.frameId);
      if (!frameNode) return next;

      const dx = frameNode.position.x - drag.start.x;
      const dy = frameNode.position.y - drag.start.y;
      if (dx === 0 && dy === 0) return next;

      return next.map((n) => {
        const start = drag.nodeStarts.get(n.id);
        if (!start) return n;
        if (n.id === drag.frameId) return n;
        return { ...n, position: { x: start.x + dx, y: start.y + dy } };
      });
    });
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((prev) => applyEdgeChanges(changes, prev));
  }, []);

  const onPaneDoubleClick = useCallback(
    (event: React.MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!settings.doubleClickAddsTask) return;
      if (!target.classList.contains("react-flow__pane")) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addTaskAtWorld(position);
    },
    [addTaskAtWorld, screenToFlowPosition, settings.doubleClickAddsTask],
  );

  const hasContent = scene.tasks.length > 0 || scene.frames.length > 0;

  const hasFocusableContent = useMemo(() => {
    for (const task of scene.tasks) {
      if (visible.has(task.sceneId)) return true;
    }
    for (const frame of scene.frames) {
      if (visible.has(frame.sceneId) && !chromeless.has(frame.sceneId)) return true;
    }
    return false;
  }, [scene.tasks, scene.frames, visible, chromeless]);

  const activeFocusCount = useMemo(() => {
    const focusableIds = new Set<string>();
    for (const task of scene.tasks) {
      if (visible.has(task.sceneId)) focusableIds.add(task.task.id);
    }
    for (const frame of scene.frames) {
      if (visible.has(frame.sceneId) && !chromeless.has(frame.sceneId)) {
        focusableIds.add(frame.linkId);
      }
    }
    let count = 0;
    for (const id of focusedIds) {
      if (focusableIds.has(id)) count += 1;
    }
    return count;
  }, [scene.tasks, scene.frames, visible, chromeless, focusedIds]);

  const showFocusHint = hasFocusableContent && activeFocusCount === 0;
  const canvasPrefs = useMemo(
    () => ({ animateFocus: settings.animateFocus }),
    [settings.animateFocus],
  );

  return (
    <CanvasPrefsContext.Provider value={canvasPrefs}>
    <div className="relative h-[calc(100vh-3.5rem)]" onDoubleClick={onPaneDoubleClick}>
      <div className="absolute left-4 top-4 z-10 flex max-w-[calc(100%-2rem)] flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2">
        <Link href="/" className="text-sm text-ink-muted hover:text-ink">
          ← plans
        </Link>
        <nav className="flex min-w-0 items-center gap-1 text-sm" aria-label="Plan focus">
          {breadcrumbs.map((crumb, i) => {
            const isLast = i === breadcrumbs.length - 1;
            return (
              <span key={`${crumb.path.join("/")}-${crumb.name}`} className="flex min-w-0 items-center gap-1">
                {i > 0 ? <span className="text-ink-muted">›</span> : null}
                {isLast ? (
                  <span className="max-w-48 truncate font-semibold text-ink">{crumb.name}</span>
                ) : (
                  <button
                    type="button"
                    className="max-w-36 truncate text-ink-muted hover:text-highlight"
                    onClick={() => exitToFocusPath(crumb.path)}
                  >
                    {crumb.name}
                  </button>
                )}
              </span>
            );
          })}
        </nav>
        <span className="text-xs text-ink-muted">
          {headerRollup.done}/{headerRollup.total} tasks
          {directSubPlanCount > 0 ? ` · ${directSubPlanCount} sub-plans` : ""}
        </span>
        {clientGraph.plan.visibility === "private" ? (
          <span className="flex items-center gap-2">
            <span className="rounded-full border border-highlight/40 px-2 py-0.5 text-xs text-highlight">
              Private
            </span>
            <Button
              variant="ghost"
              onClick={() => {
                if (
                  window.confirm(
                    "Share this plan, and every private plan inside it, with everyone? This can't be undone.",
                  )
                ) {
                  handleShareSubPlan(clientGraph.plan.id);
                }
              }}
            >
              Share with everyone
            </Button>
          </span>
        ) : null}
        {realtime.others.length > 0 ? (
          <span className="flex items-center gap-1" aria-label="Also viewing">
            {realtime.others.map((person) => (
              <span
                key={person.kerberos}
                title={`${person.name} is viewing`}
                className="rounded-full px-2 py-0.5 text-xs font-semibold text-night"
                style={{ backgroundColor: editorColor(person.kerberos) }}
              >
                {person.name}
              </span>
            ))}
          </span>
        ) : null}
        <Button
          variant="ghost"
          disabled={startableOnly}
          onClick={() =>
            addTaskAtWorld(
              screenToFlowPosition({
                x: window.innerWidth / 2,
                y: window.innerHeight / 2,
              }),
            )
          }
        >
          + Task
        </Button>
        <Button
          variant="ghost"
          disabled={startableOnly}
          onClick={() =>
            addSubPlanAtWorld(
              screenToFlowPosition({
                x: window.innerWidth / 2,
                y: window.innerHeight / 2,
              }),
            )
          }
        >
          + Sub-plan
        </Button>
        {focusPath.length > 0 ? (
          <Button variant="ghost" onClick={() => exitToFocusPath(focusPath.slice(0, -1))}>
            Back
          </Button>
        ) : null}
        <Button variant="ghost" aria-pressed={settingsOpen} onClick={() => setSettingsOpen((open) => !open)}>
          Settings
        </Button>
      </div>

      {error ? (
        <div
          className="absolute left-1/2 top-4 z-20 -translate-x-1/2 rounded-md border border-status-blocked/50 bg-status-blocked/15 px-4 py-2 text-sm text-status-blocked"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      {!hasContent ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <p className="text-sm text-ink-muted">
            {settings.doubleClickAddsTask
              ? "Double-click for a task, or use + Sub-plan to nest another plan."
              : "Use + Task, or + Sub-plan to nest another plan."}
          </p>
        </div>
      ) : null}

      {showFocusHint ? (
        <div className="pointer-events-none absolute left-1/2 top-16 z-10 -translate-x-1/2 rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted">
          Hover a task or sub-plan and click the target icon to make it jump out
        </div>
      ) : null}

      <PlanCanvasActionsContext.Provider value={canvasActions}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStart={onNodeDragStart}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={(event, node) => {
          const nodePath = node.type === "task" || node.type === "subPlanFrame" ? node.data.path : [];
          const additive = settings.selectModifier === "shift" ? event.shiftKey : event.metaKey || event.ctrlKey;
          if (node.type === "subPlanFrame" && node.data.chromeless) return;
          if (!pathsEqual(nodePath, focusPath)) {
            if (!additive) setSelectedSceneIds(new Set());
            return;
          }

          if (additive) {
            setSelectedSceneIds((prev) => {
              const next = new Set(prev);
              if (next.has(node.id)) next.delete(node.id);
              else next.add(node.id);
              return next;
            });
            return;
          }

          setSelectedSceneIds(new Set([node.id]));
        }}
        onNodeDoubleClick={(_event, node) => {
          if (node.type === "subPlanFrame" && !node.data.chromeless) {
            enterFocus(node.data.path, node.data.linkId);
          }
        }}
        onPaneClick={() => {
          setSelectedSceneIds(new Set());
        }}
        onEdgeDoubleClick={(_event, edge) => {
          if (settings.confirmDeleteEdge && !window.confirm("Remove this dependency?")) return;
          const edgeId = edge.data?.edgeId as string | undefined;
          if (!edgeId) return;
          void deleteEdge(edgeId).then((result) => {
            if (!result.ok) {
              setError(result.error);
              return;
            }
            setClientGraph((g) => removeEdgeEverywhere(g, edgeId));
            onHistoryMutationSuccess();
          });
        }}
        deleteKeyCode={null}
        zoomOnDoubleClick={false}
        panOnScroll={settings.scrollAction === "pan"}
        panOnScrollSpeed={panScrollSpeed(settings)}
        panOnScrollMode={
          settings.scrollAxis === "horizontal"
            ? PanOnScrollMode.Horizontal
            : settings.scrollAxis === "vertical"
              ? PanOnScrollMode.Vertical
              : PanOnScrollMode.Free
        }
        zoomOnScroll={settings.scrollAction === "zoom"}
        zoomOnPinch
        panOnDrag={settings.dragEmpty === "pan"}
        selectionOnDrag={settings.dragEmpty === "select"}
        selectionMode={settings.selectionBox === "partial" ? SelectionMode.Partial : SelectionMode.Full}
        multiSelectionKeyCode={settings.selectModifier === "shift" ? "Shift" : ["Meta", "Control"]}
        autoPanOnNodeDrag={settings.autoPan}
        autoPanOnConnect={settings.autoPan}
        autoPanSpeed={settings.autoPanSpeed}
        connectionRadius={settings.connectionRadius}
        snapToGrid={settings.snapToGrid}
        snapGrid={[settings.gridSize, settings.gridSize]}
        fitView={false}
        fitViewOptions={{ padding: 0.2 }}
        minZoom={MIN_ZOOM}
        maxZoom={maxZoom}
        proOptions={{ hideAttribution: true }}
      >
        {settings.showDots ? (
          <Background variant={BackgroundVariant.Dots} gap={24} size={1.5} color="#2e2833" />
        ) : null}
        {settings.showZoomControls ? <Controls position="bottom-left" showInteractive={false} /> : null}
        <div
          className={`absolute bottom-4 z-10 flex flex-wrap items-end gap-2 ${settings.showZoomControls ? "left-14" : "left-4"}`}
        >
          <FilterBar
            filters={filters}
            people={people}
            matchedCount={taskFilterStats.matched}
            totalCount={taskFilterStats.total}
            onChange={(next) => {
              setFilters(next);
              writeStoredFilters(next);
            }}
          />
        </div>
        {settings.showMinimap ? (
          <MiniMap
            position="bottom-right"
            pannable
            zoomable
            nodeColor={(node) => {
              if (node.type === "subPlanFrame") return "var(--color-highlight)";
              const t = (node as TaskFlowNode).data?.task;
              return t ? `var(--color-status-${t.status})` : "var(--color-line)";
            }}
            maskColor="rgba(13, 11, 14, 0.7)"
            bgColor="var(--color-surface)"
          />
        ) : null}
      </ReactFlow>
      </PlanCanvasActionsContext.Provider>

      {selectedTask ? (
        <TaskInspector
          key={selectedTask.id}
          task={selectedTask}
          people={assignablePeople}
          onPatch={handlePatch}
          onSetAssignees={handleSetAssignees}
          onDelete={handleDeleteTask}
          onClose={() => setSelectedSceneIds(new Set())}
          lockedBy={realtime.lockedByOthers.get(`task:${selectedTask.id}`)?.name ?? null}
        />
      ) : null}

      {selectedFrame && !chromeless.has(selectedFrame.sceneId) ? (
        <SubPlanInspector
          key={selectedFrame.sceneId}
          subPlan={selectedFrame.subPlan}
          linkId={selectedFrame.linkId}
          people={people}
          onPatch={handleSubPlanPatch}
          onDelete={handleDeleteSubPlan}
          onShare={handleShareSubPlan}
          onClose={() => setSelectedSceneIds(new Set())}
          lockedBy={frameEditor(realtime.lockedByOthers, selectedFrame)?.name ?? null}
        />
      ) : null}

      {effectiveSelectedSceneIds.size >= 1 && !startableOnly ? (
        <OrganizeBar
          selectionCount={effectiveSelectedSceneIds.size}
          destinations={moveDestinations}
          busy={organizeBusy}
          onMoveTo={handleOrganizeMove}
          onPackage={handleOrganizePackage}
          onClear={() => setSelectedSceneIds(new Set())}
        />
      ) : null}

      <p className="pointer-events-none absolute bottom-4 left-1/2 z-10 max-w-[70%] -translate-x-1/2 text-center text-xs text-ink-muted">
        {interactionHint(settings)}
      </p>
      {settingsOpen ? (
        <CanvasSettingsPanel settings={settings} onChange={updateSettings} onClose={() => setSettingsOpen(false)} />
      ) : null}
    </div>
    </CanvasPrefsContext.Provider>
  );
}

function interactionHint(settings: CanvasSettings): string {
  const scroll = settings.scrollAction === "pan" ? "scroll: pan" : "scroll: zoom";
  const drag = settings.dragEmpty === "pan" ? "drag empty: pan" : "drag empty: select · space: pan";
  const add = settings.doubleClickAddsTask ? "double-click empty: task · " : "";
  const select = settings.selectModifier === "shift" ? "shift-click: add" : "⌘-click: add";
  return `${scroll} · pinch: zoom · ${drag} · ${add}double-click sub-plan: enter · ${select} · ⌘Z undo`;
}
