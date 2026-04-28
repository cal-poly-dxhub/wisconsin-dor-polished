import { BadgeWithFade } from '@/components/ui/badge-with-fade';
import { cn } from '@/lib/utils';

export type DiscoveryTag =
  | 'vector-search'
  | 'graph-neighbor'
  | 'fetched'
  | 'framework-list'
  | 'opinion-fetched'
  | 'unknown';

interface DiscoveryBadgeProps {
  tag: DiscoveryTag;
  size?: 'sm' | 'md';
}

const TAG_META: Record<DiscoveryTag, { label: string; tone: string; description: string }> = {
  'vector-search': {
    label: 'Semantic match',
    tone: 'border-blue-300 text-blue-800 dark:border-blue-700 dark:text-blue-200',
    description: 'Found via semantic similarity to your question',
  },
  'graph-neighbor': {
    label: 'Related via graph',
    tone: 'border-purple-300 text-purple-800 dark:border-purple-700 dark:text-purple-200',
    description: 'Connected through legal authority or citations',
  },
  fetched: {
    label: 'Directly looked up',
    tone: 'border-teal-300 text-teal-800 dark:border-teal-700 dark:text-teal-200',
    description: 'The agent fetched this specific document by ID',
  },
  'framework-list': {
    label: 'From framework list',
    tone: 'border-slate-300 text-slate-800 dark:border-slate-700 dark:text-slate-200',
    description: 'Part of the authority framework the agent browsed',
  },
  'opinion-fetched': {
    label: 'Court opinion',
    tone: 'border-amber-300 text-amber-800 dark:border-amber-700 dark:text-amber-200',
    description: 'Full court opinion the agent pulled for this citation',
  },
  unknown: {
    label: 'Source',
    tone: 'border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-300',
    description: 'Discovery method unrecorded',
  },
};

export function DiscoveryBadge({ tag, size = 'sm' }: DiscoveryBadgeProps) {
  const meta = TAG_META[tag] ?? TAG_META.unknown;
  const sizeClass = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1';

  return (
    <BadgeWithFade
      variant="outline"
      className={cn(meta.tone, sizeClass, 'font-normal')}
    >
      {meta.label}
    </BadgeWithFade>
  );
}
