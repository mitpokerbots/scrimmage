import { eq } from "drizzle-orm";
import { notFound } from "next/navigation";
import { z } from "zod";
import { db } from "@/db";
import { plans } from "@/db/schema";
import { getPageViewer, listPeople } from "@/lib/auth";
import { canSee } from "@/planner/access";
import { PlanCanvas } from "@/planner/components/plan-canvas";
import { getUndoRedoState } from "@/planner/history";
import { getPlanGraph } from "@/planner/queries";

export default async function PlanPage({
  params,
  searchParams,
}: {
  params: Promise<{ planId: string }>;
  searchParams: Promise<{ focus?: string }>;
}) {
  const { planId } = await params;
  const { focus } = await searchParams;
  const viewer = await getPageViewer(`/plans/${planId}`);

  // Missing and someone else's private plan look the same.
  if (!z.uuid().safeParse(planId).success) notFound();
  const plan = await db.query.plans.findFirst({ where: eq(plans.id, planId) });
  if (!plan || !canSee(plan, viewer)) notFound();

  const [graph, undoRedo] = await Promise.all([
    getPlanGraph(planId, viewer),
    getUndoRedoState(viewer.kerberos),
  ]);

  return (
    <PlanCanvas
      graph={graph}
      people={listPeople()}
      viewerKerberos={viewer.kerberos}
      focusParam={focus ?? null}
      undoRedo={undoRedo}
    />
  );
}
