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

export interface FAQ {
  faqId: string;
  question: string;
  answer: string;
}

const faqCardVariants = cva(
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

const faqHeaderVariants = cva('min-w-0 flex-1', {
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

interface FAQHeaderProps extends VariantProps<typeof faqHeaderVariants> {
  question: string;
  faqId?: string;
  variant?: 'compact' | 'modal' | null;
}

export function FAQHeader({
  question,
  faqId,
  variant = 'compact',
  size = 'md',
}: FAQHeaderProps) {
  return (
    <div className={cn(faqHeaderVariants({ variant, size }))}>
      <div className="min-w-0 flex-1">
        <CardTitle className={cn(titleVariants({ variant, size }))}>
          {question}
        </CardTitle>
        {variant === 'modal' && faqId && (
          <CardDescription>{faqId}</CardDescription>
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

const ANSWER_PREVIEW_LENGTH = 150;

interface FAQCardCompactProps extends VariantProps<typeof faqCardVariants> {
  faq: FAQ;
  className?: string;
  isExpanded: boolean;
  onClick: () => void;
}

export function FAQCardCompact({
  faq,
  className,
  isExpanded,
  onClick,
  variant = 'compact',
  size = 'md',
}: FAQCardCompactProps) {
  const answerPreview =
    faq.answer.length > ANSWER_PREVIEW_LENGTH
      ? `${faq.answer.substring(0, ANSWER_PREVIEW_LENGTH)}...`
      : faq.answer;

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
          faqCardVariants({
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
            <FAQHeader question={faq.question} faqId={faq.faqId} variant={variant} size={size} />
            <button
              type="button"
              onClick={event => {
                event.stopPropagation();
                onClick();
              }}
              className="text-muted-foreground hover:bg-accent hover:text-foreground -mt-0.5 -mr-0.5 cursor-pointer rounded-md p-1 transition-colors"
              aria-label="Expand FAQ card"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          </div>
          <CardDescription className="line-clamp-2 text-xs">
            {answerPreview}
          </CardDescription>
        </CardHeader>

        <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 px-4 pb-3">
          <AuthorityBadge authorityLevel={6} size="sm" />
        </div>
      </Card>
    </motion.div>
  );
}

interface FAQCardModalProps {
  faq: FAQ;
  isAnimating: boolean;
  onClose: (e: React.MouseEvent) => void;
}

function FAQCardModal({ faq, isAnimating, onClose }: FAQCardModalProps) {
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
          {/* Top bar: badges + close */}
          <div className="flex items-center justify-between px-5 pt-4 pb-0">
            <div className="flex items-center gap-2">
              <AuthorityBadge authorityLevel={6} size="sm" />
            </div>
            <div className="flex items-center gap-3">
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
              {faq.question}
            </h2>
            <p className="text-muted-foreground text-xs mt-1 font-mono truncate">
              {faq.faqId}
            </p>
          </div>

          {/* Content */}
          <CardContent className="scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 flex-1 overflow-y-auto p-5">
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/90">
              {faq.answer}
            </p>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}

interface FAQCardProps {
  faq: FAQ;
  className?: string;
}

export const FAQCard = memo(function FAQCard({ faq, className }: FAQCardProps) {
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

  const handleModalClose = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      collapse();
    },
    [collapse]
  );

  return (
    <>
      <FAQCardCompact
        faq={faq}
        className={className}
        isExpanded={isExpanded}
        onClick={onClick}
      />

      <AnimatePresence>
        {isExpanded && (
          <FAQCardModal
            faq={faq}
            isAnimating={isAnimating}
            onClose={handleModalClose}
          />
        )}
      </AnimatePresence>
    </>
  );
});
