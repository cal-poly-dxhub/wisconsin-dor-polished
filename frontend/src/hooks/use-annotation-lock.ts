'use client';

import { useEffect } from 'react';
import { useFeedbackStore } from '@/stores/feedback-store';

// While annotation mode is active, warn on tab close / reload so in-progress
// annotations aren't lost. Sidebar navigation is guarded separately in
// sessions-sidebar.tsx by reading annotatingQueryId directly.
export function useAnnotationLock(): boolean {
  const annotatingQueryId = useFeedbackStore((s) => s.annotatingQueryId);
  const isAnnotating = annotatingQueryId !== null;

  useEffect(() => {
    if (!isAnnotating) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isAnnotating]);

  return isAnnotating;
}
