import type { TaskDto } from "./types";

export type TaskFilters = {
  /** Local calendar day `YYYY-MM-DD`, or null when due-by is off. */
  dueBy: string | null;
  assignedToMe: boolean;
  /** Kerberos, or null when the assignee filter is off. */
  assignee: string | null;
  unassigned: boolean;
  notDone: boolean;
  blockedOnly: boolean;
};

export const EMPTY_FILTERS: TaskFilters = {
  dueBy: null,
  assignedToMe: false,
  assignee: null,
  unassigned: false,
  notDone: false,
  blockedOnly: false,
};

export function filtersActive(filters: TaskFilters): boolean {
  return (
    filters.dueBy !== null ||
    filters.assignedToMe ||
    filters.assignee !== null ||
    filters.unassigned ||
    filters.notDone ||
    filters.blockedOnly
  );
}

const LOCAL_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Last millisecond of a local calendar day. NaN when `day` is not a real `YYYY-MM-DD`
 * (including overflow like Feb 31, which `Date` would otherwise roll forward).
 */
function endOfLocalDayMs(day: string): number {
  const match = LOCAL_DAY.exec(day);
  if (!match) return Number.NaN;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const date = Number(match[3]);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(date)) {
    return Number.NaN;
  }
  const end = new Date(year, month - 1, date, 23, 59, 59, 999);
  if (end.getFullYear() !== year || end.getMonth() !== month - 1 || end.getDate() !== date) {
    return Number.NaN;
  }
  return end.getTime();
}

export function taskMatchesFilters(
  task: Pick<TaskDto, "status" | "deadline" | "assignees">,
  filters: TaskFilters,
  viewerKerberos: string,
): boolean {
  if (filters.dueBy !== null) {
    // Compare instants against the end of that local day. No deadline never matches.
    if (task.deadline === null) return false;
    const endMs = endOfLocalDayMs(filters.dueBy);
    const deadlineMs = Date.parse(task.deadline);
    if (!Number.isFinite(endMs) || !Number.isFinite(deadlineMs) || deadlineMs > endMs) return false;
  }
  if (filters.assignedToMe && !task.assignees.includes(viewerKerberos)) return false;
  if (filters.assignee !== null && !task.assignees.includes(filters.assignee)) return false;
  if (filters.unassigned && task.assignees.length > 0) return false;
  if (filters.notDone && task.status === "done") return false;
  if (filters.blockedOnly && task.status !== "blocked") return false;
  return true;
}
