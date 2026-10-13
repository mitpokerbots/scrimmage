import { eq } from "drizzle-orm";
import { db } from "@/db";
import { plans } from "@/db/schema";
import type { Viewer } from "@/lib/auth";

export type Plan = typeof plans.$inferSelect;

/**
 * Plan access. Only scrimmage admins reach Pkrtask (lib/auth.ts), and every
 * admin may read and edit every plan, so this only checks that the plan
 * exists. Per-plan sharing (plan_members) would be enforced here.
 * Throws on any violation — callers never receive data they cannot access.
 */
export async function requirePlanAccess(
  planId: string,
  _viewer: Viewer,
  _access: "read" | "write",
): Promise<Plan> {
  const plan = await db.query.plans.findFirst({ where: eq(plans.id, planId) });
  if (!plan) {
    throw new Error("Plan not found.");
  }
  return plan;
}
