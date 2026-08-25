'use client';

import { useCallback } from 'react';
import { toast } from 'sonner';
import { useChatStore } from '@/stores/chat-store';
import { useFeedbackStore } from '@/stores/feedback-store';
import { useSessionsList } from '@/hooks/api/chat';

/**
 * Shared "start a new chat" action, used by both the sessions sidebar and the
 * topic-shift suggestion. Stashes the current session, clears the in-memory
 * history, resets store state, refetches the sessions list, and marks the
 * next session as an explicit new chat (read by the chat page on mount).
 *
 * Returns a callback; it no-ops (with a toast) while annotation mode is active,
 * matching the sidebar's navigation lock.
 */
export function useNewChat(): () => void {
  const currentSessionId = useChatStore(state => state.sessionId);
  const clearHistory = useChatStore(state => state.clearHistory);
  const reset = useChatStore(state => state.reset);
  const stashSession = useChatStore(state => state.stashSession);
  const { refetch } = useSessionsList();

  return useCallback(() => {
    if (useFeedbackStore.getState().annotatingQueryId !== null) {
      toast.info('Finish or exit annotation mode first');
      return;
    }
    if (currentSessionId) {
      stashSession(currentSessionId);
    }
    clearHistory();
    reset();
    refetch();
    sessionStorage.setItem('explicit-new-chat', 'true');
  }, [currentSessionId, stashSession, clearHistory, reset, refetch]);
}
