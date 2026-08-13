'use client';

import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { useFeedbackStore, SUBSECTIONS, hasDraftContent, type FeedbackDraft } from '@/stores/feedback-store';
import { useChatStore } from '@/stores/chat-store';
import { useAssignFeedback } from '@/hooks/api/chat';
import type { ResourceItem } from '@/stores/types';
import { ThumbRating } from './thumb-rating';
import { ResponseSection } from './response-section';
import { SourcesSection } from './sources-section';
import { SpeedSection } from './speed-section';
import { AnnotateCta } from './annotate-cta';
import { RequiredMark, OptionalMark } from './field-marker';

// Maps a local FeedbackDraft to the API payload. `thumbUp` is derived from the
// top-level rating (up → true, mid/down → false) so the admin activity GSI keeps
// filtering/summarizing on it; the full structured draft rides along in
// `richFeedback`. The legacy `feedback` string is left unset in phase 1.
function toFeedbackPayload(queryId: string, draft: FeedbackDraft) {
  return {
    queryId,
    thumbUp: draft.rating === 'up',
    richFeedback: {
      rating: draft.rating,
      positiveComment: draft.positiveComment,
      response: draft.response,
      sourcesOk: draft.sourcesOk,
      sourceNotes: draft.sourceNotes,
      linksWork: draft.linksWork,
      brokenLinkIds: draft.brokenLinkIds,
      brokenLinksReason: draft.brokenLinksReason,
      annotations: draft.annotations,
      speedTimely: draft.speedTimely,
      speedComment: draft.speedComment,
    },
  };
}

function SectionHeading({
  children,
  marker,
}: {
  children: React.ReactNode;
  marker?: 'required' | 'optional';
}) {
  return (
    <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
      {marker === 'required' && <RequiredMark />}
      {marker === 'optional' && <OptionalMark />}
    </h3>
  );
}

function CollapsibleSection({
  title,
  marker,
  defaultOpen = false,
  children,
}: {
  title: string;
  marker?: 'required' | 'optional';
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 py-3 cursor-pointer"
      >
        <ChevronRight
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200 ${
            open ? 'rotate-90' : ''
          }`}
        />
        <SectionHeading marker={marker}>{title}</SectionHeading>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="pb-4 pt-3">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

export function FeedbackModal({
  queryId,
  items,
}: {
  queryId: string;
  items: ResourceItem[];
}) {
  const activeModalQueryId = useFeedbackStore((s) => s.activeModalQueryId);
  const annotatingQueryId = useFeedbackStore((s) => s.annotatingQueryId);
  const draft = useFeedbackStore((s) => s.getDraft(queryId));
  const closeModal = useFeedbackStore((s) => s.closeModal);
  const setRating = useFeedbackStore((s) => s.setRating);
  const setPositiveComment = useFeedbackStore((s) => s.setPositiveComment);
  const submit = useFeedbackStore((s) => s.submit);
  const resetDraft = useFeedbackStore((s) => s.resetDraft);
  const sessionId = useChatStore((s) => s.sessionId);
  const assignFeedback = useAssignFeedback();

  const canClear = hasDraftContent(draft);

  // Hide (without unmounting the draft) while annotation mode is active for this
  // query, then reopen automatically when the user exits annotate mode.
  const open = activeModalQueryId === queryId && annotatingQueryId === null;

  // Annotate opens by default once the response has annotations, so returning
  // from annotation mode lands the user back on an expanded Annotate section.
  const hasAnnotations = draft.annotations.length > 0;

  const rating = draft.rating;
  const showFullFlow = rating === 'mid' || rating === 'down';

  const canSubmit =
    rating === 'up' ||
    (showFullFlow &&
      SUBSECTIONS.every(({ key }) => draft.response[key]?.answer != null) &&
      draft.sourcesOk != null &&
      draft.linksWork != null);

  function handleSubmit() {
    if (!sessionId) {
      toast.error('No active session');
      return;
    }
    // Mark submitted locally (keeps the draft/indicator state) and close, then
    // POST. Optimistic: the draft persists so a failed send can be retried.
    submit(queryId);
    closeModal();
    assignFeedback.mutate(
      { sessionId, payload: toFeedbackPayload(queryId, draft) },
      {
        onSuccess: () => toast.success('Feedback submitted'),
        onError: () => toast.error('Failed to submit feedback — your draft is saved'),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) closeModal(); }}>
      <DialogContent className="!max-w-3xl w-[calc(100vw-2rem)] max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Feedback</DialogTitle>
        </DialogHeader>

        <div className="mt-2 space-y-6">
          {/* Section 1 — overall rating */}
          <div className="space-y-3">
            <p className="text-center text-sm font-medium text-foreground">
              Is this answer usable as-is?
            </p>
            <div className="mx-auto max-w-xl py-1">
              <ThumbRating
                value={rating}
                spread
                aria-label="Is this answer usable as-is?"
                onChange={(v) => setRating(queryId, v)}
              />
            </div>
            <div className="mx-auto grid max-w-xl grid-cols-3 gap-3 text-center text-xs text-muted-foreground">
              <p>
                <span className="font-medium text-emerald-500">Good</span>
                <br />
                Ready to use with no edits
              </p>
              <p>
                <span className="font-medium text-amber-500">Mixed</span>
                <br />
                Usable after minor fixes
              </p>
              <p>
                <span className="font-medium text-red-500">Poor</span>
                <br />
                Wrong or needs a rewrite
              </p>
            </div>
          </div>

          <AnimatePresence mode="wait" initial={false}>
            {rating === 'up' && (
              <motion.div
                key="up"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                transition={{ duration: 0.18 }}
                className="space-y-2"
              >
                <p className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-muted-foreground">
                  Only choose this option if this answer is ready to be used as-is, without any
                  modifications.
                </p>
                <SectionHeading marker="optional">What was good about it?</SectionHeading>
                <Textarea
                  value={draft.positiveComment}
                  onChange={(e) => setPositiveComment(queryId, e.target.value)}
                  placeholder="What made this answer work for you? (optional)"
                  className="min-h-[96px] resize-none"
                  autoFocus
                />
              </motion.div>
            )}

            {showFullFlow && (
              <motion.div
                key="full"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                transition={{ duration: 0.18 }}
                className="divide-y divide-border"
              >
                <CollapsibleSection title="Response" marker="required" defaultOpen>
                  <ResponseSection queryId={queryId} />
                </CollapsibleSection>

                <CollapsibleSection title="Sources" marker="required" defaultOpen>
                  <SourcesSection queryId={queryId} items={items} />
                </CollapsibleSection>

                <CollapsibleSection
                  key={hasAnnotations ? 'annotate-open' : 'annotate'}
                  title="Annotate"
                  marker="optional"
                  defaultOpen={hasAnnotations}
                >
                  <AnnotateCta queryId={queryId} />
                </CollapsibleSection>

                <CollapsibleSection title="Speed" marker="optional">
                  <SpeedSection queryId={queryId} />
                </CollapsibleSection>
              </motion.div>
            )}
          </AnimatePresence>

          {rating !== null && (
            <div className="flex items-center justify-between border-t border-border pt-4">
              <Button
                variant="ghost"
                onClick={() => resetDraft(queryId)}
                disabled={!canClear}
                className="text-muted-foreground hover:text-destructive"
              >
                Clear answers
              </Button>
              <Button onClick={handleSubmit} disabled={!canSubmit}>
                Submit
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
