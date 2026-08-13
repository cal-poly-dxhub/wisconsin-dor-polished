'use client';

import { Clock } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { Textarea } from '@/components/ui/textarea';
import { useChatStore } from '@/stores/chat-store';
import { useFeedbackStore } from '@/stores/feedback-store';
import { YesNo } from './yes-no';

export function SpeedSection({ queryId }: { queryId: string }) {
  const draft = useFeedbackStore((s) => s.getDraft(queryId));
  const setSpeedTimely = useFeedbackStore((s) => s.setSpeedTimely);
  const setSpeedComment = useFeedbackStore((s) => s.setSpeedComment);
  // The actual measured "Thought for Ns" duration, so the user's answer is
  // anchored to a real number and we capture it alongside their feedback.
  const duration = useChatStore((s) => s.queries[queryId]?.thinkingDuration);

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between gap-4">
        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
          Was the response timely?
          {typeof duration === 'number' && duration > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              <Clock className="h-3 w-3" />
              {duration}s
            </span>
          )}
        </span>
        <YesNo
          value={draft.speedTimely ?? ''}
          ariaLabel="Was the response timely?"
          onChange={(v) => setSpeedTimely(queryId, v)}
        />
      </div>
      <AnimatePresence initial={false}>
        {draft.speedTimely === 'no' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.16 }}
            className="overflow-hidden"
          >
            <Textarea
              value={draft.speedComment}
              onChange={(e) => setSpeedComment(queryId, e.target.value)}
              placeholder="What felt slow about it?"
              className="min-h-[52px] resize-none"
              autoFocus
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
