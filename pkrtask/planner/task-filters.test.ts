import { describe, expect, it } from "vitest";
import {
  EMPTY_FILTERS,
  filtersActive,
  taskMatchesFilters,
  type TaskFilters,
} from "./task-filters";
import type { TaskDto } from "./types";

/**
 * Active filters combine with AND. dueBy is the end of that local calendar day.
 * vitest pins TZ=America/New_York, so January is EST (UTC-5) and October is EDT (UTC-4).
 */
type FilterTask = Pick<TaskDto, "status" | "deadline" | "assignees">;

function task(overrides: Partial<FilterTask> = {}): FilterTask {
  return {
    status: "todo",
    deadline: null,
    assignees: [],
    ...overrides,
  };
}

function localIso(
  year: number,
  monthIndex: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
  ms = 0,
): string {
  return new Date(year, monthIndex, day, hour, minute, second, ms).toISOString();
}

describe("task filters", () => {
  it("matches every task when no filter is active", () => {
    expect(filtersActive(EMPTY_FILTERS)).toBe(false);
    expect(
      taskMatchesFilters(task({ status: "done", deadline: null, assignees: [] }), EMPTY_FILTERS, "alice"),
    ).toBe(true);
  });

  it("reports each individual filter as active", () => {
    expect(filtersActive({ ...EMPTY_FILTERS, dueBy: "2026-10-13" })).toBe(true);
    expect(filtersActive({ ...EMPTY_FILTERS, assignedToMe: true })).toBe(true);
    expect(filtersActive({ ...EMPTY_FILTERS, assignee: "bob" })).toBe(true);
    expect(filtersActive({ ...EMPTY_FILTERS, unassigned: true })).toBe(true);
    expect(filtersActive({ ...EMPTY_FILTERS, notDone: true })).toBe(true);
    expect(filtersActive({ ...EMPTY_FILTERS, blockedOnly: true })).toBe(true);
  });

  it("dueBy includes the last millisecond of the local day and excludes the next", () => {
    // 23:59:59.999 EDT on Oct 13 is still Oct 13 locally, and the next UTC date.
    const end = localIso(2026, 9, 13, 23, 59, 59, 999);
    expect(end).toBe("2026-10-14T03:59:59.999Z");
    const nextMidnight = localIso(2026, 9, 14, 0, 0, 0, 0);
    expect(nextMidnight).toBe("2026-10-14T04:00:00.000Z");

    const filters: TaskFilters = { ...EMPTY_FILTERS, dueBy: "2026-10-13" };
    expect(taskMatchesFilters(task({ deadline: end }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ deadline: "2026-10-13T23:59:59.999-04:00" }), filters, "alice")).toBe(
      true,
    );
    expect(taskMatchesFilters(task({ deadline: localIso(2026, 9, 13, 0, 0, 0, 0) }), filters, "alice")).toBe(
      true,
    );
    expect(taskMatchesFilters(task({ deadline: localIso(2026, 9, 12, 23, 59, 59, 999) }), filters, "alice")).toBe(
      true,
    );
    expect(taskMatchesFilters(task({ deadline: nextMidnight }), filters, "alice")).toBe(false);
    expect(taskMatchesFilters(task({ deadline: "2026-10-14T00:00:00.000-04:00" }), filters, "alice")).toBe(
      false,
    );
  });

  it("dueBy uses the local calendar day, not the UTC date", () => {
    // 22:00 EST on Jan 15 is 03:00Z on Jan 16 — still due on the 15th locally.
    const tenPm = localIso(2026, 0, 15, 22, 0, 0, 0);
    expect(tenPm).toBe("2026-01-16T03:00:00.000Z");
    const filters: TaskFilters = { ...EMPTY_FILTERS, dueBy: "2026-01-15" };
    expect(taskMatchesFilters(task({ deadline: tenPm }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ deadline: localIso(2026, 0, 15, 23, 59, 59, 999) }), filters, "alice")).toBe(
      true,
    );
    expect(taskMatchesFilters(task({ deadline: localIso(2026, 0, 16, 0, 0, 0, 0) }), filters, "alice")).toBe(
      false,
    );
  });

  it("dueBy does not match a task with no deadline", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, dueBy: "2026-10-13" };
    expect(taskMatchesFilters(task({ deadline: null }), filters, "alice")).toBe(false);
  });

  it("does not treat an impossible dueBy day as a match", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, dueBy: "2026-02-31" };
    expect(taskMatchesFilters(task({ deadline: "2026-02-28T17:00:00.000Z" }), filters, "alice")).toBe(false);
  });

  it("assignedToMe requires the viewer kerberos", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, assignedToMe: true };
    expect(taskMatchesFilters(task({ assignees: ["alice", "bob"] }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ assignees: ["bob"] }), filters, "alice")).toBe(false);
    expect(taskMatchesFilters(task({ assignees: [] }), filters, "alice")).toBe(false);
  });

  it("assignee matches that kerberos", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, assignee: "bob" };
    expect(taskMatchesFilters(task({ assignees: ["alice", "bob"] }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ assignees: ["alice"] }), filters, "alice")).toBe(false);
  });

  it("unassigned matches tasks with no assignees", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, unassigned: true };
    expect(taskMatchesFilters(task({ assignees: [] }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ assignees: ["alice"] }), filters, "alice")).toBe(false);
  });

  it("notDone excludes done tasks and keeps the other statuses", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, notDone: true };
    expect(taskMatchesFilters(task({ status: "todo" }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ status: "doing" }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ status: "blocked" }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ status: "done" }), filters, "alice")).toBe(false);
  });

  it("blockedOnly matches only blocked tasks", () => {
    const filters: TaskFilters = { ...EMPTY_FILTERS, blockedOnly: true };
    expect(taskMatchesFilters(task({ status: "blocked" }), filters, "alice")).toBe(true);
    expect(taskMatchesFilters(task({ status: "todo" }), filters, "alice")).toBe(false);
    expect(taskMatchesFilters(task({ status: "doing" }), filters, "alice")).toBe(false);
    expect(taskMatchesFilters(task({ status: "done" }), filters, "alice")).toBe(false);
  });

  it("combines active filters with AND", () => {
    const filters: TaskFilters = {
      dueBy: "2026-10-13",
      assignedToMe: true,
      assignee: "bob",
      unassigned: false,
      notDone: true,
      blockedOnly: false,
    };
    expect(filtersActive(filters)).toBe(true);
    const deadline = localIso(2026, 9, 13, 12, 0, 0, 0);
    const match = task({ status: "doing", deadline, assignees: ["alice", "bob"] });
    expect(taskMatchesFilters(match, filters, "alice")).toBe(true);
    expect(taskMatchesFilters({ ...match, status: "done" }, filters, "alice")).toBe(false);
    expect(taskMatchesFilters({ ...match, assignees: ["alice"] }, filters, "alice")).toBe(false);
    expect(taskMatchesFilters({ ...match, assignees: ["bob"] }, filters, "alice")).toBe(false);
    expect(taskMatchesFilters({ ...match, deadline: null }, filters, "alice")).toBe(false);
    expect(
      taskMatchesFilters({ ...match, deadline: localIso(2026, 9, 14, 0, 0, 0, 0) }, filters, "alice"),
    ).toBe(false);

    const blockedAndUnassigned: TaskFilters = { ...EMPTY_FILTERS, unassigned: true, blockedOnly: true };
    expect(taskMatchesFilters(task({ status: "blocked", assignees: [] }), blockedAndUnassigned, "alice")).toBe(
      true,
    );
    expect(
      taskMatchesFilters(task({ status: "blocked", assignees: ["alice"] }), blockedAndUnassigned, "alice"),
    ).toBe(false);
    expect(taskMatchesFilters(task({ status: "todo", assignees: [] }), blockedAndUnassigned, "alice")).toBe(
      false,
    );
  });
});
