'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { MessageSquarePlus, ArrowRight, X, Sparkles } from 'lucide-react';
import { Button } from '../ui/button';
import { useChatStore } from '@/stores/chat-store';
import { useNewChat } from '@/hooks/use-new-chat';

interface TopicShiftSuggestionProps {
  queryId: string;
  /** The original question that triggered the suggestion — re-sent verbatim
   *  (suppressing the nudge) on "Continue here", or prefilled into the fresh
   *  chat's input on "Start new chat". */
  query: string;
  onSendMessage?: (message: string, suppressTopicShift?: boolean) => void;
}

/**
 * Soft, dismissible suggestion shown when the classifier thinks the user's
 * question opens a topic unrelated to the current conversation. Offers two
 * actions — start a fresh chat, or continue right here — plus a dismiss (X).
 * It never blocks: dismissing leaves the user free to type anything next.
 *
 * The nudge fires at most once per topic. "Continue here" re-sends the original
 * question with suppressTopicShift=true (still classified, so an ambiguous
 * question is disambiguated — only TOPIC_SHIFT is gated). Dismiss arms a
 * one-shot store flag so the user's very next send is likewise suppressed,
 * preventing the just-declined nudge from re-firing.
 */
export function TopicShiftSuggestion({
  queryId,
  query,
  onSendMessage,
}: TopicShiftSuggestionProps) {
  const queryOrder = useChatStore(s => s.queryOrder);
  const chatState = useChatStore(s => s.chatState);
  const clearQuerySuggestion = useChatStore(s => s.clearQuerySuggestion);
  const setDraftMessage = useChatStore(s => s.setDraftMessage);
  const setSuppressTopicShiftOnNextSend = useChatStore(
    s => s.setSuppressTopicShiftOnNextSend
  );
  const startNewChat = useNewChat();

  // Only the latest turn's suggestion is actionable; older ones are inert.
  const isLastQuery = queryOrder[queryOrder.length - 1] === queryId;
  const disabled = !isLastQuery || chatState !== 'idle' || !onSendMessage;

  const handleContinueHere = () => {
    if (disabled) return;
    clearQuerySuggestion(queryId);
    onSendMessage?.(query, true);
  };

  const handleStartNewChat = () => {
    if (disabled) return;
    // Prefill the fresh chat's input with the question that triggered the
    // nudge, so the user can send or edit it without retyping.
    startNewChat();
    setDraftMessage(query);
  };

  const handleDismiss = () => {
    clearQuerySuggestion(queryId);
    // The user declined the nudge for this topic — don't re-raise it on their
    // very next question. Consumed (reset) on that send.
    setSuppressTopicShiftOnNextSend(true);
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
        className="mt-4 rounded-xl border border-border bg-muted/40 p-4"
      >
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Sparkles className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">
              This looks like a new topic
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Starting a fresh chat can keep answers focused and accurate — or
              you can continue right here.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                variant="default"
                size="sm"
                disabled={disabled}
                onClick={handleStartNewChat}
              >
                <MessageSquarePlus className="h-4 w-4" />
                Start new chat
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={handleContinueHere}
              >
                Continue here
                <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <button
            type="button"
            aria-label="Dismiss suggestion"
            onClick={handleDismiss}
            className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
