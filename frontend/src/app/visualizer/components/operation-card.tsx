"use client";

import { useMemo } from "react";

interface OperationCardProps {
  activeOp: {
    toolName: string;
    summary: string;
    chunkIds: string[];
    revealedCount: number;
    status: "revealing" | "complete";
  } | null;
}

export function OperationCard({ activeOp }: OperationCardProps) {
  const tiles = useMemo(() => {
    if (!activeOp) return [];
    const total = activeOp.chunkIds.length;
    const displayCount = Math.min(total, 30);
    return Array.from({ length: displayCount }, (_, i) => ({
      revealed: i < activeOp.revealedCount,
      index: i,
    }));
  }, [activeOp]);

  const overflow =
    activeOp && activeOp.chunkIds.length > 30
      ? activeOp.chunkIds.length - 30
      : 0;

  return (
    <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-50 pointer-events-none">
      <div
        className={`
          pointer-events-auto
          max-w-md w-auto
          bg-background/90 backdrop-blur-sm
          border border-border/30
          rounded-lg
          px-4 py-3
          transition-all duration-300
          ${activeOp ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0 pointer-events-none"}
        `}
        style={{ fontFamily: "system-ui, sans-serif" }}
      >
        {activeOp && (
          <>
            {/* Top line: tool name + status dot */}
            <div className="flex items-center justify-between gap-3 mb-1">
              <span className="font-mono text-xs uppercase tracking-wider text-foreground/50">
                {activeOp.toolName}
              </span>
              <span
                className={`
                  inline-block w-2 h-2 rounded-full
                  ${
                    activeOp.status === "revealing"
                      ? "bg-foreground/40 animate-pulse"
                      : "bg-foreground/70"
                  }
                `}
              />
            </div>

            {/* Summary */}
            <p className="text-sm text-foreground/80 mb-2 leading-snug">
              {activeOp.summary}
            </p>

            {/* Mini tile cluster */}
            {tiles.length > 0 && (
              <div className="flex flex-wrap items-center gap-1 mb-2">
                {tiles.map((tile) => (
                  <span
                    key={tile.index}
                    className={`
                      inline-block w-2 h-2 rounded-[2px]
                      transition-transform duration-200 ease-out
                      ${tile.revealed ? "bg-white scale-100" : "bg-foreground/20 scale-100"}
                    `}
                    style={{
                      transitionDelay: tile.revealed
                        ? `${Math.min(tile.index * 50, 1500)}ms`
                        : "0ms",
                      transform: tile.revealed ? "scale(1)" : "scale(0.6)",
                    }}
                  />
                ))}
                {overflow > 0 && (
                  <span className="text-[10px] text-foreground/40 ml-1">
                    +{overflow}
                  </span>
                )}
              </div>
            )}

            {/* Bottom stats */}
            {activeOp.status === "complete" && (
              <div className="text-[10px] text-foreground/40">
                {activeOp.chunkIds.length} chunk
                {activeOp.chunkIds.length !== 1 ? "s" : ""} retrieved
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
