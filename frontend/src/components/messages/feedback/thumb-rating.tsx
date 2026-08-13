'use client';

import { ThumbsUp, ThumbsDown } from 'lucide-react';
import type { Thumb } from '@/stores/feedback-store';

interface ThumbRatingProps {
  value: Thumb | null;
  onChange: (value: Thumb) => void;
  size?: 'sm' | 'md';
  // Lay the three buttons out on a 3-column grid so each aligns above a label.
  spread?: boolean;
  'aria-label'?: string;
}

// The "mixed" middle option has no dedicated lucide glyph, so it reuses
// ThumbsUp rotated 90° to read as a sideways ("so-so") thumb.
const OPTIONS: { value: Thumb; Icon: typeof ThumbsUp; rotate?: boolean; label: string; active: string }[] = [
  { value: 'up', Icon: ThumbsUp, label: 'Good', active: 'border-emerald-400 bg-emerald-500/15 text-emerald-500' },
  { value: 'mid', Icon: ThumbsUp, rotate: true, label: 'Mixed', active: 'border-amber-400 bg-amber-500/15 text-amber-500' },
  { value: 'down', Icon: ThumbsDown, label: 'Poor', active: 'border-red-400 bg-red-500/15 text-red-500' },
];

export function ThumbRating({ value, onChange, size = 'md', spread = false, ...rest }: ThumbRatingProps) {
  const dim = size === 'sm' ? 'h-8 w-8' : 'h-10 w-10';
  const icon = size === 'sm' ? 'h-4 w-4' : 'h-[18px] w-[18px]';
  const hasSelection = value !== null;

  return (
    <div
      className={spread ? 'grid grid-cols-3 gap-3 place-items-center' : 'flex items-center gap-2'}
      role="radiogroup"
      aria-label={rest['aria-label']}
    >
      {OPTIONS.map(({ value: v, Icon, rotate, label, active }) => {
        const selected = value === v;
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={label}
            title={label}
            onClick={() => onChange(v)}
            className={`flex ${dim} items-center justify-center rounded-full border transition-[color,background-color,border-color,transform,opacity] duration-150 cursor-pointer active:scale-95 ${
              selected
                ? `${active} scale-105`
                : `border-border text-muted-foreground hover:border-muted-foreground/40 hover:text-foreground ${
                    hasSelection ? 'opacity-40 hover:opacity-100' : ''
                  }`
            }`}
          >
            <Icon className={`${icon} ${rotate ? '-rotate-90' : ''}`} />
          </button>
        );
      })}
    </div>
  );
}
