'use client';

import { motion } from 'framer-motion';
import { ChoiceChips } from './choice-chips';
import type { ClarificationKind } from '@/stores/types';

interface ClarificationBlockProps {
  queryId: string;
  /** The alternatives to offer as chips. The block renders nothing if empty. */
  choices: string[];
  /** The single question to put to the user. Absent on the legacy pre-loop
   *  disambiguation path, whose answer text already asks it. */
  question?: string;
  /** Short noun phrase for the fact being asked about ("property
   *  classification"); becomes the block's label when present. */
  axis?: string;
  kind?: ClarificationKind;
  onSelect?: (choice: string) => void;
}

const DEFAULT_LABEL = 'To narrow this down';
const DISAMBIGUATION_LABEL = 'Property type';

/** Sentence-case an axis for use as a label: the judge writes axes in the
 *  corpus's own lowercase vocabulary ("property classification"), and the
 *  label is rendered in small caps, so only the first letter needs lifting. */
function labelForAxis(axis: string): string {
  const trimmed = axis.trim();
  if (!trimmed) return DEFAULT_LABEL;
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

/**
 * One subtly lifted section holding a clarification question and its options.
 *
 * Sits between the answer markdown and the source cards, so the fork the answer
 * left open is the last thing read before the citations — and so the chips are
 * never stranded below a wall of source cards. The question travels on the
 * `choices` WebSocket message rather than in the prose (Phase B is told not to
 * restate it), which is what lets the two be framed together here.
 *
 * Styling stays in the app's semantic tokens — a muted tint with a thin accent
 * rule — so it reads as lifted rather than loud, and adapts to both themes
 * without a per-theme color list.
 */
export function ClarificationBlock({
  queryId,
  choices,
  question,
  axis,
  kind,
  onSelect,
}: ClarificationBlockProps) {
  if (!choices || choices.length === 0) return null;

  const label = axis
    ? labelForAxis(axis)
    : kind === 'disambiguation'
      ? DISAMBIGUATION_LABEL
      : DEFAULT_LABEL;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="mt-5 overflow-hidden rounded-xl border border-border/70 bg-muted/40 px-4 py-3.5"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {question && (
        <p className="mt-1 text-sm font-normal leading-snug text-foreground">
          {question}
        </p>
      )}
      <ChoiceChips queryId={queryId} choices={choices} onSelect={onSelect} />
    </motion.div>
  );
}
