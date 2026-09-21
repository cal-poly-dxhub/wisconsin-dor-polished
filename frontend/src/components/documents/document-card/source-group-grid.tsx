'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { groupSourceEntries, type SourceKind } from './source-taxonomy';

export interface SourceEntry {
  /** React key for the card. */
  key: string;
  kind: SourceKind;
  node: ReactNode;
}

interface SourceGroupGridProps {
  entries: SourceEntry[];
  /**
   * Cards that sit above the columns and are never collapsed — today just the
   * decision-flowchart card, which keeps its own look.
   */
  leading?: ReactNode;
  className?: string;
}

const LEADING_ROW_CLASS =
  'inline-sources-row grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] items-stretch gap-2.5';

/**
 * Every cell in the grid is the same fixed height (roughly two source cards
 * plus the header). Past that a column scrolls instead of growing, so a
 * ten-statute answer stays as short as a two-statute one and rows line up.
 */
const ROW_HEIGHT_CLASS = 'h-[24rem]';

/**
 * The "Sources (…)" area: one cell per authority group in a shared-border
 * grid (a 1px gap over a border-colored backdrop draws the lines once, so
 * neighbours share them). Extra groups wrap to a new row; every cell in every
 * row is the same fixed height. Cards stack inside a cell and scroll past the
 * cap, with a bottom fade as the scroll hint.
 */
export function SourceGroupGrid({ entries, leading, className }: SourceGroupGridProps) {
  const groups = useMemo(() => groupSourceEntries(entries), [entries]);

  if (!groups.length && !leading) return null;

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {leading && (
        <div className={LEADING_ROW_CLASS}>
          {/* Wrapper div keeps the `.inline-sources-row > div` height rules in
              chat-message.css applying to the card, as before. */}
          <div>{leading}</div>
        </div>
      )}

      {groups.length > 0 && (
        <div className="border-border/60 grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-px overflow-hidden rounded-lg border bg-border/60">
          {groups.map(group => (
            <section
              key={group.id}
              aria-label={group.label}
              className={cn('bg-background flex min-w-0 flex-col gap-1.5 p-2.5', ROW_HEIGHT_CLASS)}
            >
              <h4 className="text-muted-foreground flex items-center gap-1.5 px-1 pb-[3px] text-[11px] font-semibold tracking-[0.08em] uppercase">
                <span className="truncate">{group.label}</span>
                <span className="text-muted-foreground/60 font-normal">{group.items.length}</span>
              </h4>
              <SourceColumn>
                {group.items.map(entry => (
                  <div key={entry.key} className="shrink-0">
                    {entry.node}
                  </div>
                ))}
              </SourceColumn>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * A height-capped, vertically scrolling stack of cards with a bottom fade as
 * the scroll hint. The fade shows only while there is content below the fold
 * (`data-overflow`), so a short column or one scrolled to the end has no fade.
 */
function SourceColumn({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () =>
      setOverflow(el.scrollHeight - el.clientHeight - el.scrollTop > 4);
    update();
    el.addEventListener('scroll', update, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener('scroll', update);
      ro?.disconnect();
    };
  }, [children]);

  return (
    <div
      className="source-column-wrap relative min-h-0 flex-1"
      data-overflow={overflow ? 'true' : 'false'}
    >
      <div ref={ref} className="source-column flex h-full flex-col gap-2.5 overflow-y-auto pr-1">
        {children}
      </div>
      <div aria-hidden="true" className="source-column-fade" />
    </div>
  );
}
