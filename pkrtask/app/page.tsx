import Link from "next/link";
import { Card } from "@/components/card";
import { Tag } from "@/components/tag";
import { getPageViewer } from "@/lib/auth";
import { CreatePlanForm } from "@/planner/components/create-plan-form";
import { listPlans } from "@/planner/queries";
import type { PlanTreeNodeDto } from "@/planner/types";

const INDENT_REM = 1.25;
const MAX_VISUAL_DEPTH = 3;
const MAX_RENDER_DEPTH = 32;

function countVisiblePlans(nodes: PlanTreeNodeDto[]): number {
  const seen = new Set<string>();

  function walk(node: PlanTreeNodeDto) {
    seen.add(node.id);
    for (const child of node.children) {
      walk(child);
    }
  }

  for (const root of nodes) {
    walk(root);
  }

  return seen.size;
}

type PlanTreeListProps = {
  nodes: PlanTreeNodeDto[];
  depth?: number;
  path?: string;
};

function PlanTreeList({ nodes, depth = 0, path = "" }: PlanTreeListProps) {
  if (depth > MAX_RENDER_DEPTH) {
    return null;
  }

  return (
    <>
      {nodes.map((plan) => {
        const rowPath = path ? `${path}/${plan.id}` : plan.id;
        const indentRem = Math.min(depth, MAX_VISUAL_DEPTH) * INDENT_REM;

        return (
          <li key={rowPath}>
            <div
              className={depth > 0 ? "border-l border-line/60 pl-3" : ""}
              style={{ marginLeft: depth > 0 ? `${indentRem}rem` : undefined }}
            >
              <Link href={`/plans/${plan.id}`} className="block">
                <Card className="transition-colors hover:border-highlight/50">
                  <div className="flex items-center justify-between">
                    <div>
                      <h2 className="font-medium text-ink">{plan.name}</h2>
                      {plan.description ? (
                        <p className="mt-1 text-sm text-ink-muted">{plan.description}</p>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-2">
                      <Tag>
                        {plan.doneCount}/{plan.taskCount} done
                      </Tag>
                      <Tag>{plan.ownerKerberos}</Tag>
                    </div>
                  </div>
                </Card>
              </Link>
            </div>
            {plan.children.length > 0 ? (
              <ul className="mt-3 flex flex-col gap-3">
                <PlanTreeList nodes={plan.children} depth={depth + 1} path={rowPath} />
              </ul>
            ) : null}
          </li>
        );
      })}
    </>
  );
}

export default async function PlansPage() {
  const viewer = await getPageViewer("/");
  const plans = await listPlans(viewer);
  const planCount = countVisiblePlans(plans);

  return (
    <div className="mx-auto max-w-3xl p-8">
      <div className="mb-6 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-ink">Plans</h1>
        <span className="text-sm text-ink-muted">
          {planCount} plan{planCount === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mb-8">
        <CreatePlanForm />
      </div>

      {plans.length === 0 ? (
        <Card className="text-sm text-ink-muted">
          No plans yet. Create one above; sub-plans can be added on its canvas.
        </Card>
      ) : (
        <ul className="flex flex-col gap-3">
          <PlanTreeList nodes={plans} />
        </ul>
      )}
    </div>
  );
}
