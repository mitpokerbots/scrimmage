"use client";

import type { Person } from "@/lib/auth";
import { EMPTY_FILTERS, filtersActive, type TaskFilters } from "../task-filters";

export const CANVAS_FILTERS_KEY = "pkrtask.canvas.filters";

function isStoredFilters(value: unknown): value is TaskFilters {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    (record.dueBy === null || typeof record.dueBy === "string") &&
    typeof record.assignedToMe === "boolean" &&
    (record.assignee === null || typeof record.assignee === "string") &&
    typeof record.unassigned === "boolean" &&
    typeof record.notDone === "boolean" &&
    typeof record.blockedOnly === "boolean"
  );
}

/** Lazy localStorage read. Bad JSON or an unexpected shape falls back to no filters. */
export function readStoredFilters(): TaskFilters {
  if (typeof window === "undefined") return EMPTY_FILTERS;
  const raw = window.localStorage.getItem(CANVAS_FILTERS_KEY);
  if (raw === null) return EMPTY_FILTERS;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isStoredFilters(parsed)) return EMPTY_FILTERS;
    return {
      dueBy: parsed.dueBy,
      assignedToMe: parsed.assignedToMe,
      assignee: parsed.assignee,
      unassigned: parsed.unassigned,
      notDone: parsed.notDone,
      blockedOnly: parsed.blockedOnly,
    };
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    return EMPTY_FILTERS;
  }
}

export function writeStoredFilters(filters: TaskFilters): void {
  window.localStorage.setItem(CANVAS_FILTERS_KEY, JSON.stringify(filters));
}

type FilterBarProps = {
  filters: TaskFilters;
  people: readonly Person[];
  matchedCount: number;
  totalCount: number;
  onChange: (next: TaskFilters) => void;
};

const fieldClass =
  "rounded border border-line bg-raised px-1 py-0.5 text-xs text-ink [color-scheme:dark] focus:border-highlight focus:outline-none";

function personLabel(person: Person): string {
  const name = person.name?.trim() ?? "";
  return name.length > 0 ? `${name} (${person.kerberos})` : person.kerberos;
}

function FilterCheck({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-1.5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="accent-highlight"
      />
      {label}
    </label>
  );
}

export function FilterBar({ filters, people, matchedCount, totalCount, onChange }: FilterBarProps) {
  const active = filtersActive(filters);
  const assigneeMissing =
    filters.assignee !== null && !people.some((person) => person.kerberos === filters.assignee);

  return (
    <div
      className="nodrag nopan nowheel flex max-w-[min(46rem,calc(100vw-24rem))] flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-line bg-surface/90 px-2 py-1 text-xs text-ink-muted backdrop-blur"
      role="group"
      aria-label="Task filters"
    >
      <div className="flex items-center gap-1">
        <label className="flex items-center gap-1.5">
          Due by
          <input
            type="date"
            value={filters.dueBy ?? ""}
            onChange={(event) =>
              onChange({ ...filters, dueBy: event.target.value === "" ? null : event.target.value })
            }
            className={fieldClass}
          />
        </label>
        {filters.dueBy !== null ? (
          <button
            type="button"
            className="rounded px-1 text-ink-muted hover:bg-raised hover:text-ink"
            aria-label="Clear due by"
            onClick={() => onChange({ ...filters, dueBy: null })}
          >
            ×
          </button>
        ) : null}
      </div>
      <FilterCheck
        label="Assigned to me"
        checked={filters.assignedToMe}
        onChange={(assignedToMe) => onChange({ ...filters, assignedToMe })}
      />
      <label className="flex items-center gap-1.5">
        Assignee
        <select
          value={filters.assignee ?? ""}
          onChange={(event) =>
            onChange({ ...filters, assignee: event.target.value === "" ? null : event.target.value })
          }
          className={`${fieldClass} max-w-44`}
        >
          <option value="">Any</option>
          {assigneeMissing && filters.assignee !== null ? (
            <option value={filters.assignee}>{filters.assignee}</option>
          ) : null}
          {people.map((person) => (
            <option key={person.kerberos} value={person.kerberos}>
              {personLabel(person)}
            </option>
          ))}
        </select>
      </label>
      <FilterCheck
        label="Unassigned"
        checked={filters.unassigned}
        onChange={(unassigned) => onChange({ ...filters, unassigned })}
      />
      <FilterCheck
        label="Not done"
        checked={filters.notDone}
        onChange={(notDone) => onChange({ ...filters, notDone })}
      />
      <FilterCheck
        label="Blocked"
        checked={filters.blockedOnly}
        onChange={(blockedOnly) => onChange({ ...filters, blockedOnly })}
      />
      {active ? (
        <span className="tabular-nums text-ink" aria-live="polite">
          {matchedCount} of {totalCount} tasks match
        </span>
      ) : null}
      {active ? (
        <button
          type="button"
          className="rounded border border-line px-1.5 py-0.5 text-ink hover:bg-raised"
          onClick={() => onChange({ ...EMPTY_FILTERS })}
        >
          Clear
        </button>
      ) : null}
    </div>
  );
}
