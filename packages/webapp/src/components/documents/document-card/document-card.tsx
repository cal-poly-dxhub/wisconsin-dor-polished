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
import { Maximize2, X } from 'lucide-react';
import { memo, useCallback, useState } from 'react';
import { AuthorityBadge } from './authority-badge';
import { DiscoveryBadge } from './discovery-badge';
import { DocumentBadge } from './document-badge';

export interface Document {
  documentId: string;
  title: string;
  content: string;
  source?: string;
  sourceUrl?: string;
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
  'group cursor-pointer font-sans transition-colors duration-200 ease-in-out hover:border-primary/40 hover:shadow-md focus-within:border-primary/50',
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
      compact: 'line-clamp-3',
      modal: 'line-clamp-2',
    },
    size: {
      sm: 'text-sm',
      md: 'text-lg',
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
        {variant === 'compact' && documentId && (
          <div className="text-muted-foreground mb-1 truncate text-[0.65rem] font-medium uppercase tracking-wide">
            ID: {documentId}
          </div>
        )}
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
  const contentPreview =
    document.content.length > CONTENT_PREVIEW_LENGTH
      ? `${document.content.substring(0, CONTENT_PREVIEW_LENGTH)}...`
      : document.content;

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
          className
        )}
      >
        <CardHeader className="pb-3">
          <div className="flex items-start gap-3">
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
              className="text-muted-foreground hover:bg-accent hover:text-foreground -mt-1 -mr-1 cursor-pointer rounded-md p-1.5 transition-colors"
              aria-label="Expand document card"
            >
              <Maximize2 className="h-4 w-4" />
            </button>
          </div>
          <CardDescription className="line-clamp-2">
            {contentPreview}
          </CardDescription>
        </CardHeader>

        <CardContent className="pt-0">
          <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
            {document.authorityLevel !== undefined && (
              <AuthorityBadge authorityLevel={document.authorityLevel} size="sm" />
            )}
            {document.discoveryTag && document.discoveryTag !== 'unknown' && (
              <DiscoveryBadge tag={document.discoveryTag} size="sm" />
            )}
          </div>

        </CardContent>
        {document.source && (
          <div className="px-6 pb-6">
            <DocumentBadge
              source={getSourceActionLabel(document)}
              sourceUrl={document.sourceUrl}
              onSourceClick={onSourceClick}
              size="sm"
            />
          </div>
        )}
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
          <CardHeader className="border-border border-b pb-4 flex-shrink-0">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-muted-foreground mb-1 truncate text-[0.65rem] font-medium uppercase tracking-wide">
                  ID: {document.documentId}
                </div>
                <DocumentHeader
                  title={document.title}
                  variant="modal"
                />
                <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
                  {document.authorityLevel !== undefined && (
                    <AuthorityBadge authorityLevel={document.authorityLevel} size="md" />
                  )}
                  {document.discoveryTag && document.discoveryTag !== 'unknown' && (
                    <DiscoveryBadge tag={document.discoveryTag} size="md" />
                  )}
                </div>
                {document.source && (
                  <div className="mt-3">
                    <DocumentBadge
                      source={getSourceActionLabel(document)}
                      sourceUrl={document.sourceUrl}
                      onSourceClick={onSourceClick}
                      size="md"
                    />
                  </div>
                )}
                {document.sourceUrl && (
                  <details className="mt-2 text-xs text-muted-foreground">
                    <summary className="cursor-pointer select-none hover:text-foreground">
                      Show original link
                    </summary>
                    <a
                      href={document.sourceUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 block break-all underline-offset-4 hover:text-foreground hover:underline"
                    >
                      {document.sourceUrl}
                    </a>
                  </details>
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
          </CardHeader>

          <CardContent className="scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 flex-1 overflow-y-auto p-6">
            <div className="prose prose-sm max-w-none">
              <p className="leading-relaxed whitespace-pre-wrap">
                {document.content}
              </p>
            </div>
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
      if (document.sourceUrl) {
        window.open(document.sourceUrl, '_blank', 'noopener,noreferrer');
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
