'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  useActivityData,
  type ActivitySummary,
  type ActivityFilters,
  type FeedbackFilter,
} from '@/hooks/use-activity-data';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import {
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
  MessageSquare,
  Search,
  Filter,
  ChevronDown,
  ExternalLink,
} from 'lucide-react';

type TimeRange = 'day' | 'week' | 'month' | 'all';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

const TIME_RANGE_LABELS: Record<TimeRange, string> = {
  day: 'Last 24 hours',
  week: 'Last 7 days',
  month: 'Last 30 days',
  all: 'All time',
};

const FEEDBACK_LABELS: Record<FeedbackFilter, string> = {
  all: 'All feedback',
  up: 'Thumbs up only',
  down: 'Thumbs down only',
  rated: 'Has rating',
  unrated: 'No rating',
};

function isTimeRange(value: string | null): value is TimeRange {
  return value != null && value in TIME_RANGE_LABELS;
}

function isFeedbackFilter(value: string | null): value is FeedbackFilter {
  return value != null && value in FEEDBACK_LABELS;
}

function getTimeRangeISO(range: TimeRange): string | undefined {
  if (range === 'all') return undefined;
  const now = new Date();
  switch (range) {
    case 'day':
      return new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
    case 'week':
      return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString();
    case 'month':
      return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString();
  }
}

function formatTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function applyClientSearch(items: ActivitySummary[], searchQuery: string): ActivitySummary[] {
  const normalizedQuery = searchQuery.trim().toLowerCase();
  if (!normalizedQuery) return items;

  return items.filter(item => (
    item.query.toLowerCase().includes(normalizedQuery)
    || item.feedback?.toLowerCase().includes(normalizedQuery)
    || item.email?.toLowerCase().includes(normalizedQuery)
  ));
}

function ActivityPageFallback() {
  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="h-6 w-24 animate-pulse rounded bg-muted" />
      <div className="mt-6 h-16 animate-pulse rounded bg-muted/50" />
      <div className="mt-6 h-8 max-w-xl animate-pulse rounded bg-muted/50" />
      <div className="mt-4 h-72 animate-pulse rounded-lg border border-border bg-muted/30" />
    </div>
  );
}

export default function AdminActivityPage() {
  return (
    <Suspense fallback={<ActivityPageFallback />}>
      <ActivityDashboard />
    </Suspense>
  );
}

function ActivityDashboard() {
  const {
    items,
    loading,
    lastFetched,
    hasMore,
    totalLoaded,
    loadFirstPage,
    loadNextPage,
    refresh,
  } = useActivityData();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rangeParam = searchParams.get('range');
  const feedbackParam = searchParams.get('feedback');
  const urlSearchQuery = searchParams.get('q') ?? '';
  const timeRange: TimeRange = isTimeRange(rangeParam) ? rangeParam : 'week';
  const feedbackFilter: FeedbackFilter = isFeedbackFilter(feedbackParam)
    ? feedbackParam
    : 'all';
  const [searchQuery, setSearchQuery] = useState(urlSearchQuery);
  const loadFirstPageRef = useRef(loadFirstPage);

  useEffect(() => {
    loadFirstPageRef.current = loadFirstPage;
  });

  const replaceSearchParams = useCallback((updates: Record<string, string | null>) => {
    const nextParams = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) nextParams.set(key, value);
      else nextParams.delete(key);
    }
    const queryString = nextParams.toString();
    router.replace(queryString ? `${pathname}?${queryString}` : pathname, { scroll: false });
  }, [pathname, router, searchParams]);

  useEffect(() => {
    setSearchQuery(urlSearchQuery);
  }, [urlSearchQuery]);

  useEffect(() => {
    if (searchQuery === urlSearchQuery) return;
    const timer = window.setTimeout(() => {
      replaceSearchParams({ q: searchQuery.trim() || null });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [replaceSearchParams, searchQuery, urlSearchQuery]);

  useEffect(() => {
    const filters: ActivityFilters = {
      after: getTimeRangeISO(timeRange),
      feedback: feedbackFilter,
      limit: PAGE_SIZE,
      cacheKey: `${timeRange}:${feedbackFilter}`,
    };
    loadFirstPageRef.current(filters);
  }, [feedbackFilter, timeRange]);

  const displayItems = useMemo(
    () => applyClientSearch(items, searchQuery),
    [items, searchQuery]
  );

  const stats = useMemo(() => {
    const thumbsUp = displayItems.filter(item => item.thumbUp === true).length;
    const thumbsDown = displayItems.filter(item => item.thumbUp === false).length;
    return {
      total: displayItems.length,
      rated: thumbsUp + thumbsDown,
      thumbsUp,
      thumbsDown,
    };
  }, [displayItems]);

  const clearFilters = () => {
    setSearchQuery('');
    replaceSearchParams({ range: null, feedback: null, q: null });
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Activity</h1>
          {lastFetched && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              Synced {formatRelativeTime(lastFetched)}
            </p>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={() => refresh()} disabled={loading}>
          <RefreshCw className={`mr-1.5 h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          Sync
        </Button>
      </div>

      <div className="mt-6 flex gap-6">
        <div>
          <div className="text-2xl font-semibold text-foreground">{stats.total}</div>
          <div className="text-xs text-muted-foreground">shown</div>
        </div>
        <Separator orientation="vertical" className="h-10" />
        <div>
          <div className="text-2xl font-semibold text-foreground">{stats.rated}</div>
          <div className="text-xs text-muted-foreground">rated</div>
        </div>
        <Separator orientation="vertical" className="h-10" />
        <div className="flex items-center gap-1.5">
          <ThumbsUp className="h-3.5 w-3.5 text-green-500" />
          <span className="text-2xl font-semibold text-foreground">{stats.thumbsUp}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <ThumbsDown className="h-3.5 w-3.5 text-red-500" />
          <span className="text-2xl font-semibold text-foreground">{stats.thumbsDown}</span>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] max-w-sm flex-1">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search loaded queries, comments, or email..."
            value={searchQuery}
            onChange={event => setSearchQuery(event.target.value)}
            className="h-8 pl-8 text-sm"
          />
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
              <Filter className="h-3 w-3" />
              {TIME_RANGE_LABELS[timeRange]}
              <ChevronDown className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {(Object.entries(TIME_RANGE_LABELS) as [TimeRange, string][]).map(([key, label]) => (
              <DropdownMenuItem
                key={key}
                onClick={() => replaceSearchParams({ range: key === 'week' ? null : key })}
              >
                {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
              <MessageSquare className="h-3 w-3" />
              {FEEDBACK_LABELS[feedbackFilter]}
              <ChevronDown className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {(Object.entries(FEEDBACK_LABELS) as [FeedbackFilter, string][]).map(([key, label]) => (
              <DropdownMenuItem
                key={key}
                onClick={() => replaceSearchParams({ feedback: key === 'all' ? null : key })}
              >
                {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {(searchQuery || timeRange !== 'week' || feedbackFilter !== 'all') && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 text-xs text-muted-foreground"
            onClick={clearFilters}
          >
            Clear filters
          </Button>
        )}
      </div>

      <div className="mt-4">
        {loading && items.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            Loading activity data...
          </div>
        ) : displayItems.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            <p>No loaded queries match the current filters.</p>
            {hasMore && (
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={loadNextPage}
                disabled={loading}
              >
                {loading ? 'Loading...' : 'Search next page'}
              </Button>
            )}
          </div>
        ) : (
          <>
            <Card>
              <CardContent className="p-0">
                <div className="divide-y divide-border">
                  {displayItems.map(item => (
                    <Link
                      key={item.queryId}
                      href={`/admin/activity/${item.queryId}${searchParams.toString() ? `?${searchParams.toString()}` : ''}`}
                      scroll={false}
                      aria-label={`Open details for: ${item.query}`}
                      className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/50 focus-visible:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                    >
                      <div className="shrink-0">
                        {item.thumbUp === true ? (
                          <ThumbsUp className="h-3.5 w-3.5 text-green-500" />
                        ) : item.thumbUp === false ? (
                          <ThumbsDown className="h-3.5 w-3.5 text-red-500" />
                        ) : (
                          <MessageSquare className="h-3.5 w-3.5 text-muted-foreground/50" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-foreground">{item.query}</p>
                        <div className="mt-0.5 flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">
                            {item.timestamp ? formatTimestamp(item.timestamp) : 'No timestamp'}
                          </span>
                          {item.email && (
                            <span className="truncate text-xs text-muted-foreground">
                              &middot; {item.email}
                            </span>
                          )}
                          {item.feedback && (
                            <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                              has comment
                            </Badge>
                          )}
                        </div>
                      </div>
                      <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
                    </Link>
                  ))}
                </div>
              </CardContent>
            </Card>

            <div className="mt-4 flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                Showing {displayItems.length} of {totalLoaded} loaded queries
              </span>
              {hasMore && (
                <Button variant="outline" size="sm" onClick={loadNextPage} disabled={loading}>
                  {loading ? 'Loading...' : 'Load more'}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
