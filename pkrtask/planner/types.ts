export const TASK_STATUSES = ["todo", "doing", "done", "blocked"] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_EFFORTS = ["S", "M", "L"] as const;
export type TaskEffort = (typeof TASK_EFFORTS)[number];

export const GRAPH_NODE_KINDS = ["task", "plan_link"] as const;
export type GraphNodeKind = (typeof GRAPH_NODE_KINDS)[number];

/** Task shape shared between server queries and the canvas client. */
export type TaskDto = {
  id: string;
  planId: string;
  title: string;
  status: TaskStatus;
  notes: string;
  deadline: string | null; // ISO string (serializable across the RSC boundary)
  effort: TaskEffort | null;
  x: number;
  y: number;
  width: number;
  height: number;
  assignees: string[]; // kerberos
};

export type EdgeDto = {
  id: string;
  fromKind: GraphNodeKind;
  fromId: string;
  toKind: GraphNodeKind;
  toId: string;
};

export type PlanRollup = {
  total: number;
  done: number;
  blocked: number;
  nearestDeadline: string | null;
};

export type PlanSummary = {
  id: string;
  name: string;
  description: string;
  ownerKerberos: string;
  taskCount: number;
  doneCount: number;
};

/** Plan summary nested under its parent(s) for the plan list page. */
export type PlanTreeNodeDto = PlanSummary & {
  children: PlanTreeNodeDto[];
};

export type PlanMetaDto = {
  status: TaskStatus;
  effort: TaskEffort | null;
  deadline: string | null; // ISO
  notes: string;
};

export type SubPlanDto = {
  linkId: string;
  parentPlanId: string;
  childPlanId: string;
  name: string;
  description: string;
  x: number;
  y: number;
  width: number;
  height: number;
  assignees: string[];
  rollup: PlanRollup;
  tasks: TaskDto[];
  edges: EdgeDto[];
  subPlans: SubPlanDto[];
} & PlanMetaDto;
