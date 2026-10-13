import { describe, expect, it } from "vitest";
import { applyAffine, DEFAULT_FOCUS_PADDING, invertAffine } from "./plan-focus";
import {
  applyExternalBlock,
  applyStartableOnlyFilter,
  computeRecursiveRollup,
  containerAffine,
  flattenScene,
  focusContentViewRect,
  FRAME_HEADER_HEIGHT,
  nonStartableSceneIds,
  refitVisibleFrameContents,
  sceneEdgeId,
  sceneFrameId,
  sceneTaskId,
  unionAssignees,
  visibleSceneIds,
  type Scene,
  type SceneGraphInput,
} from "./plan-scene";
import type { EdgeDto, SubPlanDto, TaskDto } from "./types";

const EPS = 5;

function emptyRollup() {
  return { total: 0, done: 0, blocked: 0, nearestDeadline: null };
}

function task(
  id: string,
  planId: string,
  overrides: Partial<TaskDto> = {},
): TaskDto {
  return {
    id,
    planId,
    title: id,
    status: "todo",
    notes: "",
    deadline: null,
    effort: null,
    x: 0,
    y: 0,
    width: 240,
    height: 84,
    assignees: [],
    ...overrides,
  };
}

function subPlan(
  linkId: string,
  parentPlanId: string,
  childPlanId: string,
  overrides: Partial<SubPlanDto> = {},
): SubPlanDto {
  return {
    linkId,
    parentPlanId,
    childPlanId,
    name: linkId,
    description: "",
    x: 0,
    y: 0,
    width: 360,
    height: 240,
    assignees: [],
    rollup: emptyRollup(),
    status: "todo",
    effort: null,
    deadline: null,
    notes: "",
    tasks: [],
    edges: [],
    subPlans: [],
    ...overrides,
  };
}

function edge(
  id: string,
  fromKind: EdgeDto["fromKind"],
  fromId: string,
  toKind: EdgeDto["toKind"],
  toId: string,
): EdgeDto {
  return { id, fromKind, fromId, toKind, toId };
}

function childPlanTasks(childPlanId: string): TaskDto[] {
  return [
    task("shared-a", childPlanId, { x: 20, y: 30 }),
    task("shared-b", childPlanId, { x: 120, y: 90 }),
  ];
}

function diamondGraph(): SceneGraphInput {
  const rootPlanId = "root-plan";
  const childPlanId = "shared-child";
  const sharedTasks = childPlanTasks(childPlanId);
  const sharedEdges = [edge("shared-edge", "task", "shared-a", "task", "shared-b")];

  const embed = (linkId: string, x: number): SubPlanDto =>
    subPlan(linkId, rootPlanId, childPlanId, {
      x,
      y: 40,
      tasks: sharedTasks.map((t) => ({ ...t })),
      edges: sharedEdges.map((e) => ({ ...e })),
    });

  return {
    plan: { id: rootPlanId },
    tasks: [task("root-only", rootPlanId, { x: 500, y: 10 })],
    edges: [],
    subPlans: [embed("link-left", 0), embed("link-right", 420)],
  };
}

function nestedFocusGraph(): SceneGraphInput {
  const rootPlanId = "root";
  const planA = "plan-a";
  const planB = "plan-b";

  const frameB = subPlan("link-b", planA, planB, {
    x: 40,
    y: 120,
    width: 280,
    height: 200,
    tasks: [task("task-in-b", planB, { x: 30, y: 20 })],
  });

  const frameA = subPlan("link-a", rootPlanId, planA, {
    x: 80,
    y: 60,
    width: 360,
    height: 280,
    tasks: [task("task-in-a", planA, { x: 50, y: 40 })],
    subPlans: [frameB],
    edges: [edge("edge-in-a", "task", "task-in-a", "plan_link", "link-b")],
  });

  return {
    plan: { id: rootPlanId },
    tasks: [task("root-task", rootPlanId, { x: 10, y: 10 })],
    edges: [edge("root-to-a", "task", "root-task", "plan_link", "link-a")],
    subPlans: [frameA],
  };
}

function cloneGraph(graph: SceneGraphInput): SceneGraphInput {
  const cloneTask = (t: TaskDto): TaskDto => ({ ...t, assignees: [...t.assignees] });
  const cloneSubPlan = (sp: SubPlanDto): SubPlanDto => ({
    ...sp,
    assignees: [...sp.assignees],
    tasks: sp.tasks.map(cloneTask),
    edges: sp.edges.map((e) => ({ ...e })),
    subPlans: sp.subPlans.map(cloneSubPlan),
  });

  return {
    plan: { ...graph.plan },
    tasks: graph.tasks.map(cloneTask),
    edges: graph.edges.map((e) => ({ ...e })),
    subPlans: graph.subPlans.map(cloneSubPlan),
  };
}

function cloneScene(scene: Scene): Scene {
  return {
    tasks: scene.tasks.map((t) => ({ ...t, path: [...t.path], task: { ...t.task, assignees: [...t.task.assignees] } })),
    frames: scene.frames.map((f) => ({
      ...f,
      path: [...f.path],
      subPlan: cloneGraph({ plan: { id: f.subPlan.childPlanId }, tasks: [], edges: [], subPlans: [f.subPlan] }).subPlans[0]!,
    })),
    edges: scene.edges.map((e) => ({ ...e, path: [...e.path] })),
  };
}

function sceneSnapshot(scene: Scene) {
  return {
    tasks: scene.tasks.map((t) => ({
      sceneId: t.sceneId,
      x: t.x,
      y: t.y,
      scale: t.scale,
      path: t.path,
    })),
    frames: scene.frames.map((f) => ({
      sceneId: f.sceneId,
      x: f.x,
      y: f.y,
      width: f.width,
      height: f.height,
      scale: f.scale,
      contentScale: f.contentScale,
      path: f.path,
    })),
    edges: scene.edges.map((e) => ({
      sceneId: e.sceneId,
      sourceSceneId: e.sourceSceneId,
      targetSceneId: e.targetSceneId,
      path: e.path,
    })),
  };
}

function allSceneIds(scene: Scene): string[] {
  return [
    ...scene.tasks.map((t) => t.sceneId),
    ...scene.frames.map((f) => f.sceneId),
    ...scene.edges.map((e) => e.sceneId),
  ];
}

function expectFiniteScene(scene: Scene): void {
  for (const node of [...scene.tasks, ...scene.frames]) {
    expect(Number.isFinite(node.x)).toBe(true);
    expect(Number.isFinite(node.y)).toBe(true);
    expect(Number.isNaN(node.x)).toBe(false);
    expect(Number.isNaN(node.y)).toBe(false);
  }
  for (const frame of scene.frames) {
    expect(Number.isFinite(frame.width)).toBe(true);
    expect(Number.isFinite(frame.height)).toBe(true);
    expect(Number.isFinite(frame.scale)).toBe(true);
    expect(Number.isFinite(frame.contentScale)).toBe(true);
  }
}

function frameWorldContentRect(
  frame: Scene["frames"][number],
  graph: SceneGraphInput,
): { x: number; y: number; width: number; height: number } {
  const parentAffine = containerAffine(graph, frame.path);
  const contentRect = {
    x: frame.subPlan.x,
    y: frame.subPlan.y + FRAME_HEADER_HEIGHT,
    width: frame.subPlan.width,
    height: frame.subPlan.height - FRAME_HEADER_HEIGHT,
  };
  const topLeft = applyAffine({ x: contentRect.x, y: contentRect.y }, parentAffine);
  const scale = parentAffine.scale;
  return {
    x: topLeft.x,
    y: topLeft.y,
    width: contentRect.width * scale,
    height: contentRect.height * scale,
  };
}

function expectRectInside(
  inner: { x: number; y: number; width: number; height: number },
  outer: { x: number; y: number; width: number; height: number },
  tolerance: number,
): void {
  expect(inner.x).toBeGreaterThanOrEqual(outer.x - tolerance);
  expect(inner.y).toBeGreaterThanOrEqual(outer.y - tolerance);
  expect(inner.x + inner.width).toBeLessThanOrEqual(outer.x + outer.width + tolerance);
  expect(inner.y + inner.height).toBeLessThanOrEqual(outer.y + outer.height + tolerance);
}

describe("the same child plan embedded under two parents renders two complete unique scenes", () => {
  it("flattens two full copies with no colliding scene ids", () => {
    const scene = flattenScene(diamondGraph());
    const ids = allSceneIds(scene);
    const unique = new Set(ids);

    expect(unique.size).toBe(ids.length);

    const leftTasks = scene.tasks.filter((t) => t.path[0] === "link-left");
    const rightTasks = scene.tasks.filter((t) => t.path[0] === "link-right");
    expect(leftTasks).toHaveLength(2);
    expect(rightTasks).toHaveLength(2);

    expect(leftTasks.map((t) => t.taskId).sort()).toEqual(["shared-a", "shared-b"]);
    expect(rightTasks.map((t) => t.taskId).sort()).toEqual(["shared-a", "shared-b"]);

    expect(scene.frames.some((f) => f.sceneId === sceneFrameId([], "link-left"))).toBe(true);
    expect(scene.frames.some((f) => f.sceneId === sceneFrameId([], "link-right"))).toBe(true);
    expect(scene.frames.some((f) => f.sceneId === sceneFrameId(["link-left"], "link-left"))).toBe(false);

    const leftEdge = scene.edges.find((e) => e.path[0] === "link-left");
    const rightEdge = scene.edges.find((e) => e.path[0] === "link-right");
    expect(leftEdge?.sceneId).toBe(sceneEdgeId(["link-left"], "shared-edge"));
    expect(rightEdge?.sceneId).toBe(sceneEdgeId(["link-right"], "shared-edge"));
    expect(leftEdge?.sceneId).not.toBe(rightEdge?.sceneId);
  });
});

describe("flattening the same nested graph is deterministic", () => {
  it("yields identical ids, order, and coordinates on repeat and clone", () => {
    const graph = nestedFocusGraph();
    const first = flattenScene(graph);
    const second = flattenScene(graph);
    const cloned = flattenScene(cloneGraph(graph));

    expect(sceneSnapshot(first)).toEqual(sceneSnapshot(second));
    expect(sceneSnapshot(first)).toEqual(sceneSnapshot(cloned));
  });
});

describe("focus never moves anything in the scene", () => {
  it("leaves the scene unchanged and preserves world coordinates for still-visible nodes", () => {
    const graph = nestedFocusGraph();
    const scene = flattenScene(graph);
    const before = cloneScene(scene);
    const rootVisible = visibleSceneIds(scene, []);
    const focusedVisible = visibleSceneIds(scene, ["link-a"]);

    expect(sceneSnapshot(scene)).toEqual(sceneSnapshot(before));

    for (const task of scene.tasks) {
      if (rootVisible.visible.has(task.sceneId) && focusedVisible.visible.has(task.sceneId)) {
        const original = before.tasks.find((t) => t.sceneId === task.sceneId)!;
        expect(task.x).toBeCloseTo(original.x, EPS);
        expect(task.y).toBeCloseTo(original.y, EPS);
        expect(task.scale).toBeCloseTo(original.scale, EPS);
      }
    }
    for (const frame of scene.frames) {
      if (rootVisible.visible.has(frame.sceneId) && focusedVisible.visible.has(frame.sceneId)) {
        const original = before.frames.find((f) => f.sceneId === frame.sceneId)!;
        expect(frame.x).toBeCloseTo(original.x, EPS);
        expect(frame.y).toBeCloseTo(original.y, EPS);
        expect(frame.width).toBeCloseTo(original.width, EPS);
        expect(frame.height).toBeCloseTo(original.height, EPS);
      }
    }
  });
});

describe("focusing a sub-plan hides the outside world and keeps its subtree", () => {
  it("hides root siblings, keeps nested contents, and marks the focused frame chromeless", () => {
    const scene = flattenScene(nestedFocusGraph());
    const { visible, chromeless } = visibleSceneIds(scene, ["link-a"]);

    expect(visible.has(sceneTaskId([], "root-task"))).toBe(false);
    expect(visible.has(sceneEdgeId([], "root-to-a"))).toBe(false);

    expect(visible.has(sceneFrameId([], "link-a"))).toBe(true);
    expect(chromeless.has(sceneFrameId([], "link-a"))).toBe(true);
    expect(chromeless.size).toBe(1);

    expect(visible.has(sceneTaskId(["link-a"], "task-in-a"))).toBe(true);
    expect(visible.has(sceneFrameId(["link-a"], "link-b"))).toBe(true);
    expect(visible.has(sceneTaskId(["link-a", "link-b"], "task-in-b"))).toBe(true);
    expect(visible.has(sceneEdgeId(["link-a"], "edge-in-a"))).toBe(true);
  });
});

describe("edges vanish when either endpoint is hidden", () => {
  it("shows a root-to-frame edge at root focus and hides it when drilled into the frame", () => {
    const scene = flattenScene(nestedFocusGraph());
    const edgeId = sceneEdgeId([], "root-to-a");

    expect(visibleSceneIds(scene, []).visible.has(edgeId)).toBe(true);
    expect(visibleSceneIds(scene, ["link-a"]).visible.has(edgeId)).toBe(false);
  });
});

describe("nested contents stay inside their parent frame content rect", () => {
  it("keeps every child task and frame within the letterboxed content area", () => {
    const graph = nestedFocusGraph();
    const scene = flattenScene(graph);

    for (const frame of scene.frames) {
      const content = frameWorldContentRect(frame, graph);
      const childPathPrefix = [...frame.path, frame.linkId];
      const paddingTolerance = DEFAULT_FOCUS_PADDING * frame.contentScale + 1;

      for (const taskNode of scene.tasks) {
        if (taskNode.path.length !== childPathPrefix.length) continue;
        if (!taskNode.path.every((id, i) => id === childPathPrefix[i])) continue;

        expectRectInside(
          {
            x: taskNode.x,
            y: taskNode.y,
            width: taskNode.width,
            height: taskNode.height,
          },
          content,
          paddingTolerance,
        );
      }

      for (const childFrame of scene.frames) {
        if (childFrame.path.length !== childPathPrefix.length) continue;
        if (!childFrame.path.every((id, i) => id === childPathPrefix[i])) continue;

        expectRectInside(
          {
            x: childFrame.x,
            y: childFrame.y,
            width: childFrame.width,
            height: childFrame.height,
          },
          content,
          paddingTolerance,
        );
      }
    }
  });
});

describe("canonical coordinates round-trip through the container affine", () => {
  it("maps a nested task to world space and back without drift", () => {
    const graph = nestedFocusGraph();
    const scene = flattenScene(graph);
    const nested = scene.tasks.find((t) => t.sceneId === sceneTaskId(["link-a", "link-b"], "task-in-b"))!;

    const affine = containerAffine(graph, nested.path);
    const mapped = applyAffine({ x: nested.task.x, y: nested.task.y }, affine);
    expect(mapped.x).toBeCloseTo(nested.x, EPS);
    expect(mapped.y).toBeCloseTo(nested.y, EPS);

    const inverse = invertAffine(affine);
    const back = applyAffine({ x: nested.x, y: nested.y }, inverse);
    expect(back.x).toBeCloseTo(nested.task.x, EPS);
    expect(back.y).toBeCloseTo(nested.task.y, EPS);
  });
});

describe("empty sub-plans do not break flattening", () => {
  it("still emits a frame node with finite coordinates", () => {
    const graph: SceneGraphInput = {
      plan: { id: "root" },
      tasks: [],
      edges: [],
      subPlans: [
        subPlan("empty-frame", "root", "empty-child", {
          x: 100,
          y: 50,
          width: 360,
          height: 240,
          tasks: [],
          subPlans: [],
        }),
      ],
    };

    const scene = flattenScene(graph);
    expectFiniteScene(scene);
    expect(scene.frames).toHaveLength(1);
    expect(scene.frames[0]!.sceneId).toBe(sceneFrameId([], "empty-frame"));
    expect(scene.tasks).toHaveLength(0);
  });
});

describe("derived assignees are a sorted union across tasks and child frames", () => {
  it("dedupes assignees from direct tasks and nested unions and sorts them", () => {
    expect(unionAssignees([], [])).toEqual([]);
    expect(unionAssignees([["ZZ", "AA"], ["MM", "AA"]], [["BB"], ["MM", "CC"]])).toEqual([
      "AA",
      "BB",
      "CC",
      "MM",
      "ZZ",
    ]);
  });
});

describe("recursive rollup counts every descendant task once", () => {
  it("totals nested tasks, status counts, and the earliest incomplete deadline", () => {
    const rollup = computeRecursiveRollup(
      [
        { status: "done", deadline: "2026-01-01T00:00:00.000Z" },
        { status: "blocked", deadline: "2026-03-01T00:00:00.000Z" },
        { status: "todo", deadline: "2026-02-15T00:00:00.000Z" },
      ],
      [
        { total: 2, done: 1, blocked: 0, nearestDeadline: "2026-04-10T00:00:00.000Z" },
        { total: 1, done: 1, blocked: 0, nearestDeadline: "2026-05-01T00:00:00.000Z" },
      ],
    );

    expect(rollup.total).toBe(6);
    expect(rollup.done).toBe(3);
    expect(rollup.blocked).toBe(1);
    expect(rollup.nearestDeadline).toBe("2026-02-15T00:00:00.000Z");
  });
});

describe("focus content rect frames every child inside the focused plan", () => {
  it("unions nested tasks and frames while excluding the chromeless focused frame", () => {
    const graph = nestedFocusGraph();
    const scene = flattenScene(graph);
    const focusPath = ["link-a"];
    const focusAffine = containerAffine(graph, focusPath);
    const rect = focusContentViewRect(scene, focusPath, focusAffine);

    expect(rect).not.toBeNull();

    const taskInA = scene.tasks.find((t) => t.sceneId === sceneTaskId(["link-a"], "task-in-a"))!;
    const frameB = scene.frames.find((f) => f.sceneId === sceneFrameId(["link-a"], "link-b"))!;
    const chromelessA = scene.frames.find((f) => f.sceneId === sceneFrameId([], "link-a"))!;

    const focusScale = focusAffine.scale;
    const inv = invertAffine(focusAffine);
    const project = (node: { x: number; y: number; width: number; height: number }) => {
      const topLeft = applyAffine({ x: node.x, y: node.y }, inv);
      return {
        x: topLeft.x,
        y: topLeft.y,
        width: node.width / focusScale,
        height: node.height / focusScale,
      };
    };

    const taskView = project(taskInA);
    const frameBView = project(frameB);
    const chromelessView = project(chromelessA);

    expect(rect!.x).toBeLessThanOrEqual(Math.min(taskView.x, frameBView.x) + EPS);
    expect(rect!.y).toBeLessThanOrEqual(Math.min(taskView.y, frameBView.y) + EPS);
    expect(rect!.x + rect!.width).toBeGreaterThanOrEqual(
      Math.max(taskView.x + taskView.width, frameBView.x + frameBView.width) - EPS,
    );
    expect(rect!.y + rect!.height).toBeGreaterThanOrEqual(
      Math.max(taskView.y + taskView.height, frameBView.y + frameBView.height) - EPS,
    );

    expect(
      rect!.x <= chromelessView.x + EPS &&
        rect!.y <= chromelessView.y + EPS &&
        rect!.x + rect!.width >= chromelessView.x + chromelessView.width - EPS &&
        rect!.y + rect!.height >= chromelessView.y + chromelessView.height - EPS,
    ).toBe(false);
  });

  it("falls back to the focused frame when the plan has no content children", () => {
    const graph: SceneGraphInput = {
      plan: { id: "root" },
      tasks: [],
      edges: [],
      subPlans: [
        subPlan("empty-frame", "root", "empty-child", {
          x: 100,
          y: 50,
          width: 360,
          height: 240,
          tasks: [],
          subPlans: [],
        }),
      ],
    };
    const scene = flattenScene(graph);
    const focusPath = ["empty-frame"];
    const focusAffine = containerAffine(graph, focusPath);
    const rect = focusContentViewRect(scene, focusPath, focusAffine);
    const frame = scene.frames.find((f) => f.sceneId === sceneFrameId([], "empty-frame"))!;

    expect(rect).not.toBeNull();
    expect(rect!.width).toBeCloseTo(frame.width / focusAffine.scale, EPS);
    expect(rect!.height).toBeCloseTo(frame.height / focusAffine.scale, EPS);
  });
});

describe("a node with incomplete prerequisites is not startable", () => {
  it("marks blocked targets and leaves indegree-zero nodes startable", () => {
    const scene = flattenScene(nestedFocusGraph());
    const blocked = nonStartableSceneIds(scene);

    expect(blocked.has(sceneTaskId([], "root-task"))).toBe(false);
    expect(blocked.has(sceneFrameId([], "link-a"))).toBe(true);
    expect(blocked.has(sceneTaskId(["link-a"], "task-in-a"))).toBe(true);
    expect(blocked.has(sceneFrameId(["link-a"], "link-b"))).toBe(true);
    expect(blocked.has(sceneTaskId(["link-a", "link-b"], "task-in-b"))).toBe(true);
  });

  it("clears blocks when every prerequisite is done", () => {
    const graph = nestedFocusGraph();
    graph.tasks = graph.tasks.map((t) =>
      t.id === "root-task" ? { ...t, status: "done" } : t,
    );
    const frameA = graph.subPlans[0]!;
    frameA.tasks = frameA.tasks.map((t) =>
      t.id === "task-in-a" ? { ...t, status: "done" } : t,
    );
    frameA.status = "done";

    const scene = flattenScene(graph);
    const blocked = nonStartableSceneIds(scene);

    expect(blocked.has(sceneFrameId([], "link-a"))).toBe(false);
    expect(blocked.has(sceneTaskId(["link-a"], "task-in-a"))).toBe(false);
    expect(blocked.has(sceneFrameId(["link-a"], "link-b"))).toBe(false);
    expect(blocked.has(sceneTaskId(["link-a", "link-b"], "task-in-b"))).toBe(false);
  });
});

describe("ancestor-frame prerequisites block nested descendants", () => {
  it("treats an incomplete edge into an ancestor frame as blocking a nested task", () => {
    const scene = flattenScene(nestedFocusGraph());
    const blocked = nonStartableSceneIds(scene);

    expect(blocked.has(sceneTaskId(["link-a", "link-b"], "task-in-b"))).toBe(true);
  });

  it("does not block nested tasks when the ancestor prerequisite is done", () => {
    const graph = nestedFocusGraph();
    graph.tasks = graph.tasks.map((t) =>
      t.id === "root-task" ? { ...t, status: "done" } : t,
    );
    const frameA = graph.subPlans[0]!;
    frameA.tasks = frameA.tasks.map((t) =>
      t.id === "task-in-a" ? { ...t, status: "done" } : t,
    );
    frameA.status = "done";

    const scene = flattenScene(graph);
    const blocked = nonStartableSceneIds(scene);

    expect(blocked.has(sceneTaskId(["link-a", "link-b"], "task-in-b"))).toBe(false);
  });
});

describe("startability is evaluated independently per scene embedding", () => {
  it("blocks only the copy reached by an incomplete prerequisite in a diamond graph", () => {
    const graph = diamondGraph();
    graph.tasks = graph.tasks.map((t) =>
      t.id === "root-only" ? { ...t, status: "done" } : t,
    );
    graph.subPlans = graph.subPlans.map((sp) =>
      sp.linkId === "link-left"
        ? {
            ...sp,
            tasks: sp.tasks.map((t) =>
              t.id === "shared-a" ? { ...t, status: "done" } : t,
            ),
          }
        : sp,
    );

    const scene = flattenScene(graph);
    const blocked = nonStartableSceneIds(scene);

    expect(blocked.has(sceneTaskId(["link-left"], "shared-b"))).toBe(false);
    expect(blocked.has(sceneTaskId(["link-right"], "shared-a"))).toBe(false);
    expect(blocked.has(sceneTaskId(["link-right"], "shared-b"))).toBe(true);
  });
});

function largeFrameStartableGraph(): SceneGraphInput {
  const rootPlanId = "root";
  const childPlanId = "child-plan";

  const frame = subPlan("email-frame", rootPlanId, childPlanId, {
    x: 0,
    y: 0,
    width: 800,
    height: 600,
    tasks: [
      task("blocker", childPlanId, { x: 10, y: 10, status: "todo" }),
      task("startable", childPlanId, { x: 20, y: 30, status: "todo" }),
    ],
    edges: [edge("dep", "task", "blocker", "task", "startable")],
  });

  return {
    plan: { id: rootPlanId },
    tasks: [task("root-done", rootPlanId, { x: 500, y: 10, status: "done" })],
    edges: [edge("root-to-frame", "task", "root-done", "plan_link", "email-frame")],
    subPlans: [frame],
  };
}

function emptyAfterFilterFrameGraph(): SceneGraphInput {
  const rootPlanId = "root";
  const planA = "plan-a";
  const planB = "plan-b";

  const innerFrame = subPlan("inner-frame", planA, planB, {
    x: 40,
    y: 80,
    width: 400,
    height: 300,
    tasks: [task("inner-task", planB, { x: 10, y: 10, status: "todo" })],
  });

  const outerFrame = subPlan("outer-frame", rootPlanId, planA, {
    x: 0,
    y: 0,
    width: 800,
    height: 600,
    tasks: [
      task("cycle-a", planA, { x: 10, y: 10, status: "todo" }),
      task("cycle-b", planA, { x: 120, y: 10, status: "todo" }),
    ],
    subPlans: [innerFrame],
    edges: [
      edge("cycle-ab", "task", "cycle-a", "task", "cycle-b"),
      edge("cycle-ba", "task", "cycle-b", "task", "cycle-a"),
      edge("block-inner", "task", "cycle-a", "plan_link", "inner-frame"),
    ],
  });

  return {
    plan: { id: rootPlanId },
    tasks: [task("root-done", rootPlanId, { x: 500, y: 10, status: "done" })],
    edges: [edge("root-to-outer", "task", "root-done", "plan_link", "outer-frame")],
    subPlans: [outerFrame],
  };
}

describe("startable-only filter hides frames with no visible interiors", () => {
  it("removes a frame whose descendants are all non-startable", () => {
    const scene = flattenScene(emptyAfterFilterFrameGraph());
    const base = visibleSceneIds(scene, []);
    const { visible } = applyStartableOnlyFilter(scene, base, true);

    expect(visible.has(sceneFrameId([], "outer-frame"))).toBe(false);
    expect(visible.has(sceneFrameId(["outer-frame"], "inner-frame"))).toBe(false);
    expect(visible.has(sceneTaskId(["outer-frame"], "cycle-a"))).toBe(false);
    expect(visible.has(sceneTaskId(["outer-frame"], "cycle-b"))).toBe(false);
    expect(visible.has(sceneTaskId(["outer-frame", "inner-frame"], "inner-task"))).toBe(false);
    expect(visible.has(sceneTaskId([], "root-done"))).toBe(true);
  });

  it("keeps a frame while a startable descendant remains visible", () => {
    const scene = flattenScene(largeFrameStartableGraph());
    const base = visibleSceneIds(scene, []);
    const { visible } = applyStartableOnlyFilter(scene, base, true);

    expect(visible.has(sceneFrameId([], "email-frame"))).toBe(true);
    expect(visible.has(sceneTaskId(["email-frame"], "blocker"))).toBe(true);
    expect(visible.has(sceneTaskId(["email-frame"], "startable"))).toBe(false);
  });

  it("keeps the chromeless focused frame even when its subtree is empty after filtering", () => {
    const scene = flattenScene(emptyAfterFilterFrameGraph());
    const base = visibleSceneIds(scene, ["outer-frame", "inner-frame"]);
    const { visible, chromeless } = applyStartableOnlyFilter(scene, base, true);

    expect(chromeless.has(sceneFrameId(["outer-frame"], "inner-frame"))).toBe(true);
    expect(visible.has(sceneFrameId(["outer-frame"], "inner-frame"))).toBe(true);
    expect(visible.has(sceneTaskId(["outer-frame", "inner-frame"], "inner-task"))).toBe(false);
  });
});

describe("startable-only refit scales visible interiors to fill frame content", () => {
  it("expands a lone visible task to substantially fill the parent frame content area", () => {
    const graph = largeFrameStartableGraph();
    const scene = flattenScene(graph);
    const base = visibleSceneIds(scene, []);
    const { visible, chromeless } = applyStartableOnlyFilter(scene, base, true);
    const refit = refitVisibleFrameContents(scene, visible, chromeless);

    const frame = refit.frames.find((f) => f.sceneId === sceneFrameId([], "email-frame"))!;
    const taskNode = refit.tasks.find((t) => t.sceneId === sceneTaskId(["email-frame"], "blocker"))!;

    const content = {
      x: frame.x,
      y: frame.y + FRAME_HEADER_HEIGHT * frame.scale,
      width: frame.width,
      height: Math.max(0, frame.height - FRAME_HEADER_HEIGHT * frame.scale),
    };

    const coverageX = taskNode.width / content.width;
    const coverageY = taskNode.height / content.height;
    expect(coverageX).toBeGreaterThan(0.4);
    expect(coverageY).toBeGreaterThan(0.4);

    const taskCenterX = taskNode.x + taskNode.width / 2;
    const taskCenterY = taskNode.y + taskNode.height / 2;
    const contentCenterX = content.x + content.width / 2;
    const contentCenterY = content.y + content.height / 2;
    expect(Math.abs(taskCenterX - contentCenterX)).toBeLessThan(content.width * 0.15);
    expect(Math.abs(taskCenterY - contentCenterY)).toBeLessThan(content.height * 0.15);
  });

  it("does not move or resize the frame itself", () => {
    const graph = largeFrameStartableGraph();
    const scene = flattenScene(graph);
    const originalFrame = scene.frames.find((f) => f.sceneId === sceneFrameId([], "email-frame"))!;
    const base = visibleSceneIds(scene, []);
    const { visible, chromeless } = applyStartableOnlyFilter(scene, base, true);
    const refit = refitVisibleFrameContents(scene, visible, chromeless);
    const frame = refit.frames.find((f) => f.sceneId === sceneFrameId([], "email-frame"))!;

    expect(frame.x).toBeCloseTo(originalFrame.x, EPS);
    expect(frame.y).toBeCloseTo(originalFrame.y, EPS);
    expect(frame.width).toBeCloseTo(originalFrame.width, EPS);
    expect(frame.height).toBeCloseTo(originalFrame.height, EPS);
  });

  it("refits nested frames inside-out", () => {
    const rootPlanId = "root";
    const planA = "plan-a";
    const planB = "plan-b";

    const innerFrame = subPlan("inner", planA, planB, {
      x: 40,
      y: 80,
      width: 400,
      height: 300,
      tasks: [task("inner-task", planB, { x: 10, y: 10 })],
    });

    const outerFrame = subPlan("outer", rootPlanId, planA, {
      x: 0,
      y: 0,
      width: 900,
      height: 700,
      tasks: [task("outer-task", planA, { x: 50, y: 50, status: "done" })],
      subPlans: [innerFrame],
      edges: [edge("outer-dep", "task", "outer-task", "plan_link", "inner")],
    });

    const graph: SceneGraphInput = {
      plan: { id: rootPlanId },
      tasks: [task("root-done", rootPlanId, { x: 500, y: 10, status: "done" })],
      edges: [edge("root-to-outer", "task", "root-done", "plan_link", "outer")],
      subPlans: [outerFrame],
    };

    const scene = flattenScene(graph);
    const base = visibleSceneIds(scene, []);
    const { visible, chromeless } = applyStartableOnlyFilter(scene, base, true);
    const refit = refitVisibleFrameContents(scene, visible, chromeless);

    const outer = refit.frames.find((f) => f.sceneId === sceneFrameId([], "outer"))!;
    const inner = refit.frames.find((f) => f.sceneId === sceneFrameId(["outer"], "inner"))!;
    const innerTask = refit.tasks.find((t) => t.sceneId === sceneTaskId(["outer", "inner"], "inner-task"))!;

    const innerContent = {
      x: inner.x,
      y: inner.y + FRAME_HEADER_HEIGHT * inner.scale,
      width: inner.width,
      height: Math.max(0, inner.height - FRAME_HEADER_HEIGHT * inner.scale),
    };
    expect(innerTask.width / innerContent.width).toBeGreaterThan(0.4);
    expect(innerTask.height / innerContent.height).toBeGreaterThan(0.4);

    const outerContent = {
      x: outer.x,
      y: outer.y + FRAME_HEADER_HEIGHT * outer.scale,
      width: outer.width,
      height: Math.max(0, outer.height - FRAME_HEADER_HEIGHT * outer.scale),
    };
    expectRectInside(
      { x: inner.x, y: inner.y, width: inner.width, height: inner.height },
      outerContent,
      20,
    );
  });
});

describe("applyExternalBlock", () => {
  it("marks every task and frame non-startable when externally blocked", () => {
    const scene = flattenScene(nestedFocusGraph());
    const base = nonStartableSceneIds(scene);
    const blocked = applyExternalBlock(base, scene, true);

    for (const task of scene.tasks) {
      expect(blocked.has(task.sceneId)).toBe(true);
    }
    for (const frame of scene.frames) {
      expect(blocked.has(frame.sceneId)).toBe(true);
    }
  });

  it("leaves the set unchanged when not externally blocked", () => {
    const scene = flattenScene(nestedFocusGraph());
    const base = nonStartableSceneIds(scene);
    const blocked = applyExternalBlock(base, scene, false);

    expect(blocked).toBe(base);
    expect([...blocked].sort()).toEqual([...base].sort());
  });
});
