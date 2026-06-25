'use client';

import { use, useState } from 'react';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import '@/components/messages/chat-message.css';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { useActivityData, type TraceEvent } from '@/hooks/use-activity-data';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import {
  ArrowLeft,
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
} from 'lucide-react';

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

export default function QueryDetailPage({
  params,
}: {
  params: Promise<{ queryId: string }>;
}) {
  const { queryId } = use(params);

  return (
    <ProtectedRoute>
      <QueryDetail queryId={queryId} />
    </ProtectedRoute>
  );
}

function QueryDetail({ queryId }: { queryId: string }) {
  const { items, loading, getItemById } = useActivityData();
  const item = getItemById(queryId);
  const [showMarkdown, setShowMarkdown] = useState(false);

  if (loading && items.length === 0) {
    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-3xl px-6 py-8">
          <div className="py-16 text-center text-sm text-muted-foreground">
            Loading...
          </div>
        </div>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-3xl px-6 py-8">
          <Link href="/admin/activity">
            <Button variant="ghost" size="sm" className="gap-1.5 text-xs text-muted-foreground">
              <ArrowLeft className="h-3 w-3" />
              Back to activity
            </Button>
          </Link>
          <div className="py-16 text-center text-sm text-muted-foreground">
            Query not found.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-6 py-8">
        {/* Back nav */}
        <Link href="/admin/activity">
          <Button variant="ghost" size="sm" className="gap-1.5 text-xs text-muted-foreground mb-6">
            <ArrowLeft className="h-3 w-3" />
            Back to activity
          </Button>
        </Link>

        {/* Header meta */}
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              {item.thumbUp === true ? (
                <Badge className="gap-1 bg-green-500/10 text-green-600 border-green-500/20 hover:bg-green-500/10">
                  <ThumbsUp className="h-3 w-3" />
                  Positive
                </Badge>
              ) : item.thumbUp === false ? (
                <Badge className="gap-1 bg-red-500/10 text-red-600 border-red-500/20 hover:bg-red-500/10">
                  <ThumbsDown className="h-3 w-3" />
                  Negative
                </Badge>
              ) : (
                <Badge variant="secondary" className="gap-1">
                  <MessageSquare className="h-3 w-3" />
                  Unrated
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Metadata */}
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
          {item.timestamp && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatFullTimestamp(item.timestamp)}
            </span>
          )}
          {item.email && (
            <span className="flex items-center gap-1">
              {item.email}
            </span>
          )}
          <span className="flex items-center gap-1">
            <Hash className="h-3 w-3" />
            {item.queryId}
          </span>
        </div>

        <Separator className="my-6" />

        {/* Question */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Question
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed text-foreground">{item.query}</p>
          </CardContent>
        </Card>

        {/* Answer */}
        <Card className="mt-4">
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Response
            </CardTitle>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 text-xs text-muted-foreground"
              onClick={() => setShowMarkdown(prev => !prev)}
            >
              {showMarkdown ? <Code className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
              {showMarkdown ? 'Plain text' : 'Preview'}
            </Button>
          </CardHeader>
          <CardContent>
            {showMarkdown ? (
              <div className="markdown-container text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {item.answer}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm leading-relaxed text-foreground whitespace-pre-wrap">
                {item.answer}
              </p>
            )}
          </CardContent>
        </Card>

        {/* User feedback comment */}
        {item.feedback && (
          <Card className="mt-4 border-amber-500/20 bg-amber-500/5">
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
        )}

        {/* Retrieval Trace */}
        {item.trace && item.trace.length > 0 && (
          <RetrievalTrace trace={item.trace} />
        )}

        {/* Session info */}
        <div className="mt-6 rounded-md border border-border bg-muted/30 px-4 py-3">
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <span className="text-muted-foreground">Session ID</span>
              <p className="mt-0.5 font-mono text-foreground">{item.sessionId}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Query ID</span>
              <p className="mt-0.5 font-mono text-foreground">{item.queryId}</p>
            </div>
          </div>
        </div>
      </div>
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
  const loopComplete = trace.find(e => e.kind === 'loop_complete');
  const toolResults = trace.filter(e => e.kind === 'tool_result');

  return (
    <Card className="mt-4">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Retrieval Trace
          </CardTitle>
          {loopComplete && (
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              {loopComplete.turnsUsed && (
                <span>{loopComplete.turnsUsed} turn{loopComplete.turnsUsed > 1 ? 's' : ''}</span>
              )}
              {loopComplete.elapsedMs && (
                <span>{(loopComplete.elapsedMs / 1000).toFixed(1)}s</span>
              )}
              {loopComplete.citedDocCount != null && (
                <span>{loopComplete.citedDocCount} cited</span>
              )}
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {toolResults.map((event, i) => (
            <div key={i} className="flex items-start gap-2.5">
              {/* Timeline dot */}
              <div className="mt-1.5 flex flex-col items-center">
                <div className={`rounded-full p-0.5 ${getStatusColor(event.status)}`}>
                  {event.status === 'ok' || event.status === 'terminal' ? (
                    <CheckCircle className="h-3 w-3" />
                  ) : event.status === 'error' ? (
                    <XCircle className="h-3 w-3" />
                  ) : (
                    getToolIcon(event.toolName || '')
                  )}
                </div>
                {i < toolResults.length - 1 && (
                  <div className="mt-1 h-full w-px bg-border" />
                )}
              </div>

              {/* Content */}
              <div className="min-w-0 flex-1 pb-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-foreground">
                    {event.toolName || event.kind}
                  </span>
                  {event.turn != null && (
                    <span className="text-[10px] text-muted-foreground">
                      T{event.turn}
                    </span>
                  )}
                  {event.latencyMs != null && (
                    <span className="text-[10px] text-muted-foreground">
                      {event.latencyMs}ms
                    </span>
                  )}
                </div>
                {event.summary && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{event.summary}</p>
                )}
                {event.docTitles && event.docTitles.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {event.docTitles.slice(0, 5).map((title, j) => (
                      <Badge key={j} variant="secondary" className="text-[10px] px-1.5 py-0 font-normal">
                        {title.length > 40 ? title.slice(0, 40) + '...' : title}
                      </Badge>
                    ))}
                    {event.docTitles.length > 5 && (
                      <span className="text-[10px] text-muted-foreground">
                        +{event.docTitles.length - 5} more
                      </span>
                    )}
                  </div>
                )}
                {/* Key metadata */}
                {event.metadata && Object.keys(event.metadata).length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-muted-foreground">
                    {Object.entries(event.metadata).map(([key, val]) => (
                      <span key={key}>
                        {key}: <span className="text-foreground">{String(val)}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Discovery summary from loop_complete */}
        {loopComplete?.discovery && Object.keys(loopComplete.discovery).length > 0 && (
          <>
            <Separator className="my-3" />
            <div>
              <span className="text-xs font-medium text-muted-foreground">Discovery Sources</span>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {Object.entries(loopComplete.discovery).map(([source, count]) => (
                  <Badge key={source} variant="outline" className="text-[10px] gap-1 font-normal">
                    {source}: {String(count)}
                  </Badge>
                ))}
              </div>
            </div>
          </>
        )}

        {/* Cited doc IDs */}
        {loopComplete?.citedDocIds && loopComplete.citedDocIds.length > 0 && (
          <>
            <Separator className="my-3" />
            <div>
              <span className="text-xs font-medium text-muted-foreground">Cited Documents</span>
              <div className="mt-1.5 space-y-0.5">
                {loopComplete.citedDocIds.map(docId => (
                  <p key={docId} className="text-xs font-mono text-foreground">{docId}</p>
                ))}
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
