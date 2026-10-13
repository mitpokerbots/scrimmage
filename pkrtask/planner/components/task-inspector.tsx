"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/button";
import { Input, Select, Textarea } from "@/components/input";
import { useResizablePanelWidth } from "@/components/use-resizable-panel-width";
import type { Person } from "@/lib/auth";
import { TASK_EFFORTS, TASK_STATUSES, type TaskDto } from "../types";

const ASSIGNEE_VIEW_KEY = "pkrtask-assignee-view";

type AssigneeViewMode = "list" | "grid";

function readAssigneeViewMode(): AssigneeViewMode {
  if (typeof window === "undefined") return "list";
  return localStorage.getItem(ASSIGNEE_VIEW_KEY) === "grid" ? "grid" : "list";
}

function firstName(person: Person): string {
  return person.name?.split(" ")[0] ?? "";
}

export type TaskPatch = Partial<
  Pick<TaskDto, "title" | "status" | "notes" | "deadline" | "effort">
>;

type TaskInspectorProps = {
  task: TaskDto;
  people: Person[];
  onPatch: (taskId: string, patch: TaskPatch) => void;
  onSetAssignees: (taskId: string, kerberos: string[]) => void;
  onDelete: (taskId: string) => void;
  onClose: () => void;
};

function toLocalInputValue(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function TaskInspector({
  task,
  people,
  onPatch,
  onSetAssignees,
  onDelete,
  onClose,
}: TaskInspectorProps) {
  const { width, onResizePointerDown } = useResizablePanelWidth("pkrtask-panel-width", 360);
  // Text fields are buffered locally and saved on blur; selects save immediately.
  const [title, setTitle] = useState(task.title);
  const [notes, setNotes] = useState(task.notes);
  const [assigneeViewMode, setAssigneeViewMode] = useState<AssigneeViewMode>(readAssigneeViewMode);
  const [addSearchTaskId, setAddSearchTaskId] = useState(task.id);
  const [addSearchOpen, setAddSearchOpen] = useState(false);
  const [addSearchQuery, setAddSearchQuery] = useState("");
  const addSearchRef = useRef<HTMLInputElement>(null);

  if (addSearchTaskId !== task.id) {
    setAddSearchTaskId(task.id);
    setAddSearchOpen(false);
    setAddSearchQuery("");
  }

  useEffect(() => {
    localStorage.setItem(ASSIGNEE_VIEW_KEY, assigneeViewMode);
  }, [assigneeViewMode]);

  useEffect(() => {
    if (addSearchOpen) addSearchRef.current?.focus();
  }, [addSearchOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Assignees who are no longer admins are still listed, so they can be removed.
  const assignedPeople = useMemo(() => {
    return task.assignees.map(
      (kerberos) => people.find((p) => p.kerberos === kerberos) ?? { kerberos, name: null },
    );
  }, [task.assignees, people]);

  const addSearchResults = useMemo(() => {
    const q = addSearchQuery.trim().toLowerCase();
    if (!q) return [];
    return people.filter(
      (p) =>
        !task.assignees.includes(p.kerberos) &&
        (p.kerberos.includes(q) || (p.name ?? "").toLowerCase().includes(q)),
    );
  }, [people, task.assignees, addSearchQuery]);

  function toggleAssignee(kerberos: string) {
    const next = task.assignees.includes(kerberos)
      ? task.assignees.filter((k) => k !== kerberos)
      : [...task.assignees, kerberos];
    onSetAssignees(task.id, next);
  }

  function unassign(kerberos: string) {
    onSetAssignees(
      task.id,
      task.assignees.filter((k) => k !== kerberos),
    );
  }

  function assign(kerberos: string) {
    if (task.assignees.includes(kerberos)) return;
    onSetAssignees(task.id, [...task.assignees, kerberos]);
  }

  function closeAddSearch() {
    setAddSearchOpen(false);
    setAddSearchQuery("");
  }

  return (
    <aside
      className="absolute inset-y-0 right-0 z-10 flex flex-col gap-4 overflow-y-auto border-l border-line bg-surface p-4"
      style={{ width }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panel"
        onPointerDown={onResizePointerDown}
        className="absolute inset-y-0 left-0 z-30 w-1.5 cursor-col-resize hover:bg-highlight/40 active:bg-highlight/60"
      />
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">Task</h2>
        <button
          onClick={onClose}
          className="rounded px-2 py-0.5 text-sm text-ink-muted hover:bg-raised hover:text-ink"
          aria-label="Close inspector (Esc)"
        >
          ✕
        </button>
      </div>

      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        Title
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => {
            const trimmed = title.trim();
            if (trimmed && trimmed !== task.title) onPatch(task.id, { title: trimmed });
            else setTitle(task.title);
          }}
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Status
          <Select
            value={task.status}
            onChange={(e) => onPatch(task.id, { status: e.target.value as TaskDto["status"] })}
          >
            {TASK_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>

        <label className="flex flex-col gap-1 text-xs text-ink-muted">
          Effort
          <Select
            value={task.effort ?? ""}
            onChange={(e) =>
              onPatch(task.id, {
                effort: e.target.value === "" ? null : (e.target.value as TaskDto["effort"]),
              })
            }
          >
            <option value="">—</option>
            {TASK_EFFORTS.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </Select>
        </label>
      </div>

      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        Deadline
        <Input
          type="datetime-local"
          value={toLocalInputValue(task.deadline)}
          onChange={(e) =>
            onPatch(task.id, {
              deadline: e.target.value ? new Date(e.target.value).toISOString() : null,
            })
          }
        />
      </label>

      <div className="flex flex-col gap-1 text-xs text-ink-muted">
        <div className="flex items-center justify-between">
          <span>Assignees</span>
          <div
            className="flex rounded border border-line bg-surface text-[10px]"
            role="group"
            aria-label="Assignee view mode"
          >
            <button
              type="button"
              onClick={() => setAssigneeViewMode("list")}
              aria-pressed={assigneeViewMode === "list"}
              className={`rounded-l px-1.5 py-0.5 ${
                assigneeViewMode === "list"
                  ? "bg-raised text-ink"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              List
            </button>
            <button
              type="button"
              onClick={() => setAssigneeViewMode("grid")}
              aria-pressed={assigneeViewMode === "grid"}
              className={`rounded-r border-l border-line px-1.5 py-0.5 ${
                assigneeViewMode === "grid"
                  ? "bg-raised text-ink"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              Grid
            </button>
          </div>
        </div>
        {assigneeViewMode === "list" ? (
          <div className="max-h-48 overflow-y-auto rounded-md border border-line bg-raised p-2">
            {assignedPeople.length === 0 ? (
              <p className="text-xs text-ink-muted">No assignees</p>
            ) : (
              <ul className="flex flex-col">
                {assignedPeople.map((p) => (
                  <li
                    key={p.kerberos}
                    className="flex items-center justify-between gap-2 py-0.5"
                  >
                    <span className="flex min-w-0 items-center gap-1.5 text-xs text-ink">
                      <span className="shrink-0 font-semibold text-highlight">{p.kerberos}</span>
                      <span className="truncate text-ink-muted">{firstName(p)}</span>
                    </span>
                    <button
                      type="button"
                      onClick={() => unassign(p.kerberos)}
                      aria-label={`Remove ${p.kerberos}`}
                      className="shrink-0 rounded px-1 py-0.5 text-xs text-ink-muted hover:bg-surface hover:text-ink"
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {!addSearchOpen ? (
              <button
                type="button"
                onClick={() => setAddSearchOpen(true)}
                className="mt-1 text-xs text-highlight hover:underline"
              >
                + Add
              </button>
            ) : (
              <div className="mt-1 flex flex-col gap-1">
                <Input
                  ref={addSearchRef}
                  value={addSearchQuery}
                  onChange={(e) => setAddSearchQuery(e.target.value)}
                  onBlur={closeAddSearch}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      e.stopPropagation();
                      closeAddSearch();
                    } else if (e.key === "Enter" && addSearchResults[0]) {
                      e.preventDefault();
                      assign(addSearchResults[0].kerberos);
                    }
                  }}
                  placeholder="Search by kerberos or name…"
                  className="text-xs"
                />
                {addSearchQuery.trim() && addSearchResults.length === 0 ? (
                  <p className="text-[10px] text-ink-muted">No matches</p>
                ) : null}
                {addSearchResults.map((p) => (
                  <button
                    key={p.kerberos}
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => assign(p.kerberos)}
                    className="flex items-center gap-1.5 rounded px-1 py-0.5 text-left text-xs text-ink hover:bg-surface"
                  >
                    <span className="font-semibold text-highlight">{p.kerberos}</span>
                    <span className="truncate text-ink-muted">{firstName(p)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="max-h-48 overflow-y-auto rounded-md border border-line bg-raised p-2">
            <div className="grid grid-cols-2 gap-x-2">
              {people.map((p) => (
                <label
                  key={p.kerberos}
                  className="flex cursor-pointer items-center gap-1.5 py-0.5 text-xs text-ink"
                >
                  <input
                    type="checkbox"
                    checked={task.assignees.includes(p.kerberos)}
                    onChange={() => toggleAssignee(p.kerberos)}
                    className="accent-highlight"
                  />
                  <span className="font-semibold text-highlight">{p.kerberos}</span>
                  <span className="truncate text-ink-muted">{firstName(p)}</span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>

      <label className="flex min-h-0 flex-1 flex-col gap-1 text-xs text-ink-muted">
        Notes (markdown)
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => {
            if (notes !== task.notes) onPatch(task.id, { notes });
          }}
          className="min-h-32 flex-1 resize-none font-mono text-xs"
          placeholder="Details, links, sub-steps…"
        />
      </label>

      <Button variant="danger" onClick={() => onDelete(task.id)}>
        Delete task
      </Button>
    </aside>
  );
}
