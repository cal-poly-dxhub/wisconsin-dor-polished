'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Textarea } from '@/components/ui/textarea';
import { useFeedbackStore, SUBSECTIONS } from '@/stores/feedback-store';
import { YesNo } from './yes-no';
import { RequiredMark } from './field-marker';

export function ResponseSection({ queryId }: { queryId: string }) {
  const response = useFeedbackStore((s) => s.getDraft(queryId).response);
  const setSubsection = useFeedbackStore((s) => s.setSubsection);

  return (
    <div className="space-y-5">
      {SUBSECTIONS.map(({ key, label }) => {
        const sub = response[key] ?? { answer: null, comment: '' };
        return (
          <div key={key} className="space-y-2.5">
            <div className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                {label}
                <RequiredMark />
              </span>
              <YesNo
                value={sub.answer ?? ''}
                ariaLabel={label}
                onChange={(answer) => setSubsection(queryId, key, { answer })}
              />
            </div>
            <AnimatePresence initial={false}>
              {sub.answer === 'no' && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.16 }}
                  className="overflow-hidden"
                >
                  <Textarea
                    value={sub.comment}
                    onChange={(e) => setSubsection(queryId, key, { comment: e.target.value })}
                    placeholder="What was wrong, and what did you expect instead?"
                    className="min-h-[64px] resize-none"
                    autoFocus
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}
