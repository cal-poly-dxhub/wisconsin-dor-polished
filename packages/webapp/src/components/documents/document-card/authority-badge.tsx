import { BadgeWithFade } from '@/components/ui/badge-with-fade';
import { cn } from '@/lib/utils';

interface AuthorityBadgeProps {
  authorityLevel: number;
  size?: 'sm' | 'md';
}

const AUTHORITY_LABELS: Record<number, { label: string; tone: string }> = {
  1: { label: 'Constitution', tone: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200' },
  2: { label: 'Statute', tone: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200' },
  3: { label: 'Case Law', tone: 'bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-200' },
  4: { label: 'Admin Rule', tone: 'bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-200' },
  5: { label: 'WPAM', tone: 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-200' },
  6: { label: 'FAQ', tone: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200' },
  7: { label: 'Gov. Pub.', tone: 'bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200' },
  8: { label: 'IAAO (advisory)', tone: 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200' },
  9: { label: 'USPAP (advisory)', tone: 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200' },
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
