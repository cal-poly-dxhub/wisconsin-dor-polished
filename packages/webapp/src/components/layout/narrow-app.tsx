'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function NarrowApp() {
  const { sendMessage } = useWebSocketChat(stableConfig);

  const handleSendMessage = (message: string) => {
    sendMessage({ message });
  };

  return (
    <div className="bg-background h-screen w-full p-4">
      <div className="relative h-full w-full">
        <ChatContainer variant="narrow" />

        {/* Top blur gradient overlay */}
        <div className="gradient-blur-narrow-top">
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
        </div>

        {/* Bottom blur gradient overlay */}
        <div className="gradient-blur-narrow">
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
          <div></div>
        </div>

        {/* Overlaid Chat Input */}
        <div className="absolute bottom-24 left-1/2 z-20 w-3/4 max-w-md -translate-x-1/2 transform">
          <ChatInput
            placeholder="Type your message..."
            onSendMessage={handleSendMessage}
          />
        </div>
      </div>
    </div>
  );
}
