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
        <div className="h-screen w-full max-w-[calc(66rem+400px)] p-20 pt-24">
          <div className="grid h-full min-h-0 grid-cols-[minmax(0,66rem)_400px] overflow-hidden rounded-lg border bg-card shadow-sm">
            {/* Chat Container - Main Content Area */}
            <div className="flex min-h-0 flex-col overflow-hidden">
              <div className="min-h-0 flex-1">
                <ChatContainer variant="wide" />
              </div>
              <ChatInput
                placeholder="Ask a Wisconsin tax or revenue question..."
                onSendMessage={sendMessage}
              />
            </div>

            {/* Documents List - Sidebar */}
            <aside className="min-h-0 border-l bg-muted/20 p-5">
              <DocumentList title="Sources" items={items} />
            </aside>
          </div>
        </div>
      </div>
      <Toaster />
    </>
  );
}
