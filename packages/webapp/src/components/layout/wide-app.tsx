'use client';

import { useState } from 'react';
import { DocumentList } from '@/components/documents/document-list';
import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { SessionsSidebar } from '@/components/layout/sessions-sidebar';
import { useChatStore } from '@/stores/chat-store';
import type { ResourceItem } from '@/stores/types';
import { useWebSocketChat } from '@/hooks/use-websocket-chat';
import { useSessionResume } from '@/hooks/use-session-resume';
import { Toaster } from '@/components/ui/sonner';
import { GradientBackground } from '@/components/ui/gradient-background';

const stableConfig = {
  websocketUrl: process.env.NEXT_PUBLIC_WEBSOCKET_URL!,
};

export function WideApp() {
  useSessionResume();
  const queryOrder = useChatStore(state => state.queryOrder);
  const queries = useChatStore(state => state.queries);
  const isEmpty = queryOrder.length === 0;
  const items: ResourceItem[] = queryOrder.reduce<ResourceItem[]>((acc, id) => {
    const q = queries[id];
    if (q?.resources) acc.push(...q.resources);
    return acc;
  }, []);
  const hasSources = items.length > 0;
  const { sendMessage } = useWebSocketChat(stableConfig);

  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const [prevHasSources, setPrevHasSources] = useState(false);

  // Auto-expand when sources first arrive
  if (hasSources && !prevHasSources) {
    setSourcesExpanded(true);
  }
  if (hasSources !== prevHasSources) {
    setPrevHasSources(hasSources);
  }

  return (
    <>
      <GradientBackground />
      <div className="flex h-screen w-screen overflow-hidden">
        {/* Left Sidebar - Sessions */}
        <SessionsSidebar />

        {/* Main Content Area */}
        <div className="flex min-w-0 flex-1">
          {/* Chat Container - Main Content Area */}
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            <div className={`min-h-0 transition-all duration-500 ease-in-out ${isEmpty ? 'flex-none' : 'flex-1'}`}>
              {!isEmpty && <ChatContainer variant="wide" />}
            </div>
            <div
              className={`flex flex-col items-center transition-all duration-500 ease-in-out ${
                isEmpty ? 'flex-1 justify-center' : 'flex-none'
              }`}
            >
              {isEmpty && (
                <div className="mb-6 text-center">
                  <h2 className="text-xl font-medium text-foreground">
                    Wisconsin Property Tax Assistant
                  </h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Ask a question about Wisconsin property tax law, assessments, or appeals.
                  </p>
                </div>
              )}
              <div className="w-full max-w-2xl px-4">
                <ChatInput
                  placeholder="Ask a Wisconsin tax or revenue question..."
                  onSendMessage={sendMessage}
                />
              </div>
            </div>
          </div>

          {/* Sources Sidebar */}
          <aside
            className={`flex h-full flex-col border-l border-border bg-muted/20 transition-all duration-300 ease-in-out ${
              sourcesExpanded ? 'w-[400px]' : 'w-[52px]'
            }`}
          >
            {/* Toggle button */}
            <div className="flex items-center justify-center px-3 py-3 w-[52px]">
              <button
                onClick={() => setSourcesExpanded(!sourcesExpanded)}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
                aria-label={sourcesExpanded ? 'Collapse sources' : 'Expand sources'}
              >
                <svg
                  width="20"
                  height="20"
                  viewBox="0 0 16 16"
                  fill="none"
                >
                  <path
                    d="M2 3.5C3.5 3 5.5 3 8 4.5C10.5 3 12.5 3 14 3.5V12.5C12.5 12 10.5 12 8 13.5C5.5 12 3.5 12 2 12.5V3.5Z"
                    stroke="currentColor"
                    strokeWidth="1.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <path
                    d="M8 4.5V13.5"
                    stroke="currentColor"
                    strokeWidth="1.2"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
            {/* Content */}
            <div className={`flex-1 overflow-hidden transition-opacity duration-300 ${sourcesExpanded ? 'opacity-100 px-5 pb-5' : 'opacity-0'}`}>
              <DocumentList title="Sources" items={items} />
            </div>
          </aside>
        </div>
      </div>
      <Toaster />
    </>
  );
}
