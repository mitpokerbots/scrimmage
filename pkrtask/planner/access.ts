import { eq } from "drizzle-orm";
import { db } from "@/db";
import { plans } from "@/db/schema";
import type { Viewer } from "@/lib/auth";
import type { PlanVisibility } from "./types";

export type Plan = typeof plans.$inferSelect;

/** Who may see a plan: everyone if shared, only its owner if private. */
export type PlanScope = { visibility: PlanVisibility; ownerKerberos: string };

export function canSee(scope: PlanScope, viewer: Pick<Viewer, "kerberos">): boolean {
  return scope.visibility === "shared" || scope.ownerKerberos === viewer.kerberos;
}

/**
 * Plan access. Everyone on the roster can read and edit shared plans; a private
 * plan is invisible to everyone but its owner (reported as not found, so its
 * existence does not leak). Throws on any violation — callers never receive
 * data they cannot access.
 */
export async function requirePlanAccess(
  planId: string,
  viewer: Viewer,
  _access: "read" | "write",
): Promise<Plan> {
  const plan = await db.query.plans.findFirst({ where: eq(plans.id, planId) });
  if (!plan || !canSee(plan, viewer)) {
    throw new Error("Plan not found.");
  }
  return plan;
}
