import { PlanCanvas } from "@/planner/components/plan-canvas";
import { getUndoRedoState } from "@/planner/history";
import { getPlanGraph } from "@/planner/queries";
import { getPageViewer, listPeople } from "@/lib/auth";

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
  const [graph, people, undoRedo] = await Promise.all([
    getPlanGraph(planId, viewer),
    listPeople(),
    getUndoRedoState(),
  ]);

  return (
    <PlanCanvas graph={graph} people={people} focusParam={focus ?? null} undoRedo={undoRedo} />
  );
}
