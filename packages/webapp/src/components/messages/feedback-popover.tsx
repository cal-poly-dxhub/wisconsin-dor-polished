'use client';

import { useState, useEffect } from 'react';
import { Check, X } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { Textarea } from '../ui/textarea';
import { Button } from '../ui/button';
import { ButtonGroup } from '../ui/button-group';

interface FeedbackPopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (feedback: string) => void;
  children: React.ReactNode;
}

export function FeedbackPopover({
  open,
  onOpenChange,
  onSubmit,
  children,
}: FeedbackPopoverProps) {
  const [feedback, setFeedback] = useState('');
  const maxLength = 500;
  const remainingChars = maxLength - feedback.length;

  useEffect(() => {
    if (!open) return;

    const handleScroll = () => {
      onOpenChange(false);
      setFeedback('');
    };

    window.addEventListener('scroll', handleScroll, true);

    return () => {
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [open, onOpenChange]);

  const handleSubmit = () => {
    onSubmit(feedback);
    setFeedback('');
    onOpenChange(false);
  };

  const handleCancel = () => {
    setFeedback('');
    onOpenChange(false);
  };

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-h-96 overflow-y-auto">
        <div className="space-y-3">
          <div className="space-y-2">
            <Textarea
              value={feedback}
              onChange={e => setFeedback(e.target.value)}
              maxLength={maxLength}
              placeholder="Share your thoughts..."
              className="resize-none"
            />
          </div>
          <div className="flex items-start justify-between">
            <div className="text-xs text-muted-foreground">
              {remainingChars}/{maxLength}
            </div>
            <ButtonGroup>
              <Button
                variant="outline"
                size="sm"
                onClick={handleSubmit}
                aria-label="Submit feedback"
              >
                <Check className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleCancel}
                aria-label="Cancel"
              >
                <X className="h-4 w-4" />
              </Button>
            </ButtonGroup>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
