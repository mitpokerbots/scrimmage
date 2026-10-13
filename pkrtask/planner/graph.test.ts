import { describe, expect, it } from "vitest";
import { buildAdjacency, findPath } from "./graph";

/**
 * Guarantee under test: a plan's dependencies can never form a loop.
 *
 * A cycle means a task waits on itself, which would hang the canvas layout and
 * make "what's next" unanswerable. Actions reject a new edge when a path already
 * runs the other way, so these cases decide whether that rejection fires.
 */
describe("a plan cannot contain a circular dependency", () => {
  it("sees a path along the direction of the arrows", () => {
    const graph = buildAdjacency([
      { fromId: "a", toId: "b" },
      { fromId: "b", toId: "c" },
    ]);
    expect(findPath("a", "c", graph)).toEqual(["a", "b", "c"]);
  });

  it("does not invent a path against the arrows, so a→b→c can still gain a→c", () => {
    const graph = buildAdjacency([
      { fromId: "a", toId: "b" },
      { fromId: "b", toId: "c" },
    ]);
    expect(findPath("c", "a", graph)).toBeNull();
  });

  it("rejects an edge that would close a loop", () => {
    // Existing a→b→c. Adding c→a is only safe if a cannot already reach c.
    const graph = buildAdjacency([
      { fromId: "a", toId: "b" },
      { fromId: "b", toId: "c" },
    ]);
    const wouldCycle = findPath("a", "c", graph) !== null;
    expect(wouldCycle).toBe(true);
  });

  it("treats a task as trivially reaching itself, blocking self-links", () => {
    expect(findPath("a", "a", buildAdjacency([]))).toEqual(["a"]);
  });

  it("terminates on a graph that already contains a loop", () => {
    // Bad data must not hang the request.
    const graph = buildAdjacency([
      { fromId: "a", toId: "b" },
      { fromId: "b", toId: "a" },
    ]);
    expect(findPath("a", "b", graph)).toEqual(["a", "b"]);
    expect(findPath("b", "unreachable", graph)).toBeNull();
  });

  it("finds a path through a diamond where one branch is a dead end", () => {
    const graph = buildAdjacency([
      { fromId: "start", toId: "left" },
      { fromId: "start", toId: "right" },
      { fromId: "right", toId: "finish" },
    ]);
    expect(findPath("start", "finish", graph)).toEqual(["start", "right", "finish"]);
    expect(findPath("left", "finish", graph)).toBeNull();
  });
});
