"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { ChunkMeta } from "../hooks/use-chunk-metadata";

interface ChunkTooltipProps {
  chunkId: string;
  docTitle: string;
  meta: ChunkMeta | null;
  position: { x: number; y: number };
  onClose: () => void;
}

export function ChunkTooltip({ chunkId, docTitle, meta, position, onClose }: ChunkTooltipProps) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [adjusted, setAdjusted] = useState<{ left: number; top: number; arrowLeft: number } | null>(null);

  // Clamp position to viewport after first paint
  useEffect(() => {
    const el = tooltipRef.current;
    if (!el) return;

    requestAnimationFrame(() => {
      const rect = el.getBoundingClientRect();
      const pad = 8;
      const arrowGap = 10;

      let left = position.x - rect.width / 2;
      let top = position.y - rect.height - arrowGap;

      // Clamp horizontal
      if (left < pad) left = pad;
      if (left + rect.width > window.innerWidth - pad) left = window.innerWidth - pad - rect.width;

      // Clamp vertical — if no room above, show below
      if (top < pad) top = position.y + 16;

      // Arrow points to the original click x relative to the tooltip's left
      const arrowLeft = Math.max(10, Math.min(position.x - left, rect.width - 10));

      setAdjusted({ left, top, arrowLeft });
    });
  }, [position]);

  // Click outside to dismiss
  const handleClickOutside = useCallback((e: MouseEvent) => {
    if (tooltipRef.current && !tooltipRef.current.contains(e.target as Node)) {
      onClose();
    }
  }, [onClose]);

  useEffect(() => {
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [handleClickOutside]);

  return (
    <div
      ref={tooltipRef}
      className="fixed z-[200]"
      style={{
        left: adjusted?.left ?? position.x,
        top: adjusted?.top ?? position.y,
        opacity: adjusted ? 1 : 0,
      }}
    >
      <div className="relative max-w-[300px]">
        <div className="bg-background/95 backdrop-blur-sm border border-border/70 rounded-lg shadow-xl px-4 py-3 text-left">
          {/* Chunk ID */}
          <p className="text-[11px] text-muted-foreground/50 font-mono mb-1.5 truncate">
            {chunkId}
          </p>

          {/* Doc title */}
          <p className="text-sm text-foreground/90 font-medium leading-snug mb-2">
            {docTitle}
          </p>

          {/* Metadata rows */}
          {meta && (
            <div className="space-y-1">
              {meta.h && (
                <p className="text-xs text-foreground/70 leading-snug">
                  {meta.h}
                </p>
              )}
              {meta.sh && (
                <p className="text-xs text-foreground/50 leading-snug pl-2">
                  {meta.sh}
                </p>
              )}
              {(meta.sp != null || meta.ep != null) && (
                <p className="text-[11px] text-muted-foreground/60 mt-1.5">
                  {meta.sp != null && meta.ep != null
                    ? meta.sp === meta.ep
                      ? `Page ${meta.sp}`
                      : `Pages ${meta.sp}–${meta.ep}`
                    : meta.sp != null
                      ? `Page ${meta.sp}+`
                      : `Page ?–${meta.ep}`}
                </p>
              )}
            </div>
          )}

          {!meta && (
            <p className="text-[11px] text-muted-foreground/40 italic">
              No page metadata
            </p>
          )}
        </div>

      </div>
    </div>
  );
}
