'use client';

import { DocumentList } from '@/components/documents/document-list';
import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { SessionsSidebar } from '@/components/layout/sessions-sidebar';
import { useChatStore } from '@/stores/chat-store';
import type { ResourceItem } from '@/stores/types';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { Toaster } from '@/components/ui/sonner';
import { GradientBackground } from '@/components/ui/gradient-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function WideApp() {
  const selectedQuery = useChatStore(state =>
    state.currentQueryId ? state.queries[state.currentQueryId] : null
  );
  const items: ResourceItem[] = selectedQuery?.resources || [];
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
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1">
              <ChatContainer variant="wide" />
            </div>
            <ChatInput
              placeholder="Ask a Wisconsin tax or revenue question..."
              onSendMessage={sendMessage}
            />
          </div>

          {/* Documents List - Right Sidebar */}
          <aside className="flex h-full w-[400px] flex-col border-l border-border bg-muted/20 p-5">
            <DocumentList title="Sources" items={items} />
          </aside>
        </div>
      </div>
      <Toaster />
    </>
  );
}
