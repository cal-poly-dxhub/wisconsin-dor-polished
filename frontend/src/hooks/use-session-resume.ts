import { useEffect, useRef, useCallback, useState } from 'react';
import { useChatStore } from '@/stores/chat-store';
import { getSessions, getSessionHistory } from '@/api/chat-api';
import type { Query } from '@/stores/types';

const POLL_INTERVAL_MS = 3000;
const MAX_POLL_ATTEMPTS = 40; // ~2 minutes max

export function useSessionResume(): { loading: boolean } {
  const [loading, setLoading] = useState(true);
  const sessionId = useChatStore(s => s.sessionId);
  const addQuery = useChatStore(s => s.addQuery);
  const setSessionId = useChatStore(s => s.setSessionId);
  const clearHistory = useChatStore(s => s.clearHistory);

  const hasResumedRef = useRef(false);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    pollCountRef.current = 0;
  }, []);

  const pollForAnswer = useCallback(
    (targetSessionId: string, queryId: string) => {
      pollCountRef.current = 0;

      pollIntervalRef.current = setInterval(async () => {
        pollCountRef.current += 1;
        if (pollCountRef.current > MAX_POLL_ATTEMPTS) {
          useChatStore.getState().updateQueryStatus(queryId, 'completed');
          useChatStore.getState().setChatState('idle');
          stopPolling();
          return;
        }

        const currentStatus =
          useChatStore.getState().queries[queryId]?.status;
        if (currentStatus === 'streaming' || currentStatus === 'completed') {
          useChatStore.getState().setChatState('idle');
          stopPolling();
          return;
        }

        try {
          const history = await getSessionHistory(targetSessionId);
          const targetMsg = history.messages.find(
            m => m.queryId === queryId
          );

          if (targetMsg?.answer && targetMsg.answer.trim() !== '') {
            useChatStore.getState().updateQueryResponse(queryId, targetMsg.answer);
            useChatStore.getState().updateQueryStatus(queryId, 'completed');
            useChatStore.getState().setChatState('idle');
            stopPolling();
          }
        } catch {
          // Transient error — keep polling
        }
      }, POLL_INTERVAL_MS);
    },
    [stopPolling]
  );

  useEffect(() => {
    if (hasResumedRef.current || sessionId !== null) return;
    hasResumedRef.current = true;

    const resume = async () => {
      try {
        const { sessions } = await getSessions();
        if (!sessions || sessions.length === 0) return;

        const mostRecent = sessions[0];
        const history = await getSessionHistory(mostRecent.sessionId);

        if (!history.messages || history.messages.length === 0) {
          setSessionId(mostRecent.sessionId);
          return;
        }

        clearHistory();
        history.messages.forEach(msg => {
          const isPending = !msg.answer || msg.answer.trim() === '';
          const query: Query = {
            queryId: msg.queryId,
            query: msg.query,
            type: 'outbound',
            timestamp: msg.timestamp || new Date().toISOString(),
            status: isPending ? 'pending' : 'completed',
            response: {
              type: 'stream',
              content: msg.answer || '',
            },
            resources: msg.resources,
          };
          addQuery(query);
        });

        setSessionId(mostRecent.sessionId);

        const lastMsg = history.messages[history.messages.length - 1];
        if (!lastMsg.answer || lastMsg.answer.trim() === '') {
          useChatStore.getState().setCurrentQueryId(lastMsg.queryId);
          useChatStore.getState().setChatState('waiting_for_response');
          pollForAnswer(mostRecent.sessionId, lastMsg.queryId);
        }
      } catch {
        // Fail silently — user can start a new session
      } finally {
        setLoading(false);
      }
    };

    resume();

    return () => {
      stopPolling();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [sessionId, stopPolling]);

  return { loading };
}
