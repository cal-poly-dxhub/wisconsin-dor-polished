'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { Toaster } from '@/components/ui/sonner';
import { ShaderBackground } from '@/components/ui/shader-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function NarrowApp() {
  const { sendMessage } = useWebSocketChat(stableConfig);

  return (
    <>
      <ShaderBackground />
      <div className="h-screen w-full p-4">
        <div className="flex h-full flex-col overflow-hidden rounded-lg border bg-card shadow-sm">
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
