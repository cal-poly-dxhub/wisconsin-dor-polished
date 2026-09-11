'use client';

import { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import '@/components/messages/chat-message.css';
import { type ActivityItem, type RichFeedback, type TraceEvent } from '@/hooks/use-activity-data';
import { SUBSECTIONS } from '@/stores/feedback-store';
import { CanvasView } from '@/app/admin/canvas/canvas-view';
import { toCanvasEvents } from '@/app/admin/canvas/hooks/use-persisted-trace';
import { http } from '@/lib/http';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import {
  ThumbsUp,
  ThumbsDown,
  Clock,
  Hash,
  MessageSquare,
  Eye,
  Code,
  Search,
  FileText,
  Network,
  BookText,
  Sparkles,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Zap,
  Clock3,
  ChevronRight,
  ListTree,
  LayoutGrid,
  Check,
  X,
  Quote,
  Link2,
} from 'lucide-react';

// The middle ("mixed") rating collapses to thumbUp=false on the way in, so the
// scalar can't distinguish it from a true thumbs-down. Prefer the structured
// richFeedback.rating when present; fall back to the legacy boolean.
type EffectiveRating = 'up' | 'mid' | 'down' | null;

function effectiveRating(item: ActivityItem): EffectiveRating {
  const rating = item.richFeedback?.rating ?? item.rating;
  if (rating === 'up' || rating === 'mid' || rating === 'down') return rating;
  if (item.thumbUp === true) return 'up';
  if (item.thumbUp === false) return 'down';
  return null;
}

const SUBSECTION_LABELS: Record<string, string> = Object.fromEntries(
  SUBSECTIONS.map(({ key, label }) => [key, label])
);

function formatFullTimestamp(ts: string): string {
  return new Date(ts).toLocaleString('en-US', {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  });
}

export function ActivityDetail({
  queryId,
  layout = 'stacked',
}: {
  queryId: string;
  // 'stacked' = single column (drawer); 'split' = response left / feedback
  // right (expanded full-width view). Toggling this does NOT refetch — the same
  // component instance stays mounted, so only the arrangement changes.
  layout?: 'stacked' | 'split';
}) {
  const [item, setItem] = useState<ActivityItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  // Rendered markdown preview is the default; the toggle drops to raw text.
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    async function fetchItem() {
      setLoading(true);
      setError(false);
      setItem(null);

      try {
        const response = await http.get(`admin/activity/${queryId}`, {
          signal: controller.signal,
        }).json<{
          statusCode?: number;
          body?: string;
          item?: ActivityItem;
        }>();

        const data = response.statusCode && response.body
          ? JSON.parse(response.body) as { item?: ActivityItem }
          : response;

        if (!controller.signal.aborted) {
          setItem(data.item ?? null);
          setError(!data.item);
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          console.error('Failed to fetch activity item:', err);
          setError(true);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    fetchItem();
    return () => controller.abort();
  }, [queryId]);

  if (loading) {
    return (
      <div className="space-y-4" aria-label="Loading query details">
        <div className="h-5 w-24 animate-pulse rounded bg-muted" />
        <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-28 animate-pulse rounded-lg border border-border bg-muted/50" />
        <div className="h-64 animate-pulse rounded-lg border border-border bg-muted/50" />
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="py-16 text-center text-sm text-muted-foreground">
        Query details could not be loaded.
      </div>
    );
  }

  const ratingBadge = <RatingBadge rating={effectiveRating(item)} />;

  const metaBar = (
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
      {item.timestamp && (
        <span className="flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {formatFullTimestamp(item.timestamp)}
        </span>
      )}
      {item.email && <span className="flex items-center gap-1">{item.email}</span>}
      <span className="flex min-w-0 items-center gap-1">
        <Hash className="h-3 w-3 shrink-0" />
        <span className="truncate">{item.queryId}</span>
      </span>
    </div>
  );

  const questionCard = (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-muted-foreground">Question</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm leading-relaxed text-foreground">{item.query}</p>
      </CardContent>
    </Card>
  );

  const responseCard = (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="text-sm font-medium text-muted-foreground">Response</CardTitle>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 text-xs text-muted-foreground"
          onClick={() => setShowRaw(previous => !previous)}
        >
          {showRaw ? <Eye className="h-3 w-3" /> : <Code className="h-3 w-3" />}
          {showRaw ? 'Preview' : 'Raw markdown'}
        </Button>
      </CardHeader>
      <CardContent>
        {showRaw ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
            {item.answer}
          </p>
        ) : (
          <div className="markdown-container text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.answer}</ReactMarkdown>
          </div>
        )}
      </CardContent>
    </Card>
  );

  const legacyCommentCard = item.feedback ? (
    <Card className="border-amber-500/20 bg-amber-500/5">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-amber-600 dark:text-amber-400">
          User Comment
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm italic leading-relaxed text-foreground">
          &ldquo;{item.feedback}&rdquo;
        </p>
      </CardContent>
    </Card>
  ) : null;

  const richCard = item.richFeedback ? (
    <RichFeedbackDisplay
      feedback={item.richFeedback}
      submittedAt={item.feedbackSubmittedAt ?? null}
    />
  ) : null;

  const traceCard =
    item.trace && item.trace.length > 0 ? <RetrievalTrace trace={item.trace} /> : null;

  const idsFooter = (
    <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
      <div className="grid gap-4 text-xs sm:grid-cols-2">
        <div className="min-w-0">
          <span className="text-muted-foreground">Session ID</span>
          <p className="mt-0.5 break-all font-mono text-foreground">{item.sessionId}</p>
        </div>
        <div className="min-w-0">
          <span className="text-muted-foreground">Query ID</span>
          <p className="mt-0.5 break-all font-mono text-foreground">{item.queryId}</p>
        </div>
      </div>
    </div>
  );

  if (layout === 'split') {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 md:items-start">
          {/* Left half — what the assistant produced */}
          <div className="min-w-0 space-y-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Response
            </h3>
            {questionCard}
            {responseCard}
          </div>

          {/* Right half — how the user rated it */}
          <div className="min-w-0 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Feedback
              </h3>
              {ratingBadge}
            </div>
            {metaBar}
            {legacyCommentCard}
            {richCard}
            {idsFooter}
          </div>
        </div>

        {/* Full-width row — the retrieval trace gets the whole width beneath the
            response/feedback columns so the timeline has room to breathe. */}
        {traceCard && <div className="min-w-0">{traceCard}</div>}
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start justify-between gap-4">{ratingBadge}</div>
      <div className="mt-4">{metaBar}</div>
      <Separator className="my-6" />
      <div className="space-y-4">
        {questionCard}
        {responseCard}
        {legacyCommentCard}
        {richCard}
        {traceCard}
      </div>
      <div className="mt-6">{idsFooter}</div>
    </div>
  );
}

// Humanized tool labels, mirroring the canvas's TOOL_TITLES.
const TOOL_LABELS: Record<string, string> = {
  faq_search: 'FAQ Search',
  refine_query: 'Query Refinement',
  vector_search: 'Vector Search',
  search_document: 'Document Search',
  list_sections: 'List Sections',
  get_section: 'Get Section',
  get_document: 'Get Document',
  get_neighbors: 'Graph Neighbors',
  get_authority_chain: 'Authority Chain',
  list_framework_docs: 'Framework Documents',
  find_case_law: 'Case Law Search',
  fetch_case_opinion: 'Fetch Case Opinion',
  prepare_answer: 'Answer Synthesis',
  answer: 'Answer Synthesis',
  cite_documents: 'Cite Documents',
  clarify: 'Clarify',
};

function toolLabel(toolName?: string): string {
  if (!toolName) return 'Step';
  return TOOL_LABELS[toolName] ?? toolName;
}

function getToolIcon(toolName?: string) {
  switch (toolName) {
    case 'faq_search':
    case 'find_case_law':
      return <Search className="h-4 w-4" />;
    case 'vector_search':
    case 'search_document':
      return <FileText className="h-4 w-4" />;
    case 'get_neighbors':
    case 'get_authority_chain':
      return <Network className="h-4 w-4" />;
    case 'get_section':
    case 'list_sections':
    case 'get_document':
    case 'list_framework_docs':
    case 'fetch_case_opinion':
      return <BookText className="h-4 w-4" />;
    case 'prepare_answer':
    case 'answer':
    case 'cite_documents':
      return <Sparkles className="h-4 w-4" />;
    default:
      return <Zap className="h-4 w-4" />;
  }
}

// Status → dot color + node accent. `ok`/`terminal` read as success.
function statusStyle(status?: string): { ring: string; icon: React.ReactNode | null } {
  switch (status) {
    case 'ok':
    case 'terminal':
      return {
        ring: 'border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400',
        icon: <CheckCircle2 className="h-3 w-3" />,
      };
    case 'error':
      return {
        ring: 'border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400',
        icon: <XCircle className="h-3 w-3" />,
      };
    case 'miss':
      return {
        ring: 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400',
        icon: <AlertCircle className="h-3 w-3" />,
      };
    default:
      return { ring: 'border-border bg-muted text-muted-foreground', icon: null };
  }
}

// Turn a metadata key into a readable label ("preDedupCount" → "Pre-dedup").
function humanizeKey(key: string): string {
  const withSpaces = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .replace(/\bMs\b/i, '')
    .replace(/\bId\b/i, 'ID')
    .trim();
  const lower = withSpaces.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

// Render a metadata value as a short, human string. Objects/arrays are
// summarized (count + example) instead of dumping "[object Object]".
function formatMetaValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    if (value.length === 0) return 'none';
    const allPrimitive = value.every(v => typeof v !== 'object' || v === null);
    if (allPrimitive) {
      const head = value.slice(0, 3).map(v => (typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v)));
      return value.length > 3 ? `${head.join(', ')} +${value.length - 3}` : head.join(', ');
    }
    return `${value.length} item${value.length === 1 ? '' : 's'}`;
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>);
    return keys.length === 0 ? '—' : `${keys.length} field${keys.length === 1 ? '' : 's'}`;
  }
  return String(value);
}

// Metadata keys that are noisy / already surfaced elsewhere (latency shown on
// the header row; doc IDs shown as title badges). Everything else renders as a
// clean stat chip. Ordered so the most useful stats come first.
const META_HIDE = new Set(['latencyMs', 'chunkIds', 'docChunks', 'seeded']);
const META_ORDER = [
  'chunkCount',
  'preDedupCount',
  'totalChunkCount',
  'broadChunkCount',
  'docCount',
  'documentCount',
  'topScore',
  'faqCount',
  'caseLawCount',
  'neighborCount',
  'sectionCount',
  'chainLength',
];

function orderedMeta(metadata: Record<string, unknown>): [string, unknown][] {
  const entries = Object.entries(metadata).filter(([k]) => !META_HIDE.has(k));
  return entries.sort(([a], [b]) => {
    const ia = META_ORDER.indexOf(a);
    const ib = META_ORDER.indexOf(b);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.localeCompare(b);
  });
}

function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col rounded-md border border-border bg-muted/40 px-2.5 py-1.5">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">{label}</span>
      <span className="mt-0.5 truncate text-xs font-medium tabular-nums text-foreground" title={value}>
        {value}
      </span>
    </div>
  );
}

function TimelineStep({ event, isLast }: { event: TraceEvent; isLast: boolean }) {
  const style = statusStyle(event.status);
  const meta = event.metadata ? orderedMeta(event.metadata) : [];

  return (
    <div className="flex gap-3">
      {/* Rail: icon node + connector */}
      <div className="flex flex-col items-center">
        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border ${style.ring}`}>
          {getToolIcon(event.toolName)}
        </div>
        {!isLast && <div className="mt-1 w-px flex-1 bg-border" />}
      </div>

      {/* Body */}
      <div className="min-w-0 flex-1 pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{toolLabel(event.toolName)}</span>
          {event.turn != null && (
            <Badge variant="outline" className="px-1.5 py-0 text-[10px] font-normal">
              Turn {event.turn}
            </Badge>
          )}
          {style.icon && (
            <span className={`flex items-center gap-1 text-[11px] ${style.ring.split(' ').find(c => c.startsWith('text-')) ?? ''}`}>
              {style.icon}
              {event.status}
            </span>
          )}
          {event.latencyMs != null && (
            <span className="ml-auto flex items-center gap-1 text-[11px] tabular-nums text-muted-foreground">
              <Clock3 className="h-3 w-3" />
              {event.latencyMs.toLocaleString()}ms
            </span>
          )}
        </div>

        {event.summary && (
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{event.summary}</p>
        )}

        {event.docTitles && event.docTitles.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {event.docTitles.slice(0, 6).map((title, i) => (
              <Badge key={`${title}-${i}`} variant="secondary" className="max-w-[220px] truncate px-2 py-0.5 text-[10px] font-normal">
                {title}
              </Badge>
            ))}
            {event.docTitles.length > 6 && (
              <span className="self-center text-[10px] text-muted-foreground">
                +{event.docTitles.length - 6} more
              </span>
            )}
          </div>
        )}

        {meta.length > 0 && (
          <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:grid-cols-4">
            {meta.map(([key, value]) => (
              <StatChip key={key} label={humanizeKey(key)} value={formatMetaValue(value)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TimelineView({ trace }: { trace: TraceEvent[] }) {
  const loopComplete = trace.find(event => event.kind === 'loop_complete');
  const toolResults = trace.filter(event => event.kind === 'tool_result');

  return (
    <div>
      <div className="space-y-0">
        {toolResults.map((event, index) => (
          <TimelineStep
            key={`${event.kind}-${event.turn}-${event.ts}-${index}`}
            event={event}
            isLast={index === toolResults.length - 1}
          />
        ))}
      </div>

      {loopComplete?.discovery && Object.keys(loopComplete.discovery).length > 0 && (
        <>
          <Separator className="my-4" />
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Discovery Sources
            </span>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(loopComplete.discovery).map(([source, count]) => (
                <Badge key={source} variant="outline" className="gap-1 text-[11px] font-normal">
                  {source}
                  <span className="font-semibold tabular-nums text-foreground">{count}</span>
                </Badge>
              ))}
            </div>
          </div>
        </>
      )}

      {loopComplete?.citedDocIds && loopComplete.citedDocIds.length > 0 && (
        <>
          <Separator className="my-4" />
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Cited Documents
            </span>
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {loopComplete.citedDocIds.map(docId => (
                <p key={docId} className="break-all rounded border border-border bg-muted/40 px-2 py-1 font-mono text-[11px] text-foreground">
                  {docId}
                </p>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ViewToggle({
  view,
  onChange,
}: {
  view: 'timeline' | 'canvas';
  onChange: (v: 'timeline' | 'canvas') => void;
}) {
  const base =
    'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors';
  return (
    <div className="flex items-center gap-0.5 rounded-lg border border-border bg-muted/50 p-0.5">
      <button
        type="button"
        onClick={() => onChange('timeline')}
        className={`${base} ${view === 'timeline' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
      >
        <ListTree className="h-3.5 w-3.5" />
        Timeline
      </button>
      <button
        type="button"
        onClick={() => onChange('canvas')}
        className={`${base} ${view === 'canvas' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
      >
        <LayoutGrid className="h-3.5 w-3.5" />
        Canvas
      </button>
    </div>
  );
}

function RetrievalTrace({ trace }: { trace: TraceEvent[] }) {
  // Collapsed by default — the trace is long and secondary to the feedback.
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<'timeline' | 'canvas'>('timeline');
  const loopComplete = trace.find(event => event.kind === 'loop_complete');

  // Adapt the flat persisted trace to the canvas event stream once, lazily —
  // only needed when the canvas view is shown.
  const canvasEvents = useMemo(
    () => (view === 'canvas' ? toCanvasEvents(trace) : []),
    [view, trace]
  );

  return (
    // Not a card — a full-width section set off by a top rule, so the trace
    // reads as a continuation of the detail rather than a boxed panel.
    <section className="mt-6 border-t border-border pt-5">
      <div className="flex w-full items-center justify-between gap-4">
        <button
          type="button"
          onClick={() => setOpen(previous => !previous)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-4 text-left"
        >
          <span className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <ChevronRight
              className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
            />
            Retrieval Trace
          </span>
          {loopComplete && (
            <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              {loopComplete.turnsUsed != null && (
                <Badge variant="secondary" className="px-1.5 py-0 text-[10px] font-normal">
                  {loopComplete.turnsUsed} turn{loopComplete.turnsUsed === 1 ? '' : 's'}
                </Badge>
              )}
              {loopComplete.elapsedMs != null && (
                <Badge variant="secondary" className="px-1.5 py-0 text-[10px] font-normal tabular-nums">
                  {(loopComplete.elapsedMs / 1000).toFixed(1)}s
                </Badge>
              )}
              {loopComplete.citedDocCount != null && (
                <Badge variant="secondary" className="px-1.5 py-0 text-[10px] font-normal">
                  {loopComplete.citedDocCount} cited
                </Badge>
              )}
            </div>
          )}
        </button>
        {open && <ViewToggle view={view} onChange={setView} />}
      </div>
      {open && (
        <div className="mt-5">
          {view === 'timeline' ? (
            <TimelineView trace={trace} />
          ) : (
            // The canvas panes carry their own light-mode palette; wrap so they
            // render on a neutral surface regardless of admin theme, and pull
            // back the CanvasView's outer horizontal padding.
            <div className="-mx-2 overflow-hidden rounded-lg border border-border bg-white py-2 text-neutral-900">
              <CanvasView events={canvasEvents} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function RatingBadge({ rating }: { rating: EffectiveRating }) {
  if (rating === 'up') {
    return (
      <Badge className="gap-1 border-green-500/20 bg-green-500/10 text-green-600 hover:bg-green-500/10">
        <ThumbsUp className="h-3 w-3" />
        Positive
      </Badge>
    );
  }
  if (rating === 'mid') {
    return (
      <Badge className="gap-1 border-amber-500/20 bg-amber-500/10 text-amber-600 hover:bg-amber-500/10 dark:text-amber-400">
        {/* No dedicated "sideways thumb" glyph — reuse the modal's rotated ThumbsUp. */}
        <ThumbsUp className="h-3 w-3 -rotate-90" />
        Mixed
      </Badge>
    );
  }
  if (rating === 'down') {
    return (
      <Badge className="gap-1 border-red-500/20 bg-red-500/10 text-red-600 hover:bg-red-500/10">
        <ThumbsDown className="h-3 w-3" />
        Negative
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="gap-1">
      <MessageSquare className="h-3 w-3" />
      Unrated
    </Badge>
  );
}

// Yes / No / (not answered) — always renders something so an unanswered field
// reads as a deliberate blank rather than a missing row.
function YesNoValue({ value }: { value?: string | null }) {
  if (value === 'yes') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 text-xs font-medium text-green-600 dark:text-green-400">
        <Check className="h-3 w-3" />
        Yes
      </span>
    );
  }
  if (value === 'no') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-600 dark:text-red-400">
        <X className="h-3 w-3" />
        No
      </span>
    );
  }
  return <NotProvided />;
}

function NotProvided({ label = 'Not answered' }: { label?: string }) {
  return <span className="text-xs italic text-muted-foreground/60">{label}</span>;
}

const isFilled = (value?: string | null) => (value ?? '').trim() !== '';

// A comment/free-text value: shows the quote if present, else a muted blank.
// User-entered text is the payload of this panel, so it renders larger and at
// full foreground contrast — the muted uppercase labels above it carry the
// hierarchy, not size.
function CommentValue({ value, empty = 'No comment' }: { value?: string | null; empty?: string }) {
  if (isFilled(value)) {
    return (
      <blockquote className="border-l-2 border-primary/30 pl-3 text-[15px] leading-relaxed text-foreground">
        &ldquo;{value}&rdquo;
      </blockquote>
    );
  }
  return <NotProvided label={empty} />;
}

function FeedbackRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {children}
    </div>
  );
}

// Renders the full feedback schema — every field, filled or not — so an admin
// gets a complete picture of what the form captured for this query.
function RichFeedbackDisplay({
  feedback,
  submittedAt,
}: {
  feedback: RichFeedback;
  submittedAt: string | null;
}) {
  const sourceNotes = feedback.sourceNotes ?? [];
  const annotations = feedback.annotations ?? [];
  const brokenLinkIds = feedback.brokenLinkIds ?? [];
  const response = feedback.response ?? {};

  // Always show all defined subsection questions, plus any extra keys the row
  // happens to carry that aren't in the current SUBSECTIONS list.
  const responseKeys = [
    ...SUBSECTIONS.map(s => s.key),
    ...Object.keys(response).filter(k => !SUBSECTION_LABELS[k]),
  ];

  return (
    <Card className="mt-4 border-primary/20 bg-primary/5">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="text-sm font-medium text-muted-foreground">User Feedback</CardTitle>
        {submittedAt && (
          <span className="text-xs text-muted-foreground">{formatFullTimestamp(submittedAt)}</span>
        )}
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <FeedbackRow label="Overall rating">
          {feedback.rating ? (
            <RatingBadge rating={feedback.rating} />
          ) : (
            <NotProvided label="No rating" />
          )}
        </FeedbackRow>

        <FeedbackRow label="What worked (positive comment)">
          <CommentValue value={feedback.positiveComment} />
        </FeedbackRow>

        <FeedbackRow label="Response">
          <ul className="space-y-2">
            {responseKeys.map(key => {
              const v = response[key];
              return (
                <li key={key}>
                  <div className="flex items-center gap-2">
                    <YesNoValue value={v?.answer} />
                    <span className="text-foreground">{SUBSECTION_LABELS[key] ?? key}</span>
                  </div>
                  <div className="mt-0.5 pl-0.5">
                    <CommentValue value={v?.comment} />
                  </div>
                </li>
              );
            })}
          </ul>
        </FeedbackRow>

        <FeedbackRow label="Sources accurate, current & relevant">
          <YesNoValue value={feedback.sourcesOk} />
          <div className="mt-2">
            {sourceNotes.length > 0 ? (
              <ul className="space-y-2">
                {sourceNotes.map(note => (
                  <li
                    key={note.id}
                    className="rounded-md border border-border bg-background/50 px-2.5 py-2"
                  >
                    <p className="break-all font-mono text-xs text-foreground">
                      {isFilled(note.sourceId) ? note.sourceId : <NotProvided label="(no source id)" />}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Fully cited:{' '}
                      {isFilled(note.citedFully) ? (
                        <span className="text-foreground">{note.citedFully}</span>
                      ) : (
                        <NotProvided />
                      )}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Missed detail:{' '}
                      {isFilled(note.missedDetail) ? (
                        <span className="text-foreground">{note.missedDetail}</span>
                      ) : (
                        <NotProvided label="none" />
                      )}
                    </p>
                    <div className="mt-0.5">
                      <CommentValue value={note.comment} />
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <NotProvided label="No per-source notes" />
            )}
          </div>
        </FeedbackRow>

        <FeedbackRow label="Links worked">
          <YesNoValue value={feedback.linksWork} />
          <div className="mt-1.5">
            <p className="text-xs text-muted-foreground">Broken links:</p>
            {brokenLinkIds.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-1">
                {brokenLinkIds.map(id => (
                  <Badge key={id} variant="outline" className="gap-1 text-[10px] font-normal">
                    <Link2 className="h-3 w-3" />
                    {id}
                  </Badge>
                ))}
              </div>
            ) : (
              <NotProvided label="none flagged" />
            )}
          </div>
          <div className="mt-1.5">
            <p className="text-xs text-muted-foreground">Reason:</p>
            <CommentValue value={feedback.brokenLinksReason} empty="No reason given" />
          </div>
        </FeedbackRow>

        <FeedbackRow label="Highlighted passages">
          {annotations.length > 0 ? (
            <ul className="space-y-2">
              {annotations.map(a => (
                <li
                  key={a.id}
                  className="rounded-md border border-border bg-background/50 px-2.5 py-2"
                >
                  <p className="flex gap-1.5 text-[15px] leading-relaxed text-foreground">
                    <Quote className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    {isFilled(a.quote) ? (
                      <span className="italic">{a.quote}</span>
                    ) : (
                      <NotProvided label="(no quote)" />
                    )}
                  </p>
                  <div className="mt-1">
                    <CommentValue value={a.comment} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <NotProvided label="No highlights" />
          )}
        </FeedbackRow>

        <FeedbackRow label="Timely">
          <YesNoValue value={feedback.speedTimely} />
          <div className="mt-1.5">
            <CommentValue value={feedback.speedComment} />
          </div>
        </FeedbackRow>
      </CardContent>
    </Card>
  );
}
