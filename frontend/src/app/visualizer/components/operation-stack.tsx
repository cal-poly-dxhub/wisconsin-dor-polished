"use client";

import { useEffect, useRef, useState } from "react";
import type { OperationEntry } from "../hooks/use-highlight-timeline";
import type { ResourceItem } from "@/stores/types";
import { ResponseModal } from "./response-modal";

const TOOL_LABELS: Record<string, string> = {
  vector_search: "Performing vector search",
  get_neighbors: "Getting neighbors",
  search_document: "Searching document",
  list_sections: "Listing sections",
  get_section: "Getting section",
  get_authority_chain: "Getting authority chain",
  fetch_case_opinion: "Fetching case opinion",
  prepare_answer: "Preparing answer",
  faq_search: "Searching FAQs",
  refine_query: "Refining query",
};

function formatToolLabel(toolName: string): string {
  if (TOOL_LABELS[toolName]) return TOOL_LABELS[toolName];
  return toolName
    .split("_")
    .map((word, i) => (i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(" ");
}

interface OperationStackProps {
  operations: OperationEntry[];
  onTileClick?: (chunkId: string, event?: React.MouseEvent) => void;
  answerText?: string;
  answerComplete?: boolean;
  docUrls?: Record<string, string>;
  resourceItems?: ResourceItem[];
  query?: string | null;
  done?: boolean;
  onReplay?: (opId: string) => void;
}

export function OperationStack({
  operations,
  onTileClick,
  answerText = "",
  answerComplete = false,
  done = false,
  onReplay,
  docUrls = {},
  resourceItems = [],
  query = null,
}: OperationStackProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [responseOpen, setResponseOpen] = useState(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [operations.length]);

  return (
    <>
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto py-3">
        {operations.length === 0 && (
          <p className="text-sm text-muted-foreground/40 px-5">
            Waiting for query...
          </p>
        )}
        <div className="flex flex-col">
          {(() => {
            const groups = groupOperations(operations);
            // Find the last operation that's complete or revealing (the "current" one)
            const lastActiveIdx = operations.findLastIndex(
              (op) => op.status === "complete" || op.status === "revealing"
            );
            const latestCompleteId = lastActiveIdx >= 0 ? operations[lastActiveIdx].id : null;

            return groups.map((group) =>
            group.ops.length === 1 ? (
              <OperationItem
                key={group.ops[0].id}
                op={group.ops[0]}
                onTileClick={onTileClick}
                answerText={answerText}
                onViewResponse={() => setResponseOpen(true)}
                replayable={done && !!onReplay}
                onReplay={onReplay ? () => onReplay(group.ops[0].id) : undefined}
                isLatestComplete={group.ops[0].id === latestCompleteId}
              />
            ) : (
              <OperationGroup
                key={group.ops[0].id}
                group={group}
                onTileClick={onTileClick}
                replayable={done && !!onReplay}
                onReplay={onReplay ? () => onReplay(group.ops[0].id) : undefined}
                isLatestComplete={group.ops.some((op) => op.id === latestCompleteId)}
              />
            )
          );
          })()}
        </div>
      </div>

      <ResponseModal
        open={responseOpen}
        onOpenChange={setResponseOpen}
        answerText={answerText}
        answerComplete={answerComplete}
        docUrls={docUrls}
        resourceItems={resourceItems}
        query={query}
      />
    </>
  );
}

function OperationItem({
  op,
  onTileClick,
  answerText,
  onViewResponse,
  replayable,
  onReplay,
  isLatestComplete,
}: {
  op: OperationEntry;
  onTileClick?: (chunkId: string, event?: React.MouseEvent) => void;
  answerText: string;
  onViewResponse: () => void;
  replayable: boolean;
  onReplay?: () => void;
  isLatestComplete: boolean;
}) {
  const label = formatToolLabel(op.toolName);
  const isActive = op.status === "revealing";
  const isPending = op.status === "pending";

  const displayCount = Math.min(op.chunkIds.length, 40);
  const overflow = op.chunkIds.length > 40 ? op.chunkIds.length - 40 : 0;

  const isPrepareAnswer = op.toolName === "prepare_answer";

  return (
    <div
      onClick={replayable && onReplay ? onReplay : undefined}
      className={`
        border-b border-border/30 px-5 py-4 last:border-b-0
        transition-all duration-300
        ${replayable ? "cursor-pointer hover:opacity-80 hover:bg-foreground/[0.02]" : ""}
        ${isActive || isLatestComplete ? "bg-foreground/[0.03] opacity-100" : ""}
        ${isPending ? "opacity-50" : ""}
        ${op.status === "complete" && !isPrepareAnswer && !isLatestComplete ? "opacity-40" : ""}
      `}
    >
      <div className="grid grid-cols-[0.5rem_1fr] gap-x-2.5 gap-y-0">
        {/* Status dot */}
        <span
          className={`
            w-2 h-2 rounded-full self-center
            ${isActive ? "bg-white/70 animate-pulse" : ""}
            ${isPending ? "bg-white/20" : ""}
            ${op.status === "complete" ? "bg-white/30" : ""}
          `}
        />

        {/* Title + count */}
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm text-foreground/75">{label}</span>
          {op.status === "complete" && op.chunkIds.length > 0 && (
            <span className="ml-auto text-sm text-muted-foreground/50 tabular-nums shrink-0">
              {op.chunkIds.length}
            </span>
          )}
        </div>

        {/* Summary */}
        {op.summary && (
          <p className="col-start-2 text-sm text-foreground/50 leading-normal">
            {op.summary}
          </p>
        )}

        {isPrepareAnswer && (
          <div className="col-start-2 mt-2">
            <button
              type="button"
              onClick={onViewResponse}
              disabled={!answerText}
              className="text-xs font-medium tracking-wide text-foreground/70 border border-border/40 rounded px-2.5 py-1 transition-colors hover:bg-foreground/[0.04] hover:text-foreground disabled:opacity-35 disabled:cursor-not-allowed disabled:hover:bg-transparent"
            >
              View response
            </button>
          </div>
        )}

        {/* Mini tile cluster */}
        {op.chunkIds.length > 0 && (
          <div className="col-start-2 flex flex-wrap items-center gap-1 mt-3">
            {Array.from({ length: displayCount }, (_, i) => {
              const revealed = i < op.revealedCount;
              const chunkId = op.chunkIds[i];
              return (
                <span
                  key={i}
                  onClick={revealed && chunkId && onTileClick ? (e: React.MouseEvent) => onTileClick(chunkId, e) : undefined}
                  style={{
                    display: "inline-block",
                    width: 12,
                    height: 12,
                    borderRadius: 1,
                    backgroundColor: revealed
                      ? "rgba(255,255,255,0.8)"
                      : "rgba(255,255,255,0.06)",
                    transform: revealed ? "scale(1)" : "scale(0)",
                    transition: "transform 200ms ease-out, background-color 150ms ease-out",
                    transitionDelay: revealed ? `${Math.min(i * 25, 500)}ms` : "0ms",
                    cursor: revealed && chunkId && onTileClick ? "pointer" : undefined,
                  }}
                />
              );
            })}
            {overflow > 0 && (
              <span className="text-sm text-foreground/40 ml-0.5">
                +{overflow}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Grouping logic ─── */

interface OpGroup {
  toolName: string;
  ops: OperationEntry[];
}

function groupOperations(operations: OperationEntry[]): OpGroup[] {
  const groups: OpGroup[] = [];
  for (const op of operations) {
    const last = groups[groups.length - 1];
    if (last && last.toolName === op.toolName) {
      last.ops.push(op);
    } else {
      groups.push({ toolName: op.toolName, ops: [op] });
    }
  }
  return groups;
}

/* ─── Grouped parallel operations ─── */

function OperationGroup({
  group,
  onTileClick,
  replayable,
  onReplay,
  isLatestComplete,
}: {
  group: OpGroup;
  onTileClick?: (chunkId: string, event?: React.MouseEvent) => void;
  replayable: boolean;
  onReplay?: () => void;
  isLatestComplete: boolean;
}) {
  const label = formatToolLabel(group.toolName);
  const totalChunks = group.ops.reduce((sum, op) => sum + op.chunkIds.length, 0);
  const allComplete = group.ops.every((op) => op.status === "complete");
  const anyActive = group.ops.some((op) => op.status === "revealing");

  return (
    <div
      onClick={replayable && onReplay ? onReplay : undefined}
      className={`
        border-b border-border/30 px-5 py-4
        transition-all duration-300
        ${replayable ? "cursor-pointer hover:opacity-80 hover:bg-foreground/[0.02]" : ""}
        ${anyActive || isLatestComplete ? "bg-foreground/[0.03] opacity-100" : ""}
        ${allComplete && !isLatestComplete ? "opacity-40" : ""}
      `}
    >
      {/* Group header */}
      <div className="flex items-center gap-2 mb-2">
        <span
          className={`
            w-2 h-2 rounded-full
            ${anyActive ? "bg-white/70 animate-pulse" : "bg-white/30"}
          `}
        />
        <span className="text-sm text-foreground/75">{label}</span>
        <span className="text-xs text-muted-foreground/40">
          {group.ops.length} calls
        </span>
        {allComplete && totalChunks > 0 && (
          <span className="ml-auto text-sm text-muted-foreground/50 tabular-nums">
            {totalChunks}
          </span>
        )}
      </div>

      {/* Sub-items with left accent border */}
      <div className="ml-3 border-l-2 border-foreground/10 pl-3 space-y-2">
        {group.ops.map((op) => {
          const subLabel = op.vectorIndex ? `Q${op.vectorIndex}` : "";
          const displayCount = Math.min(op.chunkIds.length, 20);
          const overflow = op.chunkIds.length > 20 ? op.chunkIds.length - 20 : 0;

          return (
            <div key={op.id}>
              {/* Sub-header */}
              <div className="flex items-center gap-2">
                {subLabel && (
                  <span className="text-xs font-medium text-foreground/50">{subLabel}</span>
                )}
                <span className="text-xs text-foreground/40 truncate flex-1">
                  {op.summary}
                </span>
                {op.status === "complete" && op.chunkIds.length > 0 && (
                  <span className="text-xs text-muted-foreground/40 tabular-nums shrink-0">
                    {op.chunkIds.length}
                  </span>
                )}
              </div>

              {/* Mini tiles */}
              {op.chunkIds.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 mt-1.5">
                  {Array.from({ length: displayCount }, (_, i) => {
                    const revealed = i < op.revealedCount;
                    const chunkId = op.chunkIds[i];
                    return (
                      <span
                        key={i}
                        onClick={(e: React.MouseEvent) => {
                          if (revealed && chunkId && onTileClick) {
                            e.stopPropagation();
                            onTileClick(chunkId, e);
                          }
                        }}
                        style={{
                          display: "inline-block",
                          width: 10,
                          height: 10,
                          borderRadius: 1,
                          backgroundColor: revealed
                            ? "rgba(255,255,255,0.8)"
                            : "rgba(255,255,255,0.06)",
                          transform: revealed ? "scale(1)" : "scale(0)",
                          transition: "transform 200ms ease-out, background-color 150ms ease-out",
                          transitionDelay: revealed ? `${Math.min(i * 25, 400)}ms` : "0ms",
                          cursor: revealed && chunkId && onTileClick ? "pointer" : undefined,
                        }}
                      />
                    );
                  })}
                  {overflow > 0 && (
                    <span className="text-xs text-foreground/30 ml-0.5">
                      +{overflow}
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
