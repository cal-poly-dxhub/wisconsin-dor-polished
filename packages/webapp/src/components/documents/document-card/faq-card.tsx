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
import { HelpCircle, X } from 'lucide-react';
import { useCallback, useState } from 'react';

export interface FAQ {
  faqId: string;
  question: string;
  answer: string;
}

const faqCardVariants = cva(
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

const faqHeaderVariants = cva('flex min-w-0 flex-1 items-start gap-2', {
  variants: {
    variant: {
      compact: '',
      modal: 'pb-2',
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

const iconVariants = cva('text-muted-foreground mt-0.5 flex-shrink-0', {
  variants: {
    size: {
      sm: 'h-3 w-3',
      md: 'h-4 w-4',
      lg: 'h-5 w-5',
    },
  },
  defaultVariants: {
    size: 'md',
  },
});

const titleVariants = cva('line-clamp-2 leading-tight opacity-90', {
  variants: {
    size: {
      sm: 'text-sm',
      md: 'text-lg',
      lg: 'text-xl',
    },
  },
  defaultVariants: {
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
      <HelpCircle className={cn(iconVariants({ size }))} />
      <div className="min-w-0 flex-1">
        <CardTitle className={cn(titleVariants({ size }))}>
          {question}
        </CardTitle>
        {variant === 'modal' && faqId && (
          <CardDescription>FAQ ID: {faqId}</CardDescription>
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
          className
        )}
      >
        <CardHeader className="pb-3">
          <FAQHeader question={faq.question} variant={variant} size={size} />
          <CardDescription className="line-clamp-2">
            {answerPreview}
          </CardDescription>
        </CardHeader>

        <CardContent className="pt-0">
          <div className="text-muted-foreground text-sm">ID: {faq.faqId}</div>
        </CardContent>
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
          <CardHeader className="border-border border-b pb-3 flex-shrink-0">
            <div className="flex items-start justify-between">
              <FAQHeader
                question={faq.question}
                faqId={faq.faqId}
                variant="modal"
              />

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
          </CardHeader>

          <CardContent className="scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 flex-1 overflow-y-auto p-6">
            <div className="prose prose-sm max-w-none">
              <p className="leading-relaxed whitespace-pre-wrap">
                {faq.answer}
              </p>
            </div>
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

export function FAQCard({ faq, className }: FAQCardProps) {
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
}
