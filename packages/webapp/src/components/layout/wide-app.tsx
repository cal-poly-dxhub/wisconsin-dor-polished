'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { ChatSkeleton } from '@/components/messages/chat-skeleton';
import { SessionsSidebar } from '@/components/layout/sessions-sidebar';
import { useChatStore } from '@/stores/chat-store';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { useSessionResume } from '@/hooks/use-session-resume';
import { Toaster } from '@/components/ui/sonner';
import { GradientBackground } from '@/components/ui/gradient-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function WideApp() {
  const { loading } = useSessionResume();
  const queryOrder = useChatStore(state => state.queryOrder);
  const isEmpty = queryOrder.length === 0;
  const { sendMessage } = useWebSocketChat(stableConfig);

  return (
    <>
      <GradientBackground />
      <div className="flex h-screen w-screen overflow-hidden">
        {/* Left Sidebar - Sessions */}
        <SessionsSidebar />

        {/* Main Content Area */}
        <div className="flex min-w-0 flex-1">
          {/* Chat Container - Main Content Area */}
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {loading ? (
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
                  <ChatContainer variant="wide" />
                </div>
                <div className="absolute inset-x-0 bottom-0">
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
