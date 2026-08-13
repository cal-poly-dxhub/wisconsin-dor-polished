'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { Highlighter, X } from 'lucide-react';
import { useFeedbackStore } from '@/stores/feedback-store';
import { useAnnotationLock } from '@/hooks/use-annotation-lock';

// App-level chrome for annotation mode: a slim pulsing blue inset border and a
// top banner with an exit CTA. Mounted once near the app root; renders only
// while annotation mode is active. Also installs the beforeunload guard.
export function AnnotationModeChrome() {
  const isAnnotating = useAnnotationLock();
  const annotatingQueryId = useFeedbackStore((s) => s.annotatingQueryId);
  const count = useFeedbackStore((s) =>
    annotatingQueryId ? s.getDraft(annotatingQueryId).annotations.length : 0
  );
  const exitAnnotate = useFeedbackStore((s) => s.exitAnnotate);

  return (
    <AnimatePresence>
      {isAnnotating && (
        <>
          <div className="annotate-inset pointer-events-none fixed inset-0 z-[55]" aria-hidden />
          <motion.div
            initial={{ y: -48, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -48, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
            className="fixed inset-x-0 top-0 z-[56] flex items-center justify-center"
          >
            <div className="mt-3 flex items-center gap-3 rounded-full border border-blue-400/40 bg-blue-950/80 px-4 py-2 text-sm text-blue-50 shadow-lg backdrop-blur">
              <Highlighter className="h-4 w-4 text-blue-300" />
              <span>
                Annotation mode — highlight any text to comment
                {count > 0 && (
                  <span className="ml-1 text-blue-300">
                    ({count} so far)
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={exitAnnotate}
                className="ml-1 flex items-center gap-1.5 rounded-full bg-blue-500 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-blue-400 cursor-pointer"
              >
                <X className="h-3.5 w-3.5" />
                Done
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
