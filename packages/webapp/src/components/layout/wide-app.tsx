'use client';

import { DocumentList } from '@/components/documents/document-list';
import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { useChatStore } from '@/stores/chat-store';
import type { ResourceItem } from '@/stores/types';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { Toaster } from '@/components/ui/sonner';
import { ShaderBackground } from '@/components/ui/shader-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function WideApp() {
  const { currentQueryId, queries } = useChatStore();
  const { sendMessage } = useWebSocketChat(stableConfig);

  const selectedQuery = currentQueryId ? queries[currentQueryId] : null;
  const items: ResourceItem[] = selectedQuery?.resources || [];

  return (
    <>
      <ShaderBackground />
      <div className="flex justify-center">
        <div className="grid h-screen grid-cols-[minmax(0,64rem)_400px] gap-6 p-32">
          {/* Chat Container - Main Content Area */}
          <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border bg-card shadow-sm">
            <div className="min-h-0 flex-1">
              <ChatContainer variant="wide" />
            </div>
            <ChatInput
              placeholder="Ask a Wisconsin tax or revenue question..."
              onSendMessage={sendMessage}
            />
          </div>

          {/* Documents List - Sidebar */}
          <div className="min-h-0">
            <DocumentList title="Documents" items={items} />
          </div>
        </div>
      </div>
      <Toaster />
    </>
  );
}
