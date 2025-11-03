'use client';

import { DocumentList } from '@/components/documents/document-list';
import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { useChatStore } from '@/stores/chat-store';
import type { Document, DocumentsContent } from '@/stores/types';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { Toaster } from '@/components/ui/sonner';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function WideApp() {
  const { currentQueryId, queries } = useChatStore();
  const { sendMessage } = useWebSocketChat(stableConfig);

  const selectedQuery = currentQueryId ? queries[currentQueryId] : null;
  const documents: Document[] =
    selectedQuery?.resources?.type === 'documents'
      ? (selectedQuery.resources.content as DocumentsContent).documents
      : [];

  return (
    <>
      <div className="flex justify-center">
        <div className="bg-background grid h-screen grid-cols-[minmax(0,64rem)_400px] gap-6 p-32">
          {/* Chat Container - Main Content Area */}
          <div className="relative min-h-0">
            <ChatContainer variant="wide" />

            {/* Overlaid Chat Input */}
            <div className="absolute bottom-12 left-1/2 z-20 w-3/4 max-w-2xl -translate-x-1/2 transform">
              <ChatInput
                placeholder="Ask me anything."
                onSendMessage={sendMessage}
              />
            </div>
          </div>

          {/* Documents List - Sidebar */}
          <div className="min-h-0">
            <DocumentList title="Documents" documents={documents} />
          </div>
        </div>
      </div>
      <Toaster />
    </>
  );
}
