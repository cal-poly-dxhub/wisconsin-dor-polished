'use client';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { cva, type VariantProps } from 'class-variance-authority';
import { AnimatePresence, motion } from 'framer-motion';
import { ExternalLink, Maximize2, X } from 'lucide-react';
import { memo, useCallback, useState } from 'react';
import { buildResolverUrl } from '@/lib/citation-resolver';
import type { InlineCitation } from '@/lib/parse-inline-citations';
import { AuthorityBadge } from './authority-badge';
import { DiscoveryBadge } from './discovery-badge';
import { chooseSourceTarget } from './source-target';

export interface ChunkSnippet {
  page: number;
  text: string;
}

export interface Document {
  documentId: string;
  title: string;
  content?: string;
  source?: string;
  sourceUrl?: string;
  s3Key?: string;
  startPage?: number;
  endPage?: number;
  authorityLevel?: number;
  discoveryTag?:
    | 'vector-search'
    | 'graph-neighbor'
    | 'fetched'
    | 'framework-list'
    | 'opinion-fetched'
    | 'unknown';
  chunks?: ChunkSnippet[];
}

const documentCardVariants = cva(
  'group cursor-pointer font-sans transition-[color,background-color,border-color,box-shadow] duration-200 ease-in-out border-black/15 dark:border-border hover:border-primary/60 hover:shadow-md hover:bg-accent/70 focus-within:border-primary/50 shadow-none dark:shadow-sm',
  {
    variants: {
      variant: {
        compact: 'max-w-sm',
        modal: 'w-full max-w-2xl',
      },
      size: {
        sm: 'text-sm',
        md: 'text-base',
        lg: 'text-lg',
      },
      state: {
        default: '',
        expanded: 'scale-95 opacity-0',
        collapsed: 'scale-100 opacity-100',
      },
    },
    defaultVariants: {
      variant: 'compact',
      size: 'md',
      state: 'default',
    },
  }
);

const documentHeaderVariants = cva('min-w-0 flex-1', {
  variants: {
    variant: {
      compact: '',
      modal: 'pb-2',
    },
    size: {
      sm: '',
      md: '',
      lg: '',
    },
  },
  defaultVariants: {
    variant: 'compact',
    size: 'md',
  },
});

const titleVariants = cva('leading-snug opacity-90', {
  variants: {
    variant: {
      compact: 'line-clamp-2',
      modal: 'line-clamp-2',
    },
    size: {
      sm: 'text-sm',
      md: 'text-sm font-semibold',
      lg: 'text-xl',
    },
  },
  defaultVariants: {
    variant: 'compact',
    size: 'md',
  },
});

// Header component using CVA
interface DocumentHeaderProps
  extends VariantProps<typeof documentHeaderVariants> {
  title: string;
  documentId?: string;
  variant?: 'compact' | 'modal' | null;
}

export function DocumentHeader({
  title,
  documentId,
  variant = 'compact',
  size = 'md',
}: DocumentHeaderProps) {
  return (
    <div className={cn(documentHeaderVariants({ variant, size }))}>
      <div className="min-w-0 flex-1">
        <CardTitle className={cn(titleVariants({ variant, size }))}>
          {title}
        </CardTitle>
        {variant === 'modal' && documentId && (
          <CardDescription>Document ID: {documentId}</CardDescription>
        )}
      </div>
    </div>
  );
}

const ANIMATION_CONFIG = {
  duration: 0.15,
  compact: { scale: 1, opacity: 1 },
  expanded: { scale: 0.95, opacity: 0 },
  modal: {
    initial: { scale: 0.95, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
    exit: { scale: 0.95, opacity: 0 },
  },
} as const;

const CONTENT_PREVIEW_LENGTH = 150;

function cleanContentText(text: string): string {
  return text
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^\n+/, '')
    .replace(/\n+$/, '');
}

function getSourceActionLabel(document: Document) {
  const text = `${document.documentId} ${document.title} ${document.source ?? ''}`.toLowerCase();

  if (text.includes('case-law') || text.includes('case law') || text.includes(' v. ')) {
    return 'View Case';
  }
  if (text.includes('wpam') || text.includes('property assessment manual')) {
    return 'View WPAM';
  }
  if (text.includes('statute') || text.includes('wis. stat') || text.startsWith('statutes-')) {
    return 'View Statute';
  }
  if (text.includes('admin rule') || text.includes('administrative code') || text.startsWith('admin_rules-')) {
    return 'View Rule';
  }
  if (text.includes('faq')) {
    return 'View FAQ';
  }
  if (text.includes('guide') || text.includes('publication') || text.includes('gov_publications')) {
    return 'View Guide';
  }

  return 'View Source';
}

interface DocumentCardCompactProps
  extends VariantProps<typeof documentCardVariants> {
  document: Document;
  citations?: InlineCitation[];
  className?: string;
  isExpanded: boolean;
  onClick: () => void;
  onSourceClick: (e: React.MouseEvent) => void;
}

export function DocumentCardCompact({
  document,
  citations,
  className,
  isExpanded: _isExpanded,
  onClick,
  onSourceClick,
  variant = 'compact',
  size = 'md',
}: DocumentCardCompactProps) {
  const content = document.content || '';
  const contentPreview =
    content.length > CONTENT_PREVIEW_LENGTH
      ? `${content.substring(0, CONTENT_PREVIEW_LENGTH)}...`
      : content;

  return (
    <motion.div
      onClick={onClick}
      className="cursor-pointer"
      initial={ANIMATION_CONFIG.compact}
      animate={ANIMATION_CONFIG.compact}
      transition={{ ease: 'easeIn', duration: ANIMATION_CONFIG.duration }}
    >
      <Card
        className={cn(
          documentCardVariants({
            variant,
            size,
            state: 'default',
          }),
          'flex flex-col rounded-lg overflow-hidden',
          className
        )}
      >
        <CardHeader className="px-4 pt-3.5 pb-0">
          <div className="flex items-start gap-2">
            <DocumentHeader
              title={document.title}
              documentId={document.documentId}
              variant={variant}
              size={size}
            />
            <button
              type="button"
              onClick={event => {
                event.stopPropagation();
                onClick();
              }}
              className="text-muted-foreground hover:bg-accent hover:text-foreground -mt-0.5 -mr-0.5 cursor-pointer rounded-md p-1 transition-[color,background-color,border-color]"
              aria-label="Expand document card"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          </div>
          {(document.authorityLevel !== undefined || (document.discoveryTag && document.discoveryTag !== 'unknown') || (citations && citations.length > 1)) && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1.5">
              {document.authorityLevel !== undefined && (
                <AuthorityBadge authorityLevel={document.authorityLevel} size="sm" />
              )}
              {document.discoveryTag && document.discoveryTag !== 'unknown' && (
                <DiscoveryBadge tag={document.discoveryTag} size="sm" />
              )}
              {citations && citations.length > 1 && (
                <span className="inline-flex items-center rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20 dark:bg-blue-950 dark:text-blue-300 dark:ring-blue-400/30">
                  {citations.length} citations
                </span>
              )}
            </div>
          )}
        </CardHeader>

        <CardContent className="px-4 pt-2.5 pb-3">
          {citations && citations.length > 0 && document.s3Key ? (
            <div className="flex flex-wrap gap-1.5">
              {citations.map((c) => (
                <button
                  key={c.page}
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-xs font-medium text-foreground/80 hover:bg-accent hover:text-foreground transition-[color,background-color,border-color] cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    const popup = window.open('about:blank', '_blank');
                    if (!popup) return;
                    void buildResolverUrl(document.s3Key!, c.page)
                      .then(url => { if (url) popup.location.href = url; else popup.close(); })
                      .catch(() => popup.close());
                  }}
                >
                  <span className="truncate max-w-[10rem]">{c.label}</span>
                  <span className="text-muted-foreground">p.{c.page}</span>
                  <ExternalLink className="h-2.5 w-2.5 text-muted-foreground" />
                </button>
              ))}
            </div>
          ) : (
            <CardDescription className="line-clamp-2 text-xs">
              {contentPreview}
            </CardDescription>
          )}
        </CardContent>

        {document.source && (document.sourceUrl || document.s3Key) && (
          <button
            type="button"
            className="mt-auto w-full border-t border-border/50 bg-muted/40 px-4 py-2 text-xs font-medium text-muted-foreground hover:bg-muted/70 hover:text-foreground transition-[color,background-color,border-color] cursor-pointer inline-flex items-center justify-end gap-1.5"
            onClick={citations && citations.length > 1 && document.s3Key ? (e) => { e.stopPropagation(); onClick(); } : onSourceClick}
          >
            <span>{citations && citations.length > 1 && document.s3Key ? 'View Citations' : getSourceActionLabel(document)}</span>
            <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </Card>
    </motion.div>
  );
}

interface DocumentCardModalProps {
  document: Document;
  citations?: InlineCitation[];
  isAnimating: boolean;
  onClose: (e: React.MouseEvent) => void;
  onSourceClick: (e: React.MouseEvent) => void;
}

function DocumentCardModal({
  document,
  citations,
  isAnimating,
  onClose,
  onSourceClick,
}: DocumentCardModalProps) {
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose(e);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <motion.div
        onClick={e => e.stopPropagation()}
        className="bg-background w-full max-w-2xl rounded-lg shadow-2xl max-h-[80vh] flex flex-col border border-black/15 dark:border-border"
        variants={ANIMATION_CONFIG.modal}
        initial="initial"
        animate={isAnimating ? 'initial' : 'animate'}
        exit="exit"
        transition={{ ease: 'easeIn', duration: ANIMATION_CONFIG.duration }}
      >
        <Card className="flex h-full flex-col border-0 shadow-none overflow-hidden">
          {/* Top bar: close + source link */}
          <div className="flex items-center justify-between px-5 pt-4 pb-0">
            <div className="flex items-center gap-2">
              {document.authorityLevel !== undefined && (
                <AuthorityBadge authorityLevel={document.authorityLevel} size="sm" />
              )}
              {document.discoveryTag && document.discoveryTag !== 'unknown' && (
                <DiscoveryBadge tag={document.discoveryTag} size="sm" />
              )}
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={onClose}
              className="h-8 w-8 shrink-0 cursor-pointer"
              aria-label="Close modal"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          {/* Title section */}
          <div className="px-5 pt-3 pb-4 border-b border-border">
            <h2 id="modal-title" className="text-lg font-semibold leading-snug">
              {document.title}
            </h2>
            <p className="text-muted-foreground text-xs mt-1 font-mono truncate">
              {document.documentId}
            </p>
          </div>

          {/* Per-page citation links */}
          {citations && citations.length > 0 && document.s3Key ? (
            <CardContent className="flex-1 overflow-y-auto px-5 pt-5 pb-5">
              <p className="text-sm font-medium text-foreground mb-3">
                Cited {citations.length} {citations.length === 1 ? 'location' : 'locations'} in this response
              </p>
              <div className="flex flex-col gap-2.5">
                {citations.map((c) => {
                  const snippet = document.chunks?.find(ch => ch.page === c.page);
                  return (
                    <button
                      key={c.page}
                      type="button"
                      className="w-full text-left rounded-lg border border-border bg-background px-4 py-3 hover:bg-accent/50 hover:border-primary/40 transition-[color,background-color,border-color,box-shadow] cursor-pointer shadow-sm hover:shadow-md"
                      onClick={(e) => {
                        e.stopPropagation();
                        const popup = window.open('about:blank', '_blank');
                        if (!popup) return;
                        void buildResolverUrl(document.s3Key!, c.page)
                          .then(url => { if (url) popup.location.href = url; else popup.close(); })
                          .catch(() => popup.close());
                      }}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-foreground truncate">{c.label}</span>
                        <span className="flex items-center gap-2 shrink-0 ml-3">
                          <span className="text-muted-foreground text-xs font-normal">Page {c.page}</span>
                          <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                        </span>
                      </div>
                      {snippet && (
                        <p className="mt-1.5 text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                          {snippet.text}
                        </p>
                      )}
                    </button>
                  );
                })}
              </div>
            </CardContent>
          ) : (
            <CardContent className="flex-1 overflow-hidden p-5">
              <div className="rounded-md border border-border bg-muted/30 overflow-hidden max-h-full flex flex-col">
                <div className="p-4 overflow-y-auto scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50">
                  <p className="text-sm leading-normal whitespace-pre-wrap text-foreground/90">
                    {cleanContentText(document.content || 'No content available.')}
                  </p>
                </div>
                {document.source && (document.sourceUrl || document.s3Key) && (
                  <button
                    type="button"
                    className="w-full border-t border-border bg-muted/70 px-4 py-2.5 text-sm font-medium text-primary hover:bg-muted transition-[color,background-color,border-color] cursor-pointer inline-flex items-center justify-center gap-1.5"
                    onClick={onSourceClick}
                  >
                    <span>{getSourceActionLabel(document)}</span>
                    <ExternalLink className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </CardContent>
          )}
        </Card>
      </motion.div>
    </div>
  );
}

interface DocumentCardProps {
  document: Document;
  citations?: InlineCitation[];
  className?: string;
  onSourceClick?: (document: Document) => void;
}

export const DocumentCard = memo(function DocumentCard({
  document,
  citations,
  className,
  onSourceClick,
}: DocumentCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isAnimating, setIsAnimating] = useState(false);

  const onClick = useCallback(() => {
    setIsAnimating(true);
    setIsExpanded(true);
    requestAnimationFrame(() => setIsAnimating(false));
  }, []);

  const collapse = useCallback(() => {
    setIsAnimating(true);
    setIsExpanded(false);
    requestAnimationFrame(() => setIsAnimating(false));
  }, []);

  const handleSourceClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();

      const target = chooseSourceTarget(document);
      if (target?.kind === 's3') {
        const popup = window.open('about:blank', '_blank');
        if (!popup) return;
        const page = (citations && citations.length > 0 ? citations[0].page : undefined) ?? document.startPage;
        void buildResolverUrl(target.s3Key, page)
          .then(url => {
            if (url) {
              popup.location.href = url;
            } else {
              popup.close();
            }
          })
          .catch(() => popup.close());
      } else if (target?.kind === 'url') {
        window.open(target.url, '_blank', 'noopener,noreferrer');
      }

      onSourceClick?.(document);
    },
    [document, citations, onSourceClick]
  );

  const handleModalClose = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      collapse();
    },
    [collapse]
  );

  return (
    <>
      <DocumentCardCompact
        document={document}
        citations={citations}
        className={className}
        isExpanded={isExpanded}
        onClick={onClick}
        onSourceClick={handleSourceClick}
      />

      <AnimatePresence>
        {isExpanded && (
          <DocumentCardModal
            document={document}
            citations={citations}
            isAnimating={isAnimating}
            onClose={handleModalClose}
            onSourceClick={handleSourceClick}
          />
        )}
      </AnimatePresence>
    </>
  );
});
