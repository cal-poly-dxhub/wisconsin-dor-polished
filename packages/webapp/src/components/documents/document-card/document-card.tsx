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
import { AuthorityBadge } from './authority-badge';
import { DiscoveryBadge } from './discovery-badge';
import { chooseSourceTarget } from './source-target';

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
}

const documentCardVariants = cva(
  'group cursor-pointer font-sans transition-colors duration-200 ease-in-out border-black/15 dark:border-border hover:border-primary/40 hover:shadow-md focus-within:border-primary/50 shadow-none dark:shadow-sm',
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
  className?: string;
  isExpanded: boolean;
  onClick: () => void;
  onSourceClick: (e: React.MouseEvent) => void;
}

export function DocumentCardCompact({
  document,
  className,
  isExpanded,
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
      animate={
        isExpanded ? ANIMATION_CONFIG.expanded : ANIMATION_CONFIG.compact
      }
      transition={{ ease: 'easeIn', duration: ANIMATION_CONFIG.duration }}
    >
      <Card
        className={cn(
          documentCardVariants({
            variant,
            size,
            state: isExpanded ? 'expanded' : 'collapsed',
          }),
          'flex flex-col rounded-lg',
          className
        )}
      >
        <CardHeader className="px-4 pt-3 pb-1.5">
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
              className="text-muted-foreground hover:bg-accent hover:text-foreground -mt-0.5 -mr-0.5 cursor-pointer rounded-md p-1 transition-colors"
              aria-label="Expand document card"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          </div>
          <CardDescription className="line-clamp-2 text-xs">
            {contentPreview}
          </CardDescription>
        </CardHeader>

        <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 px-4 pb-3">
          {document.authorityLevel !== undefined && (
            <AuthorityBadge authorityLevel={document.authorityLevel} size="sm" />
          )}
          {document.discoveryTag && document.discoveryTag !== 'unknown' && (
            <DiscoveryBadge tag={document.discoveryTag} size="sm" />
          )}
          {document.source && (document.sourceUrl || document.s3Key) && (
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center gap-1 text-xs transition-colors cursor-pointer"
              onClick={onSourceClick}
            >
              <span>{getSourceActionLabel(document)}</span>
              <ExternalLink className="h-3 w-3" />
            </button>
          )}
        </div>
      </Card>
    </motion.div>
  );
}

interface DocumentCardModalProps {
  document: Document;
  isAnimating: boolean;
  onClose: (e: React.MouseEvent) => void;
  onSourceClick: (e: React.MouseEvent) => void;
}

function DocumentCardModal({
  document,
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
        className="bg-background w-full max-w-2xl rounded-lg shadow-2xl max-h-[80vh] flex flex-col"
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
            <div className="flex items-center gap-3">
              {document.source && (document.sourceUrl || document.s3Key) && (
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium text-foreground hover:bg-accent transition-colors cursor-pointer"
                  onClick={onSourceClick}
                >
                  <span>{getSourceActionLabel(document)}</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </button>
              )}
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

          {/* Content */}
          <CardContent className="scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 flex-1 overflow-y-auto p-5">
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/90">
              {document.content || 'No content available.'}
            </p>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}

interface DocumentCardProps {
  document: Document;
  className?: string;
  onSourceClick?: (document: Document) => void;
}

export const DocumentCard = memo(function DocumentCard({
  document,
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
        // window.open() must run synchronously to satisfy popup blockers;
        // the JWT fetch is async so we open about:blank first and redirect
        // the popup once the resolver URL is built. We can't pass
        // 'noopener' here — browsers return null from window.open with
        // noopener, which would defeat the popup-first pattern. The only
        // page the popup ever lands on is our resolver's S3 redirect, so
        // window.opener leakage is bounded.
        const popup = window.open('about:blank', '_blank');
        if (!popup) return;
        void buildResolverUrl(target.s3Key, document.startPage)
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
    [document, onSourceClick]
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
        className={className}
        isExpanded={isExpanded}
        onClick={onClick}
        onSourceClick={handleSourceClick}
      />

      <AnimatePresence>
        {isExpanded && (
          <DocumentCardModal
            document={document}
            isAnimating={isAnimating}
            onClose={handleModalClose}
            onSourceClick={handleSourceClick}
          />
        )}
      </AnimatePresence>
    </>
  );
});
