'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { SessionsSidebar } from '@/components/layout/sessions-sidebar';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { Toaster } from '@/components/ui/sonner';
import { GradientBackground } from '@/components/ui/gradient-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function NarrowApp() {
  const { sendMessage } = useWebSocketChat(stableConfig);

  return (
    <>
      <GradientBackground />
      <div className="flex h-screen w-screen overflow-hidden">
        {/* Left Sidebar - Sessions */}
        <SessionsSidebar />

        {/* Main Content Area */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            <ChatContainer variant="narrow" />
          </div>
          <ChatInput
            placeholder="Ask a Wisconsin tax or revenue question..."
            onSendMessage={sendMessage}
          />
        </div>
      </div>
      <Toaster />
    </>
  );
}
