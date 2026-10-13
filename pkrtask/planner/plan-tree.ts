import type { PlanSummary, PlanTreeNodeDto } from "./types";

export type PlanLinkPair = {
  parentPlanId: string;
  childPlanId: string;
};

/** Nest a flat plan list into a tree using parent→child links. */
export function nestPlans(plans: PlanSummary[], links: PlanLinkPair[]): PlanTreeNodeDto[] {
  if (plans.length === 0) return [];

  const planById = new Map(plans.map((p) => [p.id, p]));
  const childrenByParent = new Map<string, string[]>();
  const childIds = new Set<string>();

  for (const link of links) {
    if (!planById.has(link.parentPlanId) || !planById.has(link.childPlanId)) continue;
    childIds.add(link.childPlanId);
    const siblings = childrenByParent.get(link.parentPlanId);
    if (siblings) {
      siblings.push(link.childPlanId);
    } else {
      childrenByParent.set(link.parentPlanId, [link.childPlanId]);
    }
  }

  function buildSubtree(plan: PlanSummary, ancestors: Set<string>): PlanTreeNodeDto {
    const childPlanIds = childrenByParent.get(plan.id) ?? [];
    const children: PlanTreeNodeDto[] = [];

    for (const childId of childPlanIds) {
      if (ancestors.has(childId)) continue;
      const childPlan = planById.get(childId);
      if (!childPlan) continue;
      const nextAncestors = new Set(ancestors);
      nextAncestors.add(childId);
      children.push(buildSubtree(childPlan, nextAncestors));
    }

    return { ...plan, children };
  }

  const roots = plans.filter((p) => !childIds.has(p.id));
  return roots.map((root) => buildSubtree(root, new Set([root.id])));
}
