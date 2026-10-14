import { describe, expect, it } from "vitest";
import {
  asHistoryPayload,
  buildGeometryPayload,
  canCoalesceHistory,
  changedEntityKeys,
  type GeometryPayload,
  type HistoryPayload,
  type PlanSnapshot,
  type SnapshotPayload,
} from "./history";

const basePlan = (id: string): PlanSnapshot["plan"] => ({
  id,
  name: "Plan",
  description: "",
  status: "todo",
  effort: null,
  deadline: null,
  notes: "",
  ownerKerberos: "ab",
  visibility: "shared",
});

function snap(overrides: Partial<PlanSnapshot> & { plan: PlanSnapshot["plan"] }): PlanSnapshot {
  return {
    tasks: [],
    edges: [],
    links: [],
    ...overrides,
  };
}

function snapshotPayload(plan: PlanSnapshot): SnapshotPayload {
  return { kind: "snapshot", plan };
}

function historyEntry(
  planId: string,
  before: HistoryPayload,
  after: HistoryPayload,
): { planId: string; beforeState: HistoryPayload; afterState: HistoryPayload } {
  return { planId, beforeState: before, afterState: after };
}

describe("asHistoryPayload", () => {
  it("rejects untagged plan snapshots", () => {
    expect(asHistoryPayload(snap({ plan: basePlan("p1") }))).toBeNull();
  });

  it("passes through tagged snapshot and geometry payloads", () => {
    const geometry: GeometryPayload = {
      kind: "geometry",
      tasks: [{ id: "t1", x: 0, y: 0, width: 240, height: 84 }],
      links: [],
    };
    expect(asHistoryPayload(geometry)).toEqual(geometry);
    const tagged = snapshotPayload(snap({ plan: basePlan("p1") }));
    expect(asHistoryPayload(tagged)).toEqual(tagged);
  });
});

describe("buildGeometryPayload", () => {
  it("tags task and link pose arrays", () => {
    const task = { id: "t1", x: 1, y: 2, width: 240, height: 84 };
    const link = { id: "l1", x: 3, y: 4, width: 360, height: 240 };
    expect(buildGeometryPayload([task], [link])).toEqual({
      kind: "geometry",
      tasks: [task],
      links: [link],
    });
  });
});

describe("changedEntityKeys", () => {
  it("detects a single task edit", () => {
    const before = snap({
      plan: basePlan("p1"),
      tasks: [
        {
          id: "t1",
          planId: "p1",
          title: "A",
          status: "todo",
          notes: "",
          deadline: null,
          effort: null,
          x: 0,
          y: 0,
          width: 240,
          height: 84,
          assignees: [],
        },
      ],
    });
    const after = snap({
      plan: basePlan("p1"),
      tasks: [{ ...before.tasks[0]!, title: "B" }],
    });
    expect(changedEntityKeys(before, after)).toEqual([{ kind: "task", id: "t1" }]);
  });

  it("detects multiple task moves as separate task changes", () => {
    const task = (id: string, x: number): PlanSnapshot["tasks"][number] => ({
      id,
      planId: "p1",
      title: id,
      status: "todo",
      notes: "",
      deadline: null,
      effort: null,
      x,
      y: 0,
      width: 240,
      height: 84,
      assignees: [],
    });
    const before = snap({
      plan: basePlan("p1"),
      tasks: [task("t1", 0), task("t2", 0)],
    });
    const after = snap({
      plan: basePlan("p1"),
      tasks: [task("t1", 10), task("t2", 20)],
    });
    const keys = changedEntityKeys(before, after);
    expect(keys).toHaveLength(2);
    expect(keys).toContainEqual({ kind: "task", id: "t1" });
    expect(keys).toContainEqual({ kind: "task", id: "t2" });
  });
});

describe("canCoalesceHistory", () => {
  it("merges consecutive single-task edits on the same task", () => {
    const before = snap({ plan: basePlan("p1") });
    const mid = snap({
      plan: basePlan("p1"),
      tasks: [
        {
          id: "t1",
          planId: "p1",
          title: "A",
          status: "todo",
          notes: "",
          deadline: null,
          effort: null,
          x: 0,
          y: 0,
          width: 240,
          height: 84,
          assignees: [],
        },
      ],
    });
    const after = snap({
      plan: basePlan("p1"),
      tasks: [{ ...mid.tasks[0]!, title: "B" }],
    });
    const latest = historyEntry("p1", snapshotPayload(before), snapshotPayload(mid));
    const next = {
      ...historyEntry("p1", snapshotPayload(mid), snapshotPayload(after)),
      coalesce: { taskId: "t1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(true);
  });

  it("does not merge when latest entry is geometry", () => {
    const geometry: GeometryPayload = {
      kind: "geometry",
      tasks: [{ id: "t1", x: 0, y: 0, width: 240, height: 84 }],
      links: [],
    };
    const afterGeometry: GeometryPayload = {
      kind: "geometry",
      tasks: [{ id: "t1", x: 10, y: 0, width: 240, height: 84 }],
      links: [],
    };
    const latest = historyEntry("p1", geometry, afterGeometry);
    const next = {
      ...historyEntry(
        "p1",
        snapshotPayload(snap({ plan: basePlan("p1") })),
        snapshotPayload(snap({ plan: basePlan("p1") })),
      ),
      coalesce: { taskId: "t1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(false);
  });

  it("does not merge when multiple tasks changed", () => {
    const task = (id: string, x: number) => ({
      id,
      planId: "p1",
      title: id,
      status: "todo" as const,
      notes: "",
      deadline: null,
      effort: null,
      x,
      y: 0,
      width: 240,
      height: 84,
      assignees: [] as string[],
    });
    const before = snap({ plan: basePlan("p1"), tasks: [task("t1", 0), task("t2", 0)] });
    const after = snap({ plan: basePlan("p1"), tasks: [task("t1", 1), task("t2", 2)] });
    const latest = historyEntry("p1", snapshotPayload(before), snapshotPayload(after));
    const next = {
      ...historyEntry(
        "p1",
        snapshotPayload(after),
        snapshotPayload(snap({ plan: basePlan("p1"), tasks: [task("t1", 3), task("t2", 4)] })),
      ),
      coalesce: { taskId: "t1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(false);
  });

  it("merges consecutive plan meta edits on the same plan", () => {
    const before = snap({ plan: basePlan("p1") });
    const mid = snap({ plan: { ...basePlan("p1"), name: "Plan A" } });
    const after = snap({ plan: { ...basePlan("p1"), name: "Plan B" } });
    const latest = historyEntry("p1", snapshotPayload(before), snapshotPayload(mid));
    const next = {
      ...historyEntry("p1", snapshotPayload(mid), snapshotPayload(after)),
      coalesce: { planId: "p1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(true);
  });

  it("merges consecutive link position edits on the same link", () => {
    const link = (id: string, x: number): PlanSnapshot["links"][number] => ({
      id,
      parentPlanId: "p1",
      childPlanId: "c1",
      x,
      y: 0,
      width: 360,
      height: 240,
      child: snap({ plan: basePlan("c1") }),
    });
    const before = snap({ plan: basePlan("p1"), links: [link("l1", 0)] });
    const mid = snap({ plan: basePlan("p1"), links: [link("l1", 10)] });
    const after = snap({ plan: basePlan("p1"), links: [link("l1", 20)] });
    const latest = historyEntry("p1", snapshotPayload(before), snapshotPayload(mid));
    const next = {
      ...historyEntry("p1", snapshotPayload(mid), snapshotPayload(after)),
      coalesce: { linkId: "l1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(true);
  });

  it("does not merge when multiple plans changed", () => {
    const before = snap({
      plan: basePlan("p1"),
      links: [
        {
          id: "l1",
          parentPlanId: "p1",
          childPlanId: "c1",
          x: 0,
          y: 0,
          width: 360,
          height: 240,
          child: snap({ plan: basePlan("c1") }),
        },
        {
          id: "l2",
          parentPlanId: "p1",
          childPlanId: "c2",
          x: 0,
          y: 0,
          width: 360,
          height: 240,
          child: snap({ plan: basePlan("c2") }),
        },
      ],
    });
    const after = snap({
      plan: basePlan("p1"),
      links: [
        {
          ...before.links[0]!,
          child: snap({ plan: { ...basePlan("c1"), name: "Child A" } }),
        },
        {
          ...before.links[1]!,
          child: snap({ plan: { ...basePlan("c2"), name: "Child B" } }),
        },
      ],
    });
    const latest = historyEntry("p1", snapshotPayload(before), snapshotPayload(after));
    const next = {
      ...historyEntry(
        "p1",
        snapshotPayload(after),
        snapshotPayload(
          snap({
            plan: basePlan("p1"),
            links: [
              {
                ...after.links[0]!,
                child: snap({ plan: { ...basePlan("c1"), name: "Child A2" } }),
              },
              {
                ...after.links[1]!,
                child: snap({ plan: { ...basePlan("c2"), name: "Child B2" } }),
              },
            ],
          }),
        ),
      ),
      coalesce: { planId: "c1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(false);
  });

  it("does not merge when multiple links changed", () => {
    const link = (id: string, x: number): PlanSnapshot["links"][number] => ({
      id,
      parentPlanId: "p1",
      childPlanId: `c-${id}`,
      x,
      y: 0,
      width: 360,
      height: 240,
      child: snap({ plan: basePlan(`c-${id}`) }),
    });
    const before = snap({ plan: basePlan("p1"), links: [link("l1", 0), link("l2", 0)] });
    const after = snap({ plan: basePlan("p1"), links: [link("l1", 10), link("l2", 20)] });
    const latest = historyEntry("p1", snapshotPayload(before), snapshotPayload(after));
    const next = {
      ...historyEntry(
        "p1",
        snapshotPayload(after),
        snapshotPayload(snap({ plan: basePlan("p1"), links: [link("l1", 30), link("l2", 40)] })),
      ),
      coalesce: { linkId: "l1" },
    };
    expect(canCoalesceHistory(latest, next)).toBe(false);
  });
});
