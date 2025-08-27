'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';

export function NarrowApp() {
  return (
    <div className="bg-background h-screen w-full p-4">
      <div className="relative h-full w-full">
        <ChatContainer variant="narrow" />

        {/* Blur gradient overlay */}
        <div className="gradient-blur"></div>

        {/* Overlaid Chat Input */}
        <div className="absolute bottom-24 left-1/2 z-20 w-3/4 max-w-md -translate-x-1/2 transform">
          <ChatInput
            placeholder="Type your message..."
            onSendMessage={message => console.log('Message sent:', message)}
          />
        </div>
      </div>
    </div>
  );
}
