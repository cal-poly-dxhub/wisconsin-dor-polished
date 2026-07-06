"use client";

import { useMemo } from "react";

interface AnswerTrayProps {
  revealedIds: Set<string>;
  done: boolean;
  onTileClick?: (chunkId: string, event?: React.MouseEvent) => void;
}

export function AnswerTray({ revealedIds, done, onTileClick }: AnswerTrayProps) {
  const tiles = useMemo(() => Array.from(revealedIds), [revealedIds]);

  return (
    <div className="shrink-0 border-t border-border/25 dark:bg-foreground/[0.03] px-5 pt-4 pb-4 min-h-[110px] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <span className="text-sm text-muted-foreground/60">
          Gathered
        </span>
        {tiles.length > 0 && (
          <span
            className={`
              text-sm tabular-nums transition-colors duration-300
              ${done ? "text-foreground/70" : "text-muted-foreground/50"}
            `}
          >
            {tiles.length}
          </span>
        )}
      </div>

      {/* Tile area */}
      <div className="flex-1 overflow-y-auto min-h-0 pb-3">
        {tiles.length === 0 ? (
          <p className="text-sm text-muted-foreground/30">
            Chunks appear here as retrieved...
          </p>
        ) : (
          <div className="flex flex-wrap gap-1 content-start">
            {tiles.map((id) => (
              <span
                key={id}
                onClick={onTileClick ? (e: React.MouseEvent) => onTileClick(id, e) : undefined}
                style={{
                  display: "inline-block",
                  width: 12,
                  height: 12,
                  borderRadius: 1,
                  backgroundColor: "rgba(255,255,255,0.6)",
                  cursor: onTileClick ? "pointer" : undefined,
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
