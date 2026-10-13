/** Pure graph helpers for dependency edges (no DB, no framework). */

export type GraphNodeKind = "task" | "plan_link";

export type GraphEdge = { fromId: string; toId: string };

export function buildAdjacency(edges: GraphEdge[]): Map<string, string[]> {
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    adjacency.set(edge.fromId, [...(adjacency.get(edge.fromId) ?? []), edge.toId]);
  }
  return adjacency;
}

/** DFS path from `start` to `target` following edge direction; null if unreachable. */
export function findPath(
  start: string,
  target: string,
  adjacency: Map<string, string[]>,
): string[] | null {
  const stack: string[][] = [[start]];
  const visited = new Set<string>([start]);
  while (stack.length > 0) {
    const path = stack.pop();
    if (!path) break;
    const node = path[path.length - 1];
    if (node === undefined) continue;
    if (node === target) return path;
    for (const next of adjacency.get(node) ?? []) {
      if (!visited.has(next)) {
        visited.add(next);
        stack.push([...path, next]);
      }
    }
  }
  return null;
}
