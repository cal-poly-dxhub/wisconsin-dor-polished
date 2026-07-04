/**
 * Component providing standardized error handling function.
 */

import * as React from 'react';
import { createContext, useCallback, useContext, useMemo } from 'react';
import { ChatError } from '@/components/errors/chat-error';
import { toast } from 'sonner';

interface ChatErrorContextValue {
  handleError: (error: ChatError) => void;
}

const ChatErrorContext = createContext<ChatErrorContextValue | undefined>(
  undefined
);

interface ChatErrorContextProps {
  children: React.ReactNode;
}

export function ChatErrorProvider({ children }: ChatErrorContextProps) {
  const handleError = useCallback((error: ChatError) => {
    toast.error(error.userMessage, {
      position: 'top-center',
      duration: undefined,
    });
  }, []);

  const value: ChatErrorContextValue = useMemo(
    () => ({
      handleError,
    }),
    [handleError]
  );

  return (
    <ChatErrorContext.Provider value={value}>
      {children}
    </ChatErrorContext.Provider>
  );
}

// Should be used within ChatErrorProvider
export function useChatError() {
  const context = useContext(ChatErrorContext);
  if (!context) {
    throw new Error('useChatError must be used within a ChatErrorProvider');
  }
  return context;
}
