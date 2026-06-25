'use client';

import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';
import { ProtectedRoute } from '@/components/auth/protected-route';
import {
  useActivityData,
  type ActivityItem,
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

function formatTimestamp(ts: string): string {
  return new Date(ts).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function applyClientSearch(items: ActivityItem[], searchQuery: string): ActivityItem[] {
  if (!searchQuery) return items;
  const q = searchQuery.toLowerCase();
  return items.filter(item => {
    const matchesQuery = item.query.toLowerCase().includes(q);
    const matchesAnswer = item.answer.toLowerCase().includes(q);
    const matchesFeedback = item.feedback?.toLowerCase().includes(q);
    const matchesEmail = item.email?.toLowerCase().includes(q);
    return matchesQuery || matchesAnswer || matchesFeedback || matchesEmail;
  });
}

export default function AdminActivityPage() {
  return (
    <ProtectedRoute>
      <ActivityDashboard />
    </ProtectedRoute>
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

  const [timeRange, setTimeRange] = useState<TimeRange>('week');
  const [feedbackFilter, setFeedbackFilter] = useState<FeedbackFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const buildFilters = (range: TimeRange, feedback: FeedbackFilter): ActivityFilters => ({
    after: getTimeRangeISO(range),
    feedback,
    limit: PAGE_SIZE,
  });

  useEffect(() => {
    loadFirstPage(buildFilters(timeRange, feedbackFilter));
  }, [timeRange, feedbackFilter, loadFirstPage]);

  const displayItems = useMemo(
    () => applyClientSearch(items, searchQuery),
    [items, searchQuery]
  );

  const stats = useMemo(() => {
    const total = displayItems.length;
    const thumbsUp = displayItems.filter(i => i.thumbUp === true).length;
    const thumbsDown = displayItems.filter(i => i.thumbUp === false).length;
    const rated = thumbsUp + thumbsDown;
    return { total, rated, thumbsUp, thumbsDown };
  }, [displayItems]);

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-5xl px-6 py-8">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Activity</h1>
            {lastFetched && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                Synced {formatRelativeTime(lastFetched)}
              </p>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => refresh()}
            disabled={loading}
          >
            <RefreshCw className={`mr-1.5 h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
            Sync
          </Button>
        </div>

        {/* Stats row */}
        <div className="mt-6 flex gap-6">
          <div>
            <div className="text-2xl font-semibold text-foreground">{stats.total}</div>
            <div className="text-xs text-muted-foreground">queries</div>
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

        {/* Filters bar */}
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search queries, answers, or feedback..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="pl-8 h-8 text-sm"
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
                <DropdownMenuItem key={key} onClick={() => setTimeRange(key)}>
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
                <DropdownMenuItem key={key} onClick={() => setFeedbackFilter(key)}>
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
              onClick={() => { setSearchQuery(''); setTimeRange('week'); setFeedbackFilter('all'); }}
            >
              Clear filters
            </Button>
          )}
        </div>

        {/* Results */}
        <div className="mt-4">
          {loading && items.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">
              Loading activity data...
            </div>
          ) : displayItems.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">
              No queries match the current filters.
            </div>
          ) : (
            <>
              <Card>
                <CardContent className="p-0">
                  <div className="divide-y divide-border">
                    {displayItems.map(item => (
                      <Link
                        key={item.queryId}
                        href={`/admin/activity/${item.queryId}`}
                        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/50"
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
                              <span className="text-xs text-muted-foreground">
                                &middot; {item.email}
                              </span>
                            )}
                            {item.feedback && (
                              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                                has comment
                              </Badge>
                            )}
                          </div>
                        </div>
                        <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                      </Link>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Load more */}
              <div className="mt-4 flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  Showing {displayItems.length} queries
                  {totalLoaded !== displayItems.length && ` (${totalLoaded} loaded)`}
                </span>
                {hasMore && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadNextPage}
                    disabled={loading}
                  >
                    {loading ? 'Loading...' : 'Load more'}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
