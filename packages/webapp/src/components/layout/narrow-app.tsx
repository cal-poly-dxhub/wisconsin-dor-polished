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
        <div className="relative h-full w-full">
          <ChatContainer variant="narrow" />

          {/* Overlaid Chat Input */}
          <div className="absolute bottom-24 left-1/2 z-20 w-3/4 max-w-2xl -translate-x-1/2 transform">
            <ChatInput
              placeholder="Type your message..."
              onSendMessage={sendMessage}
            />
          </div>
        </div>
      </div>
      <Toaster />
    </>
  );
}
