import { sql } from "drizzle-orm";
import {
  check,
  integer,
  primaryKey,
  real,
  sqliteTable,
  text,
  unique,
} from "drizzle-orm/sqlite-core";
import { GRAPH_NODE_KINDS, TASK_EFFORTS, TASK_STATUSES } from "../planner/types";

const PLAN_ROLES = ["viewer", "editor"] as const;

function inValues(column: string, values: readonly string[]) {
  return sql.raw(`${column} IN (${values.map((v) => `'${v}'`).join(", ")})`);
}

const id = () =>
  text("id")
    .primaryKey()
    .$defaultFn(() => crypto.randomUUID());

const createdAt = () =>
  integer("created_at", { mode: "timestamp_ms" })
    .notNull()
    .$defaultFn(() => new Date());

// People are scrimmage admins, identified by kerberos. The directory lives in
// scrimmage's database (see lib/auth.ts), so these columns have no foreign key.

export const plans = sqliteTable(
  "plans",
  {
    id: id(),
    name: text("name").notNull(),
    description: text("description").notNull().default(""),
    ownerKerberos: text("owner_kerberos").notNull(),
    status: text("status", { enum: TASK_STATUSES }).notNull().default("todo"),
    effort: text("effort", { enum: TASK_EFFORTS }),
    deadline: integer("deadline", { mode: "timestamp_ms" }),
    notes: text("notes").notNull().default(""),
    createdAt: createdAt(),
  },
  (t) => [
    check("plans_status", inValues(t.status.name, TASK_STATUSES)),
    check("plans_effort", sql`${sql.identifier(t.effort.name)} IS NULL OR ${inValues(t.effort.name, TASK_EFFORTS)}`),
  ],
);

/** Per-plan sharing. Not enforced while every admin can edit every plan (planner/access.ts). */
export const planMembers = sqliteTable(
  "plan_members",
  {
    planId: text("plan_id")
      .notNull()
      .references(() => plans.id, { onDelete: "cascade" }),
    kerberos: text("kerberos").notNull(),
    role: text("role", { enum: PLAN_ROLES }).notNull().default("viewer"),
  },
  (t) => [
    primaryKey({ columns: [t.planId, t.kerberos] }),
    check("plan_members_role", inValues(t.role.name, PLAN_ROLES)),
  ],
);

export const tasks = sqliteTable(
  "tasks",
  {
    id: id(),
    planId: text("plan_id")
      .notNull()
      .references(() => plans.id, { onDelete: "cascade" }),
    title: text("title").notNull(),
    status: text("status", { enum: TASK_STATUSES }).notNull().default("todo"),
    notes: text("notes").notNull().default(""),
    deadline: integer("deadline", { mode: "timestamp_ms" }),
    effort: text("effort", { enum: TASK_EFFORTS }),
    // Canvas position. Manual placement is sticky.
    x: real("x").notNull().default(0),
    y: real("y").notNull().default(0),
    width: real("width").notNull().default(240),
    height: real("height").notNull().default(84),
    createdAt: createdAt(),
    updatedAt: integer("updated_at", { mode: "timestamp_ms" })
      .notNull()
      .$defaultFn(() => new Date())
      .$onUpdate(() => new Date()),
  },
  (t) => [
    check("tasks_status", inValues(t.status.name, TASK_STATUSES)),
    check("tasks_effort", sql`${sql.identifier(t.effort.name)} IS NULL OR ${inValues(t.effort.name, TASK_EFFORTS)}`),
  ],
);

export const taskAssignees = sqliteTable(
  "task_assignees",
  {
    taskId: text("task_id")
      .notNull()
      .references(() => tasks.id, { onDelete: "cascade" }),
    kerberos: text("kerberos").notNull(),
  },
  (t) => [primaryKey({ columns: [t.taskId, t.kerberos] })],
);

/** Dependency edges: `from` must happen before `to`. Endpoints are tasks or plan-links. */
export const edges = sqliteTable(
  "edges",
  {
    id: id(),
    planId: text("plan_id")
      .notNull()
      .references(() => plans.id, { onDelete: "cascade" }),
    fromKind: text("from_kind", { enum: GRAPH_NODE_KINDS }).notNull(),
    fromId: text("from_id").notNull(),
    toKind: text("to_kind", { enum: GRAPH_NODE_KINDS }).notNull(),
    toId: text("to_id").notNull(),
  },
  (t) => [
    unique("edges_from_to_unique").on(t.fromKind, t.fromId, t.toKind, t.toId),
    check("edges_from_kind", inValues(t.fromKind.name, GRAPH_NODE_KINDS)),
    check("edges_to_kind", inValues(t.toKind.name, GRAPH_NODE_KINDS)),
  ],
);

/**
 * Sub-plan embedding on a parent canvas. Position is on the parent plan;
 * child is a full `plans` row. Ancestor cycles rejected in actions.
 */
export const planLinks = sqliteTable(
  "plan_links",
  {
    id: id(),
    parentPlanId: text("parent_plan_id")
      .notNull()
      .references(() => plans.id, { onDelete: "cascade" }),
    childPlanId: text("child_plan_id")
      .notNull()
      .references(() => plans.id, { onDelete: "cascade" }),
    x: real("x").notNull().default(0),
    y: real("y").notNull().default(0),
    width: real("width").notNull().default(360),
    height: real("height").notNull().default(240),
    createdAt: createdAt(),
  },
  (t) => [unique("plan_links_parent_child_unique").on(t.parentPlanId, t.childPlanId)],
);

/**
 * Global undo/redo stack. Entries are ordered by `id`, which only grows, so
 * entries written in the same millisecond still have a definite order.
 */
export const planHistory = sqliteTable("plan_history", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  label: text("label").notNull(),
  /** Plan this entry primarily affects (for navigation after undo). */
  planId: text("plan_id"),
  beforeState: text("before_state", { mode: "json" }),
  afterState: text("after_state", { mode: "json" }),
  undone: integer("undone", { mode: "boolean" }).notNull().default(false),
  createdAt: createdAt(),
});
