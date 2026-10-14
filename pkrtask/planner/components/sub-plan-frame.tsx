"use client";

import { Handle, NodeResizer, Position, type Node, type NodeProps } from "@xyflow/react";
import { createContext, memo, useContext } from "react";
import { FRAME_HEADER_HEIGHT, type ScenePath } from "../plan-scene";
import type { Rect } from "../plan-focus";
import type { SubPlanDto } from "../types";
import { EditingOutline } from "./editing-outline";
import { FocusTargetButton, useFocusPop } from "./focus-point";

export type PlanCanvasActions = {
  onFrameResize: (linkId: string, path: ScenePath, frameScale: number, size: Rect) => void;
  onTaskResize: (taskId: string, path: ScenePath, taskScale: number, size: Rect) => void;
};

export const PlanCanvasActionsContext = createContext<PlanCanvasActions>({
  onFrameResize: () => {},
  onTaskResize: () => {},
});

export type SubPlanFrameNode = Node<
  {
    subPlan: SubPlanDto;
    linkId: string;
    path: ScenePath;
    scale: number;
    chromeless: boolean;
    resizable: boolean;
    focused: boolean;
    onToggleFocus: (linkId: string) => void;
    /** Someone else is editing this sub-plan. */
    editor: { kerberos: string; name: string } | null;
  },
  "subPlanFrame"
>;

/** Canonical corner radius at scale 1 (matches Tailwind rounded-md). Scales with nesting. */
const FRAME_RADIUS = 8;

const statusVar: Record<SubPlanDto["status"], string> = {
  todo: "var(--color-status-todo)",
  doing: "var(--color-status-doing)",
  done: "var(--color-status-done)",
  blocked: "var(--color-status-blocked)",
};

function formatDeadline(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Handle diameter in flow units — proportional to view scale. */
function handleSize(scale: number): number {
  return Math.max(1e-6, 10 * scale);
}

export const SubPlanFrame = memo(function SubPlanFrame({ data, selected }: NodeProps<SubPlanFrameNode>) {
  const { subPlan, scale, chromeless, resizable, linkId, path, focused, onToggleFocus, editor } = data;
  const { onFrameResize } = useContext(PlanCanvasActionsContext);
  const { rootRef, cardAnimationClass } = useFocusPop(focused && !chromeless, {
    elevateNode: false,
    motion: "glow",
  });
  const headerWorld = FRAME_HEADER_HEIGHT * scale;
  const minWidth = 120 * scale;
  const minHeight = 80 * scale;
  const handlePx = handleSize(scale);
  const handleStyle = {
    width: handlePx,
    height: handlePx,
    minWidth: handlePx,
    minHeight: handlePx,
    maxWidth: handlePx,
    maxHeight: handlePx,
  };
  const radius = FRAME_RADIUS * scale;

  if (chromeless) {
    return (
      <div className="pointer-events-none" style={{ width: "100%", height: "100%" }}>
        <Handle type="target" position={Position.Left} className="!opacity-0 !pointer-events-none" />
        <Handle type="source" position={Position.Right} className="!opacity-0 !pointer-events-none" />
      </div>
    );
  }

  const shownAssignees = subPlan.assignees.slice(0, 4);
  const hiddenCount = subPlan.assignees.length - shownAssignees.length;

  return (
    <div ref={rootRef} className="group relative h-full w-full">
      {editor ? <EditingOutline editor={editor} scale={scale} radius={radius} /> : null}
      {resizable && selected ? (
        <NodeResizer
          minWidth={minWidth}
          minHeight={minHeight}
          isVisible={selected}
          onResizeEnd={(_event, params) => {
            onFrameResize(linkId, path, scale, {
              x: params.x,
              y: params.y,
              width: params.width,
              height: params.height,
            });
          }}
          lineClassName="!border-highlight"
          handleClassName="!h-2 !w-2 !rounded-sm !border-highlight !bg-highlight"
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
        onToggle={() => onToggleFocus(linkId)}
        labelAdd="Mark sub-plan as focus point"
        labelRemove="Remove sub-plan focus point"
      />
      <div
        className={`h-full w-full overflow-hidden border bg-surface/40 ${
          selected || focused ? "border-highlight" : "border-line"
        } ${focused ? "focus-point" : ""} ${cardAnimationClass}`}
        style={{ borderRadius: radius }}
      >
        {/*
          Header chrome policy (same at every nesting depth; CSS scale keeps proportions):
          - Title: text-sm semibold, left, truncates
          - Trailing cluster (right, fixed order): status · done/total · deadline? · assignees?
          Inner width is 100%/scale so after transform:scale the row fills the bar (avoids
          the old width:100% + scale strip that made type/status look inconsistent).
        */}
        <div
          className="overflow-hidden border-b border-line bg-raised/80"
          style={{ height: headerWorld }}
        >
          <div
            style={{
              transform: `scale(${scale})`,
              transformOrigin: "top left",
              width: `${100 / scale}%`,
              height: FRAME_HEADER_HEIGHT,
            }}
          >
            <div className="flex h-11 items-center gap-2 px-3">
              <span className="min-w-0 flex-1 truncate text-sm font-semibold leading-none text-ink">
                {subPlan.name}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                <span
                  className="rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase leading-none tracking-wide"
                  style={{ color: statusVar[subPlan.status], backgroundColor: "var(--color-surface)" }}
                >
                  {subPlan.status}
                </span>
                <span className="text-[10px] font-semibold leading-none text-ink-muted">
                  {subPlan.rollup.done}/{subPlan.rollup.total}
                </span>
                {subPlan.rollup.nearestDeadline ? (
                  <span className="rounded-full bg-surface px-1.5 py-0.5 text-[10px] font-semibold leading-none text-ink-muted">
                    {formatDeadline(subPlan.rollup.nearestDeadline)}
                  </span>
                ) : null}
                {shownAssignees.map((kerberos) => (
                  <span
                    key={kerberos}
                    className="rounded-full bg-surface px-1.5 py-0.5 text-[10px] font-semibold leading-none text-highlight"
                  >
                    {kerberos}
                  </span>
                ))}
                {hiddenCount > 0 ? (
                  <span className="text-[10px] leading-none text-ink-muted">+{hiddenCount}</span>
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
