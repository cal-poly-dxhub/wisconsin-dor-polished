'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import {
  defaultOpenGroupIds,
  groupSourceEntries,
  type SourceKind,
} from './source-taxonomy';

export interface SourceEntry {
  /** React key for the card. */
  key: string;
  kind: SourceKind;
  node: ReactNode;
}

interface SourceGroupGridProps {
  entries: SourceEntry[];
  /**
   * Cards that sit above the groups and are never collapsed — today just the
   * decision-flowchart card, which keeps its own look.
   */
  leading?: ReactNode;
  className?: string;
}

const GRID_CLASS =
  'inline-sources-row grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] items-stretch gap-2.5';

/**
 * The "Sources (…)" grid, bucketed by authority level. Each group is a
 * one-line collapsible header; small result sets open fully, larger ones open
 * only the two highest-authority groups (see defaultOpenGroupIds).
 */
export function SourceGroupGrid({ entries, leading, className }: SourceGroupGridProps) {
  const groups = useMemo(() => groupSourceEntries(entries), [entries]);
  const defaultOpen = useMemo(
    () => new Set(defaultOpenGroupIds(groups)),
    [groups]
  );

  // Only the user's explicit toggles are stored, so the defaults stay correct
  // if the entry set changes underneath us (streaming backfills, for example).
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const isOpen = (id: string) => overrides[id] ?? defaultOpen.has(id);
  const toggle = (id: string) =>
    setOverrides(prev => ({ ...prev, [id]: !(prev[id] ?? defaultOpen.has(id)) }));

  if (!groups.length && !leading) return null;

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {leading && (
        <div className={GRID_CLASS}>
          {/* Wrapper div keeps the `.inline-sources-row > div` height rules in
              chat-message.css applying to the card, as before. */}
          <div>{leading}</div>
        </div>
      )}

      {groups.map(group => {
        const open = isOpen(group.id);
        return (
          <div key={group.id} className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={() => toggle(group.id)}
              aria-expanded={open}
              className="text-muted-foreground hover:text-foreground flex w-full cursor-pointer items-center gap-1.5 text-left text-[11px] font-semibold tracking-[0.08em] uppercase transition-colors"
            >
              <svg
                width="10"
                height="10"
                viewBox="0 0 12 12"
                fill="none"
                aria-hidden="true"
                className={cn('shrink-0 transition-transform duration-200', open && 'rotate-90')}
              >
                <path
                  d="M4.5 2.5L8 6L4.5 9.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span>{group.label}</span>
              <span className="text-muted-foreground/60 font-normal">
                {group.items.length}
              </span>
            </button>
            {open && (
              <div className={GRID_CLASS}>
                {group.items.map(entry => (
                  <div key={entry.key}>{entry.node}</div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
