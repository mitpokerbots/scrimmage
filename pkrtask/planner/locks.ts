import { inArray } from "drizzle-orm";
import { db } from "@/db";
import { planLinks, plans, tasks } from "@/db/schema";
import type { Viewer } from "@/lib/auth";
import type { LockKey } from "@/realtime/protocol";
import { getHub } from "@/realtime/hub";
import { canSee, type PlanScope } from "./access";

/** Refuse a change if anything it touches is locked by someone else. */
export function assertUnlocked(viewer: Viewer, keys: Iterable<LockKey>): void {
  const lock = getHub().heldByOther(keys, viewer.kerberos);
  if (lock) {
    throw new Error(`${lock.name} is editing this right now. Try again when they're done.`);
  }
}

export const taskKey = (id: string): LockKey => `task:${id}`;
export const linkKey = (id: string): LockKey => `link:${id}`;
export const planKey = (id: string): LockKey => `plan:${id}`;

/**
 * The visibility of the plan each lock key belongs to. Keys for things that no
 * longer exist (deleted by someone else) or that the viewer cannot see come
 * back in `missing`, indistinguishably.
 */
export async function resolveLockScopes(
  viewer: Viewer,
  keys: LockKey[],
): Promise<{ resolved: { key: LockKey; scope: PlanScope }[]; missing: LockKey[] }> {
  const ids = { task: [] as string[], link: [] as string[], plan: [] as string[] };
  for (const key of keys) {
    const [kind, id] = key.split(":") as ["task" | "link" | "plan", string];
    ids[kind].push(id);
  }

  const planOf = new Map<LockKey, string>();
  if (ids.task.length > 0) {
    const rows = await db.query.tasks.findMany({ where: inArray(tasks.id, ids.task) });
    for (const row of rows) planOf.set(taskKey(row.id), row.planId);
  }
  if (ids.link.length > 0) {
    const rows = await db.query.planLinks.findMany({ where: inArray(planLinks.id, ids.link) });
    for (const row of rows) planOf.set(linkKey(row.id), row.parentPlanId);
  }
  for (const id of ids.plan) planOf.set(planKey(id), id);

  const planIds = [...new Set(planOf.values())];
  const planRows =
    planIds.length > 0 ? await db.query.plans.findMany({ where: inArray(plans.id, planIds) }) : [];
  const scopeById = new Map(planRows.map((p) => [p.id, p]));

  const resolved: { key: LockKey; scope: PlanScope }[] = [];
  const missing: LockKey[] = [];
  for (const key of keys) {
    const planId = planOf.get(key);
    const scope = planId ? scopeById.get(planId) : undefined;
    if (scope && canSee(scope, viewer)) {
      resolved.push({ key, scope: { visibility: scope.visibility, ownerKerberos: scope.ownerKerberos } });
    } else {
      missing.push(key);
    }
  }
  return { resolved, missing };
}
