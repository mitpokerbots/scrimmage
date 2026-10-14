"use client";

import {
  Handle,
  NodeResizer,
  Position,
  useUpdateNodeInternals,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { memo, useContext, useEffect, type CSSProperties } from "react";
import { TASK_NODE_HEIGHT, TASK_NODE_WIDTH } from "../plan-focus";
import { EditingOutline } from "./editing-outline";
import type { ScenePath } from "../plan-scene";
import type { TaskDto } from "../types";
import { FocusTargetButton, useFocusPop } from "./focus-point";
import { PlanCanvasActionsContext } from "./sub-plan-frame";

export type TaskFlowNode = Node<
  {
    task: TaskDto;
    scale: number;
    path: ScenePath;
    ownerPlanId: string;
    resizable: boolean;
    startable: boolean;
    focused: boolean;
    onToggleFocus: (taskId: string) => void;
    dimmed: boolean;
    /** Someone else is editing this task. */
    editor: { kerberos: string; name: string } | null;
  },
  "task"
>;

type TaskPalette = {
  bg: string;
  border: string;
  title: string;
  accent: string;
  muted: string;
  pillBg: string;
};

function resolveTaskPalette(status: TaskDto["status"], startable: boolean): TaskPalette {
  if (status === "done") {
    return {
      bg: "var(--color-task-done-bg)",
      border: "var(--color-task-done-fg)",
      title: "var(--color-ink-muted)",
      accent: "var(--color-task-done-fg)",
      muted: "var(--color-ink-muted)",
      pillBg: "color-mix(in srgb, var(--color-task-done-fg) 18%, transparent)",
    };
  }
  if (status === "doing") {
    return {
      bg: "var(--color-task-doing-bg)",
      border: "var(--color-task-doing-fg)",
      title: "var(--color-ink)",
      accent: "var(--color-task-doing-fg)",
      muted: "var(--color-ink-muted)",
      pillBg: "color-mix(in srgb, var(--color-task-doing-fg) 18%, transparent)",
    };
  }
  if (status === "todo" && startable) {
    return {
      bg: "var(--color-task-startable-bg)",
      border: "var(--color-task-startable-fg)",
      title: "var(--color-ink)",
      accent: "var(--color-task-startable-fg)",
      muted: "var(--color-ink-muted)",
      pillBg: "color-mix(in srgb, var(--color-task-startable-fg) 18%, transparent)",
    };
  }
  // Idle / blocked / waiting todo — old surface gray
  return {
    bg: "var(--color-surface)",
    border: "var(--color-line)",
    title: "var(--color-ink)",
    accent: "var(--color-status-todo)",
    muted: "var(--color-ink-muted)",
    pillBg: "var(--color-raised)",
  };
}

/** Handle diameter in flow units — proportional to view scale. */
function handleSize(scale: number): number {
  return Math.max(1e-6, 10 * scale);
}

function formatDeadline(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const TaskNode = memo(function TaskNode({ id, data, selected }: NodeProps<TaskFlowNode>) {
  const { task, scale, resizable, path, startable, focused, onToggleFocus, dimmed, editor } = data;
  const { onTaskResize } = useContext(PlanCanvasActionsContext);
  const updateNodeInternals = useUpdateNodeInternals();
  const { rootRef, cardAnimationClass } = useFocusPop(focused);
  const handlePx = handleSize(scale);
  const minWidth = 120 * scale;
  const minHeight = 60 * scale;
  const handleStyle = {
    width: handlePx,
    height: handlePx,
    minWidth: handlePx,
    minHeight: handlePx,
    maxWidth: handlePx,
    maxHeight: handlePx,
  };
  const palette = resolveTaskPalette(task.status, startable);
  // Model size, not the projected view size, so nested frames stay proportional.
  // Pre-zoom width/height let `zoom` reflow text into the card; handles stay outside this wrapper.
  const textScale = Math.min(
    3,
    Math.max(1, Math.min(task.width / TASK_NODE_WIDTH, task.height / TASK_NODE_HEIGHT)),
  );
  // `zoom` forces extra layout work on every card. Default-sized tasks skip it.
  const contentStyle: CSSProperties =
    textScale === 1
      ? { width: "100%", height: "100%" }
      : {
          zoom: textScale,
          width: `calc(100% / ${textScale})`,
          height: `calc(100% / ${textScale})`,
        };

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, scale, task.width, task.height, updateNodeInternals]);

  const overdue =
    task.deadline !== null && task.status !== "done" && new Date(task.deadline) < new Date();
  const shownAssignees = task.assignees.slice(0, 4);
  const hiddenCount = task.assignees.length - shownAssignees.length;

  return (
    <div ref={rootRef} className={`group relative h-full w-full${dimmed ? " opacity-25" : ""}`}>
      {editor ? <EditingOutline editor={editor} scale={scale} radius={6 * scale} /> : null}
      {resizable && selected ? (
        <NodeResizer
          minWidth={minWidth}
          minHeight={minHeight}
          isVisible={selected}
          onResizeEnd={(_event, params) => {
            onTaskResize(task.id, path, scale, {
              x: params.x,
              y: params.y,
              width: params.width,
              height: params.height,
            });
          }}
          lineClassName="!border-highlight"
          handleClassName="!rounded-sm !border-highlight !bg-highlight"
        />
      ) : null}
      <Handle
        type="target"
        position={Position.Left}
        className="!border-none !bg-ink-muted"
        style={handleStyle}
      />
      <FocusTargetButton
        focused={focused}
        onToggle={() => onToggleFocus(task.id)}
        labelAdd="Mark as focus point"
        labelRemove="Remove focus point"
      />
      <div
        style={{
          transform: `scale(${scale})`,
          transformOrigin: "top left",
          width: task.width,
          height: task.height,
        }}
      >
        <div
          className={`h-full w-full rounded-md border shadow-md ${
            selected ? "border-highlight shadow-highlight/20" : ""
          } ${focused ? "focus-point" : ""} ${cardAnimationClass}`}
          style={{
            backgroundColor: palette.bg,
            borderColor: selected || focused ? undefined : palette.border,
          }}
        >
          <div className="h-full w-full overflow-hidden">
            <div className="box-border overflow-hidden px-3 py-2" style={contentStyle}>
              <div
                className={`line-clamp-2 break-words text-sm font-medium ${task.status === "done" ? "line-through" : ""}`}
                style={{ color: palette.title }}
              >
                {task.title}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1 overflow-hidden">
                <span
                  className="rounded-full px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide"
                  style={{ color: palette.accent, backgroundColor: palette.pillBg }}
                >
                  {task.status}
                </span>
                {task.effort ? (
                  <span
                    className="rounded-full px-1.5 py-px text-[10px] font-semibold"
                    style={{ color: palette.muted, backgroundColor: palette.pillBg }}
                  >
                    {task.effort}
                  </span>
                ) : null}
                {task.deadline ? (
                  <span
                    className={`rounded-full px-1.5 py-px text-[10px] font-semibold ${
                      overdue ? "bg-status-blocked/20 text-status-blocked" : ""
                    }`}
                    style={
                      overdue
                        ? undefined
                        : { color: palette.muted, backgroundColor: palette.pillBg }
                    }
                  >
                    {overdue ? "overdue " : ""}
                    {formatDeadline(task.deadline)}
                  </span>
                ) : null}
                {shownAssignees.map((kerberos) => (
                  <span
                    key={kerberos}
                    className="rounded-full px-1.5 py-px text-[10px] font-semibold text-highlight"
                    style={{ backgroundColor: palette.pillBg }}
                  >
                    {kerberos}
                  </span>
                ))}
                {hiddenCount > 0 ? (
                  <span className="text-[10px]" style={{ color: palette.muted }}>
                    +{hiddenCount}
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!border-none !bg-highlight"
        style={handleStyle}
      />
    </div>
  );
});
