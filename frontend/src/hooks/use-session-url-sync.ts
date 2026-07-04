import { useEffect } from 'react';
import { useChatStore } from '@/stores/chat-store';

export function useSessionUrlSync(): void {
  const sessionId = useChatStore(s => s.sessionId);

  useEffect(() => {
    const url = new URL(window.location.href);

    if (sessionId) {
      url.searchParams.set('session', sessionId);
      sessionStorage.removeItem('explicit-new-chat');
    } else {
      url.searchParams.delete('session');
    }

    if (url.toString() !== window.location.href) {
      window.history.replaceState(null, '', url.toString());
    }
  }, [sessionId]);
}
