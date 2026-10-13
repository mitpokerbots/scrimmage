import {
  applyAffine,
  composeAffine,
  focusTransform,
  invertAffine,
  TASK_NODE_HEIGHT,
  TASK_NODE_WIDTH,
  type Affine,
  type Rect,
} from "./plan-focus";
import type { EdgeDto, PlanRollup, SubPlanDto, TaskDto, TaskStatus } from "./types";

export type SceneGraphInput = {
  plan: { id: string };
  tasks: TaskDto[];
  edges: EdgeDto[];
  subPlans: SubPlanDto[];
};

export const FRAME_HEADER_HEIGHT = 44;

/** linkIds from root container down to (not including) the node's own entry. */
export type ScenePath = string[];

export function sceneTaskId(path: ScenePath, taskId: string): string {
  const prefix = path.length > 0 ? `${path.join("/")}/` : "";
  return `${prefix}task:${taskId}`;
}

export function sceneFrameId(path: ScenePath, linkId: string): string {
  const prefix = path.length > 0 ? `${path.join("/")}/` : "";
  return `${prefix}frame:${linkId}`;
}

export function sceneEdgeId(path: ScenePath, edgeId: string): string {
  const prefix = path.length > 0 ? `${path.join("/")}/` : "";
  return `${prefix}edge:${edgeId}`;
}

export type SceneTask = {
  sceneId: string;
  taskId: string;
  ownerPlanId: string;
  path: ScenePath;
  x: number;
  y: number;
  /** World-space outer width (task.width × scale). */
  width: number;
  /** World-space outer height (task.height × scale). */
  height: number;
  scale: number;
  task: TaskDto;
};

export type SceneFrame = {
  sceneId: string;
  linkId: string;
  parentPlanId: string;
  childPlanId: string;
  path: ScenePath;
  x: number;
  y: number;
  width: number;
  height: number;
  scale: number;
  contentScale: number;
  subPlan: SubPlanDto;
};

export type SceneEdge = {
  sceneId: string;
  edgeId: string;
  ownerPlanId: string;
  path: ScenePath;
  sourceSceneId: string;
  targetSceneId: string;
};

export type Scene = { tasks: SceneTask[]; frames: SceneFrame[]; edges: SceneEdge[] };

/** Sorted, deduplicated union of direct task assignees and child sub-plan assignee unions. */
export function unionAssignees(taskAssignees: string[][], childUnions: string[][]): string[] {
  const seen = new Set<string>();
  for (const group of [...taskAssignees, ...childUnions]) {
    for (const kerberos of group) {
      seen.add(kerberos);
    }
  }
  return [...seen].sort();
}

type RollupInput = { status: TaskStatus; deadline: string | null };

/** Bottom-up recursive rollup across direct tasks and nested sub-plan rollups. */
export function computeRecursiveRollup(
  tasks: RollupInput[],
  childRollups: PlanRollup[],
): PlanRollup {
  const incompleteDeadlines = [
    ...tasks
      .filter((t) => t.status !== "done" && t.deadline)
      .map((t) => new Date(t.deadline!).getTime()),
    ...childRollups
      .filter((r) => r.nearestDeadline)
      .map((r) => new Date(r.nearestDeadline!).getTime()),
  ].sort((a, b) => a - b);

  return {
    total: tasks.length + childRollups.reduce((sum, r) => sum + r.total, 0),
    done:
      tasks.filter((t) => t.status === "done").length +
      childRollups.reduce((sum, r) => sum + r.done, 0),
    blocked:
      tasks.filter((t) => t.status === "blocked").length +
      childRollups.reduce((sum, r) => sum + r.blocked, 0),
    nearestDeadline:
      incompleteDeadlines[0] !== undefined
        ? new Date(incompleteDeadlines[0]).toISOString()
        : null,
  };
}

function frameContentRect(frameX: number, frameY: number, frameW: number, frameH: number): Rect {
  return {
    x: frameX,
    y: frameY + FRAME_HEADER_HEIGHT,
    width: frameW,
    height: frameH - FRAME_HEADER_HEIGHT,
  };
}

function unionRects(rects: Rect[]): Rect | null {
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

function childHull(subPlan: SubPlanDto): Rect | null {
  const taskRects = subPlan.tasks.map((t) => ({
    x: t.x,
    y: t.y,
    width: t.width ?? TASK_NODE_WIDTH,
    height: t.height ?? TASK_NODE_HEIGHT,
  }));
  const frameRects = subPlan.subPlans.map((sp) => ({
    x: sp.x,
    y: sp.y,
    width: sp.width,
    height: sp.height,
  }));
  return unionRects([...taskRects, ...frameRects]);
}

function worldRectFromCanonical(rect: Rect, parentWorld: Affine): Rect {
  const topLeft = applyAffine({ x: rect.x, y: rect.y }, parentWorld);
  const scale = parentWorld.scale;
  return {
    x: topLeft.x,
    y: topLeft.y,
    width: rect.width * scale,
    height: rect.height * scale,
  };
}

function resolveEndpointSceneId(
  kind: EdgeDto["fromKind"],
  id: string,
  path: ScenePath,
  tasks: TaskDto[],
  subPlans: SubPlanDto[],
): string | null {
  if (kind === "task") {
    if (!tasks.some((t) => t.id === id)) return null;
    return sceneTaskId(path, id);
  }
  if (!subPlans.some((sp) => sp.linkId === id)) return null;
  return sceneFrameId(path, id);
}

function flattenContainer(
  ownerPlanId: string,
  path: ScenePath,
  parentWorld: Affine,
  tasks: TaskDto[],
  edges: EdgeDto[],
  subPlans: SubPlanDto[],
  out: Scene,
): void {
  const contentScale = parentWorld.scale;

  const sortedTasks = [...tasks].sort((a, b) => a.id.localeCompare(b.id));
  for (const task of sortedTasks) {
    const world = applyAffine({ x: task.x, y: task.y }, parentWorld);
    const canonicalWidth = task.width ?? TASK_NODE_WIDTH;
    const canonicalHeight = task.height ?? TASK_NODE_HEIGHT;
    out.tasks.push({
      sceneId: sceneTaskId(path, task.id),
      taskId: task.id,
      ownerPlanId,
      path: [...path],
      x: world.x,
      y: world.y,
      width: canonicalWidth * contentScale,
      height: canonicalHeight * contentScale,
      scale: contentScale,
      task,
    });
  }

  const sortedSubPlans = [...subPlans].sort((a, b) => a.linkId.localeCompare(b.linkId));
  for (const subPlan of sortedSubPlans) {
    const framePath = [...path];
    const outerCanonical: Rect = {
      x: subPlan.x,
      y: subPlan.y,
      width: subPlan.width,
      height: subPlan.height,
    };
    const worldOuter = worldRectFromCanonical(outerCanonical, parentWorld);
    const contentRect = frameContentRect(subPlan.x, subPlan.y, subPlan.width, subPlan.height);
    const hull = childHull(subPlan);
    const childToParent = focusTransform(
      hull ?? { x: 0, y: 0, width: 0, height: 0 },
      contentRect,
    );
    const childWorld = composeAffine(parentWorld, childToParent);

    out.frames.push({
      sceneId: sceneFrameId(framePath, subPlan.linkId),
      linkId: subPlan.linkId,
      parentPlanId: subPlan.parentPlanId,
      childPlanId: subPlan.childPlanId,
      path: framePath,
      x: worldOuter.x,
      y: worldOuter.y,
      width: worldOuter.width,
      height: worldOuter.height,
      scale: parentWorld.scale,
      contentScale: childWorld.scale,
      subPlan,
    });

    flattenContainer(
      subPlan.childPlanId,
      [...framePath, subPlan.linkId],
      childWorld,
      subPlan.tasks,
      subPlan.edges,
      subPlan.subPlans,
      out,
    );
  }

  const sortedEdges = [...edges].sort((a, b) => a.id.localeCompare(b.id));
  for (const edge of sortedEdges) {
    const sourceSceneId = resolveEndpointSceneId(
      edge.fromKind,
      edge.fromId,
      path,
      tasks,
      subPlans,
    );
    const targetSceneId = resolveEndpointSceneId(
      edge.toKind,
      edge.toId,
      path,
      tasks,
      subPlans,
    );
    if (!sourceSceneId || !targetSceneId) continue;
    out.edges.push({
      sceneId: sceneEdgeId(path, edge.id),
      edgeId: edge.id,
      ownerPlanId,
      path: [...path],
      sourceSceneId,
      targetSceneId,
    });
  }
}

export function flattenScene(graph: SceneGraphInput): Scene {
  const out: Scene = { tasks: [], frames: [], edges: [] };
  const identity: Affine = { scale: 1, tx: 0, ty: 0 };
  flattenContainer(graph.plan.id, [], identity, graph.tasks, graph.edges, graph.subPlans, out);
  return out;
}

/** Child-canonical → world affine for the container at `path` (root = identity). */
export function containerAffine(graph: SceneGraphInput, path: ScenePath): Affine {
  if (path.length === 0) {
    return { scale: 1, tx: 0, ty: 0 };
  }

  let current: SubPlanDto | undefined;
  let subPlans = graph.subPlans;
  let parentWorld: Affine = { scale: 1, tx: 0, ty: 0 };

  for (let i = 0; i < path.length; i++) {
    const linkId = path[i];
    if (!linkId) return { scale: 1, tx: 0, ty: 0 };
    current = subPlans.find((sp) => sp.linkId === linkId);
    if (!current) return { scale: 1, tx: 0, ty: 0 };

    const contentRect = frameContentRect(current.x, current.y, current.width, current.height);
    const hull = childHull(current);
    const childToParent = focusTransform(
      hull ?? { x: 0, y: 0, width: 0, height: 0 },
      contentRect,
    );
    parentWorld = composeAffine(parentWorld, childToParent);
    subPlans = current.subPlans;
  }

  return parentWorld;
}

export function pathStartsWith(path: ScenePath, prefix: ScenePath): boolean {
  if (prefix.length > path.length) return false;
  for (let i = 0; i < prefix.length; i++) {
    if (path[i] !== prefix[i]) return false;
  }
  return true;
}

export function visibleSceneIds(
  scene: Scene,
  focusPath: ScenePath,
): { visible: Set<string>; chromeless: Set<string> } {
  const visible = new Set<string>();
  const chromeless = new Set<string>();

  if (focusPath.length === 0) {
    for (const task of scene.tasks) visible.add(task.sceneId);
    for (const frame of scene.frames) visible.add(frame.sceneId);
    for (const edge of scene.edges) visible.add(edge.sceneId);
    return { visible, chromeless };
  }

  const focusedLinkId = focusPath.at(-1);
  const focusedFramePath = focusPath.slice(0, -1);

  for (const task of scene.tasks) {
    if (pathStartsWith(task.path, focusPath)) visible.add(task.sceneId);
  }
  for (const frame of scene.frames) {
    if (pathStartsWith(frame.path, focusPath)) visible.add(frame.sceneId);
    if (
      focusedLinkId &&
      frame.linkId === focusedLinkId &&
      frame.path.length === focusedFramePath.length &&
      frame.path.every((id, i) => id === focusedFramePath[i])
    ) {
      visible.add(frame.sceneId);
      chromeless.add(frame.sceneId);
    }
  }

  for (const edge of scene.edges) {
    if (visible.has(edge.sourceSceneId) && visible.has(edge.targetSceneId)) {
      visible.add(edge.sceneId);
    }
  }

  return { visible, chromeless };
}

export function focusWorldRect(scene: Scene, focusPath: ScenePath): Rect | null {
  if (focusPath.length === 0) return null;

  const focusedLinkId = focusPath.at(-1);
  const focusedFramePath = focusPath.slice(0, -1);
  if (!focusedLinkId) return null;

  for (const frame of scene.frames) {
    if (
      frame.linkId === focusedLinkId &&
      frame.path.length === focusedFramePath.length &&
      frame.path.every((id, i) => id === focusedFramePath[i])
    ) {
      return { x: frame.x, y: frame.y, width: frame.width, height: frame.height };
    }
  }
  return null;
}

function worldRectToView(rect: Rect, focusAffine: Affine | null): Rect {
  const focusScale = focusAffine?.scale ?? 1;
  const inv = focusAffine ? invertAffine(focusAffine) : null;
  const topLeft = inv ? applyAffine({ x: rect.x, y: rect.y }, inv) : { x: rect.x, y: rect.y };
  return {
    x: topLeft.x,
    y: topLeft.y,
    width: rect.width / focusScale,
    height: rect.height / focusScale,
  };
}

/** View-space bounds of focused content for camera fit (tests / scene-level fallback). */
export function focusContentViewRect(
  scene: Scene,
  focusPath: ScenePath,
  focusAffine: Affine | null,
): Rect | null {
  const contentRects: Rect[] = [];

  for (const task of scene.tasks) {
    if (!pathStartsWith(task.path, focusPath)) continue;
    contentRects.push(
      worldRectToView(
        { x: task.x, y: task.y, width: task.width, height: task.height },
        focusAffine,
      ),
    );
  }

  for (const frame of scene.frames) {
    // Chromeless focused frame path is focusPath.slice(0, -1), so pathStartsWith skips it.
    if (!pathStartsWith(frame.path, focusPath)) continue;
    contentRects.push(
      worldRectToView(
        { x: frame.x, y: frame.y, width: frame.width, height: frame.height },
        focusAffine,
      ),
    );
  }

  const union = unionRects(contentRects);
  if (union) return union;

  if (focusPath.length > 0) {
    const focusedRect = focusWorldRect(scene, focusPath);
    if (focusedRect) return worldRectToView(focusedRect, focusAffine);
  }

  return null;
}

function ancestorFrameSceneIds(path: ScenePath): string[] {
  const ids: string[] = [];
  for (let i = 0; i < path.length; i++) {
    const linkId = path[i];
    if (linkId) ids.push(sceneFrameId(path.slice(0, i), linkId));
  }
  return ids;
}

/** Scene ids blocked by an incomplete incoming prerequisite (tasks and frames only). */
export function nonStartableSceneIds(scene: Scene): Set<string> {
  const statusBySceneId = new Map<string, TaskStatus>();
  for (const task of scene.tasks) {
    statusBySceneId.set(task.sceneId, task.task.status);
  }
  for (const frame of scene.frames) {
    statusBySceneId.set(frame.sceneId, frame.subPlan.status);
  }

  const incoming = new Map<string, string[]>();
  for (const edge of scene.edges) {
    const list = incoming.get(edge.targetSceneId) ?? [];
    list.push(edge.sourceSceneId);
    incoming.set(edge.targetSceneId, list);
  }

  const blocked = new Set<string>();

  const evaluate = (sceneId: string, path: ScenePath) => {
    const targets = [sceneId, ...ancestorFrameSceneIds(path)];
    for (const target of targets) {
      for (const source of incoming.get(target) ?? []) {
        if (statusBySceneId.get(source) !== "done") {
          blocked.add(sceneId);
          return;
        }
      }
    }
  };

  for (const task of scene.tasks) evaluate(task.sceneId, task.path);
  for (const frame of scene.frames) evaluate(frame.sceneId, frame.path);

  return blocked;
}

/** When the scene root is externally blocked, every task and frame is non-startable. */
export function applyExternalBlock(
  nonStartable: Set<string>,
  scene: Scene,
  externallyBlocked: boolean,
): Set<string> {
  if (!externallyBlocked) return nonStartable;
  const blocked = new Set(nonStartable);
  for (const task of scene.tasks) blocked.add(task.sceneId);
  for (const frame of scene.frames) blocked.add(frame.sceneId);
  return blocked;
}

function frameHasVisibleInterior(
  scene: Scene,
  frame: SceneFrame,
  visible: Set<string>,
): boolean {
  const prefix = [...frame.path, frame.linkId];
  const hasInteriorTask = scene.tasks.some(
    (t) => visible.has(t.sceneId) && pathStartsWith(t.path, prefix),
  );
  const hasInteriorFrame = scene.frames.some(
    (f) =>
      f.sceneId !== frame.sceneId &&
      visible.has(f.sceneId) &&
      pathStartsWith(f.path, prefix),
  );
  return hasInteriorTask || hasInteriorFrame;
}

/** Hide non-startable nodes; prune frames with no remaining visible interiors. */
export function applyStartableOnlyFilter(
  scene: Scene,
  base: { visible: Set<string>; chromeless: Set<string> },
  startableOnly: boolean,
  externallyBlocked = false,
): { visible: Set<string>; chromeless: Set<string> } {
  if (!startableOnly) return base;

  const nonStartable = applyExternalBlock(nonStartableSceneIds(scene), scene, externallyBlocked);
  const visible = new Set<string>();

  for (const task of scene.tasks) {
    if (!base.visible.has(task.sceneId)) continue;
    if (nonStartable.has(task.sceneId)) continue;
    visible.add(task.sceneId);
  }
  for (const frame of scene.frames) {
    if (!base.visible.has(frame.sceneId)) continue;
    if (nonStartable.has(frame.sceneId) && !base.chromeless.has(frame.sceneId)) continue;
    visible.add(frame.sceneId);
  }

  const framesByDepth = [...scene.frames]
    .filter((f) => visible.has(f.sceneId) && !base.chromeless.has(f.sceneId))
    .sort((a, b) => b.path.length - a.path.length);

  for (const frame of framesByDepth) {
    if (!frameHasVisibleInterior(scene, frame, visible)) {
      visible.delete(frame.sceneId);
    }
  }

  const finalVisible = new Set<string>();
  for (const task of scene.tasks) {
    if (visible.has(task.sceneId)) finalVisible.add(task.sceneId);
  }
  for (const frame of scene.frames) {
    if (visible.has(frame.sceneId)) finalVisible.add(frame.sceneId);
  }
  for (const edge of scene.edges) {
    if (finalVisible.has(edge.sourceSceneId) && finalVisible.has(edge.targetSceneId)) {
      finalVisible.add(edge.sceneId);
    }
  }

  return { visible: finalVisible, chromeless: base.chromeless };
}

function frameWorldContentRect(frame: SceneFrame): Rect {
  return {
    x: frame.x,
    y: frame.y + FRAME_HEADER_HEIGHT * frame.scale,
    width: frame.width,
    height: Math.max(0, frame.height - FRAME_HEADER_HEIGHT * frame.scale),
  };
}

function applyRefitTransformToTask(task: SceneTask, transform: Affine): SceneTask {
  const pos = applyAffine({ x: task.x, y: task.y }, transform);
  return {
    ...task,
    x: pos.x,
    y: pos.y,
    width: task.width * transform.scale,
    height: task.height * transform.scale,
    scale: task.scale * transform.scale,
  };
}

function applyRefitTransformToFrame(frame: SceneFrame, transform: Affine): SceneFrame {
  const pos = applyAffine({ x: frame.x, y: frame.y }, transform);
  return {
    ...frame,
    x: pos.x,
    y: pos.y,
    width: frame.width * transform.scale,
    height: frame.height * transform.scale,
    scale: frame.scale * transform.scale,
    contentScale: frame.contentScale * transform.scale,
  };
}

/** Display-only: scale and translate visible frame interiors to fill each frame's content area. */
export function refitVisibleFrameContents(
  scene: Scene,
  visible: Set<string>,
  chromeless: Set<string>,
): Scene {
  const tasks = scene.tasks.map((t) => ({ ...t, path: [...t.path] }));
  const frames = scene.frames.map((f) => ({ ...f, path: [...f.path], subPlan: { ...f.subPlan } }));
  const edges = scene.edges.map((e) => ({ ...e, path: [...e.path] }));

  const taskById = new Map(tasks.map((t) => [t.sceneId, t]));
  const frameById = new Map(frames.map((f) => [f.sceneId, f]));

  const refitFrames = frames
    .filter((f) => visible.has(f.sceneId) && !chromeless.has(f.sceneId))
    .sort((a, b) => b.path.length - a.path.length);

  for (const frame of refitFrames) {
    const prefix = [...frame.path, frame.linkId];
    const interiorTaskIds: string[] = [];
    const interiorFrameIds: string[] = [];

    for (const task of tasks) {
      if (!visible.has(task.sceneId)) continue;
      if (!pathStartsWith(task.path, prefix)) continue;
      interiorTaskIds.push(task.sceneId);
    }
    for (const inner of frames) {
      if (inner.sceneId === frame.sceneId) continue;
      if (!visible.has(inner.sceneId)) continue;
      if (!pathStartsWith(inner.path, prefix)) continue;
      interiorFrameIds.push(inner.sceneId);
    }

    if (interiorTaskIds.length === 0 && interiorFrameIds.length === 0) continue;

    const interiorRects: Rect[] = [];
    for (const id of interiorTaskIds) {
      const task = taskById.get(id)!;
      interiorRects.push({ x: task.x, y: task.y, width: task.width, height: task.height });
    }
    for (const id of interiorFrameIds) {
      const inner = frameById.get(id)!;
      interiorRects.push({ x: inner.x, y: inner.y, width: inner.width, height: inner.height });
    }

    const hull = unionRects(interiorRects);
    if (!hull) continue;

    const transform = focusTransform(hull, frameWorldContentRect(frame));

    for (const id of interiorTaskIds) {
      taskById.set(id, applyRefitTransformToTask(taskById.get(id)!, transform));
    }
    for (const id of interiorFrameIds) {
      frameById.set(id, applyRefitTransformToFrame(frameById.get(id)!, transform));
    }
  }

  return {
    tasks: tasks.map((t) => taskById.get(t.sceneId)!),
    frames: frames.map((f) => frameById.get(f.sceneId)!),
    edges,
  };
}
