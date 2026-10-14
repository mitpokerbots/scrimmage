"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/button";
import { Input, Select, Textarea } from "@/components/input";
import { useResizablePanelWidth } from "@/components/use-resizable-panel-width";
import type { Person } from "@/lib/auth";
import { TASK_EFFORTS, TASK_STATUSES, type SubPlanDto, type TaskEffort, type TaskStatus } from "../types";

export type SubPlanPatch = Partial<
  Pick<SubPlanDto, "name" | "description" | "status" | "notes" | "deadline" | "effort">
>;

type SubPlanInspectorProps = {
  subPlan: SubPlanDto;
  linkId: string;
  people: Person[];
  onPatch: (childPlanId: string, patch: SubPlanPatch) => void;
  onDelete: (linkId: string) => void;
  onClose: () => void;
  /** Make this private sub-plan (and private plans inside it) shared. */
  onShare: (childPlanId: string) => void;
  /** Someone else is editing this sub-plan: show it read-only. */
  lockedBy: string | null;
};

function toLocalInputValue(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function personName(people: Person[], kerberos: string): string {
  return people.find((p) => p.kerberos === kerberos)?.name ?? "";
}

export function SubPlanInspector({
  subPlan,
  linkId,
  people,
  onPatch,
  onDelete,
  onClose,
  onShare,
  lockedBy,
}: SubPlanInspectorProps) {
  const { width, onResizePointerDown } = useResizablePanelWidth("pkrtask-subplan-panel-width", 360);
  const [name, setName] = useState(subPlan.name);
  const [description, setDescription] = useState(subPlan.description);
  const [notes, setNotes] = useState(subPlan.notes);
  const [bufferedChildId, setBufferedChildId] = useState(subPlan.childPlanId);

  if (bufferedChildId !== subPlan.childPlanId) {
    setBufferedChildId(subPlan.childPlanId);
    setName(subPlan.name);
    setDescription(subPlan.description);
    setNotes(subPlan.notes);
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const assigneeRows = useMemo(
    () =>
      subPlan.assignees.map((kerberos) => ({
        kerberos,
        name: personName(people, kerberos),
      })),
    [people, subPlan.assignees],
  );

  return (
    <aside
      className="fixed bottom-0 right-0 top-14 z-30 flex flex-col border-l border-line bg-surface shadow-xl"
      style={{ width }}
    >
      <div
        className="absolute bottom-0 left-0 top-0 w-1.5 cursor-col-resize hover:bg-highlight/30"
        onPointerDown={onResizePointerDown}
        aria-hidden
      />
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Sub-plan</h2>
        <button type="button" className="text-sm text-ink-muted hover:text-ink" onClick={onClose}>
          Close
        </button>
      </div>
      <fieldset
        disabled={lockedBy !== null}
        className="flex-1 space-y-4 overflow-y-auto px-4 py-4"
      >
        {lockedBy ? (
          <p className="rounded-md border border-highlight/40 bg-raised px-3 py-2 text-xs text-highlight">
            {lockedBy} is editing this sub-plan. It will unlock when they’re done.
          </p>
        ) : null}
        {subPlan.visibility === "private" ? (
          <div className="flex items-center justify-between gap-2 rounded-md border border-line bg-raised px-3 py-2">
            <span className="text-xs text-ink-muted">Private: only you can see this.</span>
            <Button
              variant="ghost"
              onClick={() => {
                if (
                  window.confirm(
                    "Share this sub-plan, and every private plan inside it, with everyone? This can't be undone.",
                  )
                ) {
                  onShare(subPlan.childPlanId);
                }
              }}
            >
              Share with everyone
            </Button>
          </div>
        ) : null}
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-muted">Name</span>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => {
              const trimmed = name.trim();
              if (trimmed && trimmed !== subPlan.name) onPatch(subPlan.childPlanId, { name: trimmed });
            }}
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-muted">Description</span>
          <Textarea
            value={description}
            rows={3}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={() => {
              if (description !== subPlan.description) {
                onPatch(subPlan.childPlanId, { description });
              }
            }}
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-muted">Status</span>
            <Select
              value={subPlan.status}
              onChange={(e) => onPatch(subPlan.childPlanId, { status: e.target.value as TaskStatus })}
            >
              {TASK_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-muted">Effort</span>
            <Select
              value={subPlan.effort ?? ""}
              onChange={(e) =>
                onPatch(subPlan.childPlanId, {
                  effort: e.target.value ? (e.target.value as TaskEffort) : null,
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
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-muted">Deadline</span>
          <Input
            type="datetime-local"
            value={toLocalInputValue(subPlan.deadline)}
            onChange={(e) =>
              onPatch(subPlan.childPlanId, {
                deadline: e.target.value ? new Date(e.target.value).toISOString() : null,
              })
            }
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-muted">Notes</span>
          <Textarea
            value={notes}
            rows={4}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => {
              if (notes !== subPlan.notes) onPatch(subPlan.childPlanId, { notes });
            }}
          />
        </label>
        <div className="space-y-2">
          <span className="text-xs font-medium text-ink-muted">Assignees</span>
          <p className="text-xs text-ink-muted">Derived from contained tasks.</p>
          {assigneeRows.length === 0 ? (
            <p className="text-sm text-ink-muted">None yet.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {assigneeRows.map(({ kerberos, name: fullName }) => (
                <div key={kerberos} className="flex items-center gap-1.5 rounded-md bg-raised px-2 py-1">
                  <span className="text-xs font-semibold text-highlight">{kerberos}</span>
                  <span className="text-xs text-ink-muted">{fullName}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          className="w-full text-status-blocked hover:bg-status-blocked/10"
          onClick={() => {
            if (!window.confirm("Remove this sub-plan from the canvas? The plan itself is not deleted.")) {
              return;
            }
            onDelete(linkId);
          }}
        >
          Delete sub-plan
        </Button>
      </fieldset>
    </aside>
  );
}
