'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { ChatSkeleton } from '@/components/messages/chat-skeleton';
import { SessionsSidebar } from '@/components/layout/sessions-sidebar';
import { useChatStore } from '@/stores/chat-store';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { useSessionResume } from '@/hooks/use-session-resume';
import { useSessionUrlSync } from '@/hooks/use-session-url-sync';
import { Toaster } from '@/components/ui/sonner';
import { GradientBackground } from '@/components/ui/gradient-background';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatErrorProvider } from '@/components/errors/use-chat-error';
import { ProtectedRoute } from '@/components/auth/protected-route';

const queryClient = new QueryClient();

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export default function App() {
  return (
    <ProtectedRoute>
      <ChatErrorProvider>
        <QueryClientProvider client={queryClient}>
          <AppShell />
        </QueryClientProvider>
      </ChatErrorProvider>
    </ProtectedRoute>
  );
}

function AppShell() {
  const { loading } = useSessionResume();
  useSessionUrlSync();
  const switchingSession = useChatStore(state => state.switchingSession);
  const queryOrder = useChatStore(state => state.queryOrder);
  const isEmpty = queryOrder.length === 0;
  const { sendMessage } = useWebSocketChat(stableConfig);

  return (
    <>
      <GradientBackground />
      <div className="flex h-screen w-screen overflow-hidden">
        <SessionsSidebar />

        <div className="flex min-w-0 flex-1" style={{ contain: 'layout paint' }}>
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {loading || switchingSession ? (
              <ChatSkeleton />
            ) : isEmpty ? (
              <div className="flex flex-1 flex-col items-center justify-center">
                <div className="mb-6 text-center">
                  <h2 className="text-xl font-medium text-foreground">
                    Wisconsin Property Tax Assistant
                  </h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Ask a question about Wisconsin property tax law, assessments, or appeals.
                  </p>
                </div>
                <div className="w-full max-w-2xl px-4">
                  <ChatInput
                    placeholder="Ask a Wisconsin tax or revenue question..."
                    onSendMessage={sendMessage}
                  />
                </div>
              </div>
            ) : (
              <>
                <div className="min-h-0 flex-1">
                  <ChatContainer />
                </div>
                <div className="pointer-events-none absolute inset-x-0 bottom-0">
                  <ChatInput
                    placeholder="Ask a Wisconsin tax or revenue question..."
                    onSendMessage={sendMessage}
                  />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      <Toaster />
    </>
  );
}
