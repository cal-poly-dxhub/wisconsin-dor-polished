'use client';

import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import '@/components/messages/chat-message.css';
import { type ActivityItem, type RichFeedback, type TraceEvent } from '@/hooks/use-activity-data';
import { SUBSECTIONS } from '@/stores/feedback-store';
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
  GitBranch,
  CheckCircle,
  XCircle,
  Zap,
  ChevronRight,
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
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 md:items-start">
        {/* Left half — what the assistant produced */}
        <div className="min-w-0 space-y-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Response
          </h3>
          {questionCard}
          {responseCard}
          {traceCard}
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

function getToolIcon(toolName: string) {
  switch (toolName) {
    case 'faq_search':
    case 'vector_search':
    case 'search_document':
      return <Search className="h-3 w-3" />;
    case 'get_neighbors':
    case 'get_authority_chain':
      return <GitBranch className="h-3 w-3" />;
    default:
      return <Zap className="h-3 w-3" />;
  }
}

function getStatusColor(status?: string) {
  switch (status) {
    case 'ok':
      return 'text-green-600 dark:text-green-400';
    case 'error':
      return 'text-red-600 dark:text-red-400';
    case 'miss':
      return 'text-amber-600 dark:text-amber-400';
    case 'terminal':
      return 'text-blue-600 dark:text-blue-400';
    default:
      return 'text-muted-foreground';
  }
}

function RetrievalTrace({ trace }: { trace: TraceEvent[] }) {
  // Collapsed by default — the trace is long and secondary to the feedback.
  const [open, setOpen] = useState(false);
  const loopComplete = trace.find(event => event.kind === 'loop_complete');
  const toolResults = trace.filter(event => event.kind === 'tool_result');

  return (
    <Card className="mt-4">
      <CardHeader className="pb-3">
        <button
          type="button"
          onClick={() => setOpen(previous => !previous)}
          aria-expanded={open}
          className="flex w-full items-center justify-between gap-4 text-left"
        >
          <CardTitle className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
            <ChevronRight
              className={`h-3.5 w-3.5 shrink-0 transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
            />
            Retrieval Trace
          </CardTitle>
          {loopComplete && (
            <div className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
              {loopComplete.turnsUsed != null && (
                <span>{loopComplete.turnsUsed} turn{loopComplete.turnsUsed === 1 ? '' : 's'}</span>
              )}
              {loopComplete.elapsedMs != null && (
                <span>{(loopComplete.elapsedMs / 1000).toFixed(1)}s</span>
              )}
              {loopComplete.citedDocCount != null && (
                <span>{loopComplete.citedDocCount} cited</span>
              )}
            </div>
          )}
        </button>
      </CardHeader>
      {open && (
      <CardContent>
        <div className="space-y-2">
          {toolResults.map((event, index) => (
            <div key={`${event.kind}-${event.turn}-${event.ts}-${index}`} className="flex items-start gap-2.5">
              <div className="mt-1.5 flex flex-col items-center self-stretch">
                <div className={`rounded-full p-0.5 ${getStatusColor(event.status)}`}>
                  {event.status === 'ok' || event.status === 'terminal' ? (
                    <CheckCircle className="h-3 w-3" />
                  ) : event.status === 'error' ? (
                    <XCircle className="h-3 w-3" />
                  ) : (
                    getToolIcon(event.toolName || '')
                  )}
                </div>
                {index < toolResults.length - 1 && <div className="mt-1 w-px flex-1 bg-border" />}
              </div>

              <div className="min-w-0 flex-1 pb-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-foreground">
                    {event.toolName || event.kind}
                  </span>
                  {event.turn != null && (
                    <span className="text-[10px] text-muted-foreground">T{event.turn}</span>
                  )}
                  {event.latencyMs != null && (
                    <span className="text-[10px] text-muted-foreground">{event.latencyMs}ms</span>
                  )}
                </div>
                {event.summary && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{event.summary}</p>
                )}
                {event.docTitles && event.docTitles.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {event.docTitles.slice(0, 5).map((title, titleIndex) => (
                      <Badge
                        key={`${title}-${titleIndex}`}
                        variant="secondary"
                        className="px-1.5 py-0 text-[10px] font-normal"
                      >
                        {title.length > 40 ? `${title.slice(0, 40)}...` : title}
                      </Badge>
                    ))}
                    {event.docTitles.length > 5 && (
                      <span className="text-[10px] text-muted-foreground">
                        +{event.docTitles.length - 5} more
                      </span>
                    )}
                  </div>
                )}
                {event.metadata && Object.keys(event.metadata).length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
                    {Object.entries(event.metadata).map(([key, value]) => (
                      <span key={key}>
                        {key}: <span className="text-foreground">{String(value)}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {loopComplete?.discovery && Object.keys(loopComplete.discovery).length > 0 && (
          <>
            <Separator className="my-3" />
            <div>
              <span className="text-xs font-medium text-muted-foreground">Discovery Sources</span>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {Object.entries(loopComplete.discovery).map(([source, count]) => (
                  <Badge key={source} variant="outline" className="gap-1 text-[10px] font-normal">
                    {source}: {String(count)}
                  </Badge>
                ))}
              </div>
            </div>
          </>
        )}

        {loopComplete?.citedDocIds && loopComplete.citedDocIds.length > 0 && (
          <>
            <Separator className="my-3" />
            <div>
              <span className="text-xs font-medium text-muted-foreground">Cited Documents</span>
              <div className="mt-1.5 space-y-0.5">
                {loopComplete.citedDocIds.map(docId => (
                  <p key={docId} className="break-all font-mono text-xs text-foreground">{docId}</p>
                ))}
              </div>
            </div>
          </>
        )}
      </CardContent>
      )}
    </Card>
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
