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
import { ExternalLink, FileText, X } from 'lucide-react';
import { useCallback, useState } from 'react';
import { DocumentBadge } from './document-badge';

export interface Document {
  documentId: string;
  title: string;
  content: string;
  source?: string;
  sourceUrl?: string;
}

const documentCardVariants = cva(
  'cursor-pointer font-sans transition-shadow duration-200 ease-in-out hover:shadow-lg',
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

const documentHeaderVariants = cva('flex min-w-0 flex-1 gap-2', {
  variants: {
    variant: {
      compact: 'items-center',
      modal: 'items-start pb-2',
    },
    size: {
      sm: 'gap-1.5',
      md: 'gap-2',
      lg: 'gap-2.5',
    },
  },
  defaultVariants: {
    variant: 'compact',
    size: 'md',
  },
});

const iconVariants = cva('text-muted-foreground flex-shrink-0', {
  variants: {
    variant: {
      compact: '',
      modal: 'mt-1',
    },
    size: {
      sm: 'h-3 w-3',
      md: 'h-4 w-4',
      lg: 'h-5 w-5',
    },
  },
  defaultVariants: {
    variant: 'compact',
    size: 'md',
  },
});

const titleVariants = cva('leading-tight opacity-90', {
  variants: {
    variant: {
      compact: 'line-clamp-1 truncate',
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
      <FileText className={cn(iconVariants({ variant, size }))} />
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
          <DocumentHeader
            title={document.title}
            variant={variant}
            size={size}
          />
          <CardDescription className="line-clamp-2">
            {contentPreview}
          </CardDescription>
        </CardHeader>

        <CardContent className="pt-0">
          {document.source && (
            <DocumentBadge
              source={document.source}
              sourceUrl={document.sourceUrl}
              onSourceClick={onSourceClick}
              size="sm"
            />
          )}

          <div className="text-muted-foreground flex items-center justify-between text-sm">
            <span>ID: {document.documentId}</span>
            <ExternalLink className="h-3 w-3" />
          </div>
        </CardContent>
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
          <CardHeader className="border-border border-b pb-3 flex-shrink-0">
            <div className="flex items-start justify-between">
              <DocumentHeader
                title={document.title}
                documentId={document.documentId}
                variant="modal"
              />

              <div className="flex items-center gap-2">
                {document.source && (
                  <DocumentBadge
                    source={document.source}
                    sourceUrl={document.sourceUrl}
                    onSourceClick={onSourceClick}
                    size="md"
                  />
                )}

                <Button
                  variant="ghost"
                  size="icon"
                  onClick={onClose}
                  className="h-8 w-8"
                  aria-label="Close modal"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
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

export function DocumentCard({
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
}
