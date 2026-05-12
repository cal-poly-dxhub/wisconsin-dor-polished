'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { ChatSkeleton } from '@/components/messages/chat-skeleton';
import { SessionsSidebar } from '@/components/layout/sessions-sidebar';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { useSessionResume } from '@/hooks/use-session-resume';
import { Toaster } from '@/components/ui/sonner';
import { GradientBackground } from '@/components/ui/gradient-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function NarrowApp() {
  const { loading } = useSessionResume();
  const { sendMessage } = useWebSocketChat(stableConfig);

  return (
    <>
      <GradientBackground />
      <div className="flex h-screen w-screen overflow-hidden">
        {/* Left Sidebar - Sessions */}
        <SessionsSidebar />

        {/* Main Content Area */}
        <div className="relative flex min-w-0 flex-1 flex-col">
          {loading ? (
            <ChatSkeleton />
          ) : (
            <>
              <div className="min-h-0 flex-1">
                <ChatContainer variant="narrow" />
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
      <Toaster />
    </>
  );
}
