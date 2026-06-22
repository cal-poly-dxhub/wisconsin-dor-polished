'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { useActivityData, type ActivityItem } from '@/hooks/use-activity-data';
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
  ChevronLeft,
  ChevronRight,
  Search,
  Filter,
  ChevronDown,
  ExternalLink,
} from 'lucide-react';

type TimeRange = 'day' | 'week' | 'month' | 'all';
type FeedbackFilter = 'all' | 'thumbs-up' | 'thumbs-down' | 'rated' | 'unrated';

const PAGE_SIZE = 20;

const TIME_RANGE_LABELS: Record<TimeRange, string> = {
  day: 'Last 24 hours',
  week: 'Last 7 days',
  month: 'Last 30 days',
  all: 'All time',
};

const FEEDBACK_LABELS: Record<FeedbackFilter, string> = {
  all: 'All feedback',
  'thumbs-up': 'Thumbs up only',
  'thumbs-down': 'Thumbs down only',
  rated: 'Has rating',
  unrated: 'No rating',
};

function getTimeRangeStart(range: TimeRange): Date {
  const now = new Date();
  switch (range) {
    case 'day':
      return new Date(now.getTime() - 24 * 60 * 60 * 1000);
    case 'week':
      return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    case 'month':
      return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    case 'all':
      return new Date(0);
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

function applyFilters(
  items: ActivityItem[],
  timeRange: TimeRange,
  feedbackFilter: FeedbackFilter,
  searchQuery: string
): ActivityItem[] {
  const rangeStart = getTimeRangeStart(timeRange);

  return items.filter(item => {
    if (item.timestamp && new Date(item.timestamp) < rangeStart) return false;

    switch (feedbackFilter) {
      case 'thumbs-up':
        if (item.thumbUp !== true) return false;
        break;
      case 'thumbs-down':
        if (item.thumbUp !== false) return false;
        break;
      case 'rated':
        if (item.thumbUp == null) return false;
        break;
      case 'unrated':
        if (item.thumbUp != null) return false;
        break;
    }

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const matchesQuery = item.query.toLowerCase().includes(q);
      const matchesAnswer = item.answer.toLowerCase().includes(q);
      const matchesFeedback = item.feedback?.toLowerCase().includes(q);
      if (!matchesQuery && !matchesAnswer && !matchesFeedback) return false;
    }

    return true;
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
  const { items, loading, lastFetched, fetchData } = useActivityData();
  const [timeRange, setTimeRange] = useState<TimeRange>('week');
  const [feedbackFilter, setFeedbackFilter] = useState<FeedbackFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(0);

  const filtered = useMemo(
    () => applyFilters(items, timeRange, feedbackFilter, searchQuery),
    [items, timeRange, feedbackFilter, searchQuery]
  );

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages - 1);
  const pageItems = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  const stats = useMemo(() => {
    const total = filtered.length;
    const thumbsUp = filtered.filter(i => i.thumbUp === true).length;
    const thumbsDown = filtered.filter(i => i.thumbUp === false).length;
    const rated = thumbsUp + thumbsDown;
    return { total, rated, thumbsUp, thumbsDown };
  }, [filtered]);

  const resetPage = () => setPage(0);

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
            onClick={() => fetchData(true)}
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
              onChange={e => { setSearchQuery(e.target.value); resetPage(); }}
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
                <DropdownMenuItem key={key} onClick={() => { setTimeRange(key); resetPage(); }}>
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
                <DropdownMenuItem key={key} onClick={() => { setFeedbackFilter(key); resetPage(); }}>
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
              onClick={() => { setSearchQuery(''); setTimeRange('week'); setFeedbackFilter('all'); resetPage(); }}
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
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">
              No queries match the current filters.
            </div>
          ) : (
            <>
              <Card>
                <CardContent className="p-0">
                  <div className="divide-y divide-border">
                    {pageItems.map(item => (
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

              {/* Pagination */}
              <div className="mt-4 flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {currentPage * PAGE_SIZE + 1}&ndash;{Math.min((currentPage + 1) * PAGE_SIZE, filtered.length)} of {filtered.length}
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 w-7 p-0"
                    disabled={currentPage === 0}
                    onClick={() => setPage(p => p - 1)}
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </Button>
                  <span className="px-2 text-xs text-muted-foreground">
                    {currentPage + 1} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 w-7 p-0"
                    disabled={currentPage >= totalPages - 1}
                    onClick={() => setPage(p => p + 1)}
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
