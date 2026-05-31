import { BadgeWithFade } from '@/components/ui/badge-with-fade';
import { cn } from '@/lib/utils';

interface AuthorityBadgeProps {
  authorityLevel: number;
  size?: 'sm' | 'md';
}

const AUTHORITY_LABELS: Record<number, { label: string; tone: string }> = {
  1: { label: 'Constitution', tone: 'bg-indigo-200 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200' },
  2: { label: 'Statute', tone: 'bg-blue-200 text-blue-900 dark:bg-blue-950 dark:text-blue-200' },
  3: { label: 'Case Law', tone: 'bg-purple-200 text-purple-900 dark:bg-purple-950 dark:text-purple-200' },
  4: { label: 'Admin Rule', tone: 'bg-teal-200 text-teal-900 dark:bg-teal-950 dark:text-teal-200' },
  5: { label: 'WPAM', tone: 'bg-green-200 text-green-900 dark:bg-green-950 dark:text-green-200' },
  6: { label: 'FAQ', tone: 'bg-yellow-200 text-yellow-900 dark:bg-yellow-950 dark:text-yellow-200' },
  7: { label: 'Gov. Pub.', tone: 'bg-orange-200 text-orange-900 dark:bg-orange-950 dark:text-orange-200' },
  8: { label: 'IAAO (advisory)', tone: 'bg-rose-200 text-rose-900 dark:bg-rose-950 dark:text-rose-200' },
  9: { label: 'USPAP (advisory)', tone: 'bg-rose-200 text-rose-900 dark:bg-rose-950 dark:text-rose-200' },
};

export function AuthorityBadge({ authorityLevel, size = 'sm' }: AuthorityBadgeProps) {
  const meta = AUTHORITY_LABELS[authorityLevel];
  if (!meta) return null;

  const sizeClass = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1';

  return (
    <BadgeWithFade variant="outline" className={cn(meta.tone, sizeClass, 'font-normal')}>
      {meta.label}
    </BadgeWithFade>
  );
}
