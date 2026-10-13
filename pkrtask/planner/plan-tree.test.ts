import { describe, expect, it } from "vitest";
import { nestPlans, type PlanLinkPair } from "./plan-tree";
import type { PlanSummary, PlanTreeNodeDto } from "./types";

function plan(id: string, name = id): PlanSummary {
  return {
    id,
    name,
    description: "",
    ownerKerberos: "ab",
    taskCount: 0,
    doneCount: 0,
  };
}

function topLevelIds(tree: PlanTreeNodeDto[]): string[] {
  return tree.map((node) => node.id);
}

function findNode(tree: PlanTreeNodeDto[], id: string): PlanTreeNodeDto | undefined {
  for (const node of tree) {
    if (node.id === id) return node;
    const nested = findNode(node.children, id);
    if (nested) return nested;
  }
  return undefined;
}

function childIdsOf(tree: PlanTreeNodeDto[], parentId: string): string[] {
  const parent = findNode(tree, parentId);
  return parent ? parent.children.map((child) => child.id) : [];
}

function countNodes(tree: PlanTreeNodeDto[]): number {
  return tree.reduce((total, node) => total + 1 + countNodes(node.children), 0);
}

/**
 * Guarantee under test: the plans list reflects parent→child links from
 * plan_links — roots at the top, descendants nested underneath.
 */
describe("a plan with no parent shows up at the top level of the list", () => {
  it("lists standalone plans alongside unrelated roots", () => {
    const plans = [plan("alpha"), plan("beta"), plan("gamma")];
    const tree = nestPlans(plans, []);

    expect(topLevelIds(tree)).toEqual(["alpha", "beta", "gamma"]);
    expect(tree.every((node) => node.children.length === 0)).toBe(true);
  });
});

describe("a sub-plan appears underneath its parent rather than at the top level", () => {
  it("nests a single child under its parent", () => {
    const plans = [plan("parent"), plan("child")];
    const links: PlanLinkPair[] = [{ parentPlanId: "parent", childPlanId: "child" }];

    const tree = nestPlans(plans, links);

    expect(topLevelIds(tree)).toEqual(["parent"]);
    expect(childIdsOf(tree, "parent")).toEqual(["child"]);
    expect(findNode(tree, "child")).toBeDefined();
  });
});

describe("nesting survives more than one level deep", () => {
  it("places grandchildren under their parent, not at the top level", () => {
    const plans = [plan("root"), plan("mid"), plan("leaf")];
    const links: PlanLinkPair[] = [
      { parentPlanId: "root", childPlanId: "mid" },
      { parentPlanId: "mid", childPlanId: "leaf" },
    ];

    const tree = nestPlans(plans, links);

    expect(topLevelIds(tree)).toEqual(["root"]);
    expect(childIdsOf(tree, "root")).toEqual(["mid"]);
    expect(childIdsOf(tree, "mid")).toEqual(["leaf"]);
    expect(findNode(tree, "leaf")).toBeDefined();
  });
});

describe("a plan belonging to two parents appears under each of them", () => {
  it("duplicates the shared child under every parent and keeps it off the top level", () => {
    const plans = [plan("parent-a"), plan("parent-b"), plan("shared-child")];
    const links: PlanLinkPair[] = [
      { parentPlanId: "parent-a", childPlanId: "shared-child" },
      { parentPlanId: "parent-b", childPlanId: "shared-child" },
    ];

    const tree = nestPlans(plans, links);

    expect(topLevelIds(tree)).toEqual(["parent-a", "parent-b"]);
    expect(childIdsOf(tree, "parent-a")).toEqual(["shared-child"]);
    expect(childIdsOf(tree, "parent-b")).toEqual(["shared-child"]);
    expect(findNode(tree, "shared-child")).toBeDefined();
  });
});

describe("a malformed cycle in the links cannot hang the page", () => {
  it("terminates and returns a finite tree when links form a loop", () => {
    const plans = [plan("root"), plan("loop-a"), plan("loop-b")];
    const links: PlanLinkPair[] = [
      { parentPlanId: "root", childPlanId: "loop-a" },
      { parentPlanId: "loop-a", childPlanId: "loop-b" },
      { parentPlanId: "loop-b", childPlanId: "loop-a" },
    ];

    const tree = nestPlans(plans, links);

    expect(countNodes(tree)).toBeLessThanOrEqual(plans.length);
    expect(topLevelIds(tree)).toEqual(["root"]);
    expect(childIdsOf(tree, "loop-a")).toEqual(["loop-b"]);
    expect(findNode(tree, "loop-b")?.children ?? []).toHaveLength(0);
  });

  it("terminates when every plan is caught in a cycle with no outside root", () => {
    const plans = [plan("a"), plan("b")];
    const links: PlanLinkPair[] = [
      { parentPlanId: "a", childPlanId: "b" },
      { parentPlanId: "b", childPlanId: "a" },
    ];

    const tree = nestPlans(plans, links);

    expect(tree).toEqual([]);
    expect(countNodes(tree)).toBe(0);
  });
});

describe("a link that references a plan the viewer cannot see is ignored", () => {
  it("drops links whose parent or child is missing from the visible plan list", () => {
    const plans = [plan("visible-parent"), plan("visible-child")];
    const links: PlanLinkPair[] = [
      { parentPlanId: "visible-parent", childPlanId: "visible-child" },
      { parentPlanId: "visible-parent", childPlanId: "hidden-child" },
      { parentPlanId: "hidden-parent", childPlanId: "visible-child" },
    ];

    const tree = nestPlans(plans, links);

    expect(topLevelIds(tree)).toEqual(["visible-parent"]);
    expect(childIdsOf(tree, "visible-parent")).toEqual(["visible-child"]);
    expect(findNode(tree, "hidden-child")).toBeUndefined();
    expect(findNode(tree, "hidden-parent")).toBeUndefined();
    expect(countNodes(tree)).toBe(2);
  });
});
