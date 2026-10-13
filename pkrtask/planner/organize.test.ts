import { describe, expect, it } from "vitest";
import {
  findHistoryRootPlanId,
  isNodeInMovedSet,
  partitionEdgesForMove,
  partitionEdgesForPackage,
  remapPositionsNearAnchor,
  rewriteBoundaryEdgeToPlanLink,
  wouldMoveLinkIntoDescendant,
  type OrganizeEdge,
} from "./organize";

const movedTasks = new Set(["t1", "t2"]);
const movedLinks = new Set(["l1"]);

function edge(
  id: string,
  fromKind: "task" | "plan_link",
  fromId: string,
  toKind: "task" | "plan_link",
  toId: string,
): OrganizeEdge {
  return { id, fromKind, fromId, toKind, toId };
}

describe("organize edge partition for move", () => {
  it("migrates edges whose endpoints are entirely inside the moved set", () => {
    const { migrate, drop } = partitionEdgesForMove(
      [edge("e1", "task", "t1", "task", "t2")],
      movedTasks,
      movedLinks,
    );
    expect(migrate).toHaveLength(1);
    expect(drop).toHaveLength(0);
  });

  it("drops boundary edges that touch a node left behind", () => {
    const { migrate, drop } = partitionEdgesForMove(
      [
        edge("e1", "task", "t1", "task", "outside"),
        edge("e2", "plan_link", "outside", "task", "t2"),
      ],
      movedTasks,
      movedLinks,
    );
    expect(migrate).toHaveLength(0);
    expect(drop).toHaveLength(2);
  });

  it("ignores edges wholly outside the moved set", () => {
    const { migrate, drop } = partitionEdgesForMove(
      [edge("e1", "task", "outside-a", "task", "outside-b")],
      movedTasks,
      movedLinks,
    );
    expect(migrate).toHaveLength(0);
    expect(drop).toHaveLength(0);
  });
});

describe("organize edge partition for package", () => {
  it("classifies internal, boundary, and external edges on the parent", () => {
    const result = partitionEdgesForPackage(
      [
        edge("internal", "task", "t1", "task", "t2"),
        edge("boundary", "task", "t1", "task", "outside"),
        edge("external", "task", "outside-a", "task", "outside-b"),
      ],
      movedTasks,
      movedLinks,
    );
    expect(result.migrate.map((e) => e.id)).toEqual(["internal"]);
    expect(result.rewrite.map((e) => e.id)).toEqual(["boundary"]);
    expect(result.unchanged.map((e) => e.id)).toEqual(["external"]);
  });
});

describe("boundary edge rewrite to plan_link", () => {
  it("rewrites the packaged endpoint to the new plan_link on the parent", () => {
    expect(
      rewriteBoundaryEdgeToPlanLink(
        edge("e1", "task", "t1", "task", "outside"),
        "new-link",
        movedTasks,
        movedLinks,
      ),
    ).toEqual({
      fromKind: "plan_link",
      fromId: "new-link",
      toKind: "task",
      toId: "outside",
    });

    expect(
      rewriteBoundaryEdgeToPlanLink(
        edge("e2", "task", "outside", "task", "t2"),
        "new-link",
        movedTasks,
        movedLinks,
      ),
    ).toEqual({
      fromKind: "task",
      fromId: "outside",
      toKind: "plan_link",
      toId: "new-link",
    });
  });

  it("returns null when the rewrite would self-loop on the plan_link", () => {
    expect(
      rewriteBoundaryEdgeToPlanLink(
        edge("e1", "task", "t1", "plan_link", "new-link"),
        "new-link",
        movedTasks,
        movedLinks,
      ),
    ).toBeNull();
  });
});

describe("isNodeInMovedSet", () => {
  it("matches tasks and plan links separately", () => {
    expect(isNodeInMovedSet("task", "t1", movedTasks, movedLinks)).toBe(true);
    expect(isNodeInMovedSet("plan_link", "l1", movedTasks, movedLinks)).toBe(true);
    expect(isNodeInMovedSet("task", "l1", movedTasks, movedLinks)).toBe(false);
  });
});

describe("remapPositionsNearAnchor", () => {
  it("preserves relative layout with the bbox min at the anchor", () => {
    const map = remapPositionsNearAnchor(
      [
        { id: "a", x: 100, y: 200 },
        { id: "b", x: 150, y: 250 },
      ],
      { x: 10, y: 20 },
    );
    expect(map.get("a")).toEqual({ x: 10, y: 20 });
    expect(map.get("b")).toEqual({ x: 60, y: 70 });
  });
});

describe("findHistoryRootPlanId", () => {
  const parents = new Map<string, string[]>([
    ["child-a", ["root"]],
    ["child-b", ["root"]],
    ["grandchild", ["child-a"]],
  ]);

  it("returns the plan itself when source and target are the same", () => {
    expect(findHistoryRootPlanId("root", "root", parents)).toBe("root");
  });

  it("finds the lowest common ancestor for related plans", () => {
    expect(findHistoryRootPlanId("child-a", "child-b", parents)).toBe("root");
    expect(findHistoryRootPlanId("grandchild", "child-b", parents)).toBe("root");
    expect(findHistoryRootPlanId("grandchild", "child-a", parents)).toBe("child-a");
  });

  it("returns null when plans are in unrelated trees", () => {
    expect(findHistoryRootPlanId("child-a", "other", parents)).toBeNull();
  });
});

describe("wouldMoveLinkIntoDescendant", () => {
  const tree = new Map<string, string[]>([
    ["root", ["child"]],
    ["child", ["grandchild"]],
  ]);

  it("refuses reparenting into the embedded child or its descendants", () => {
    expect(wouldMoveLinkIntoDescendant("child", "child", tree)).toBe(true);
    expect(wouldMoveLinkIntoDescendant("grandchild", "child", tree)).toBe(true);
    expect(wouldMoveLinkIntoDescendant("root", "child", tree)).toBe(false);
  });
});
