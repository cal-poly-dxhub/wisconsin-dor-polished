'use client';

import { DocumentList } from '@/components/documents/document-list';
import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { useChatStore } from '@/stores/chat-store';
import type { ResourceItem, Query } from '@/stores/types';
import { useCallback, useRef } from 'react';
import { ShaderBackground } from '@/components/ui/shader-background';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatErrorProvider } from '@/components/errors/use-chat-error';

const queryClient = new QueryClient();

const MOCK_DOCUMENTS: ResourceItem[] = [
  {
    type: 'document',
    data: {
      documentId: 'statute-70-32',
      title: 'Wisconsin Statute §70.32 — Assessment of Real Property',
      content:
        'Real property shall be valued by the assessor in the manner specified in the Wisconsin property assessment manual provided under s. 73.03(2a) from actual view or from the best information that the assessor can practicably obtain, at the full value which could ordinarily be obtained therefor at private sale.',
      source: 'WI Statutes',
      authorityLevel: 2,
      discoveryTag: 'vector-search' as const,
    },
  },
  {
    type: 'document',
    data: {
      documentId: 'wpam-ch9',
      title: 'WPAM Chapter 9 — Income Approach to Value',
      content:
        'The income approach to value is based on the principle that the value of a property is indicated by the net return to the owner, or the earning power of the property over a given period of time.',
      source: 'WPAM',
      authorityLevel: 5,
      discoveryTag: 'graph-neighbor' as const,
    },
  },
  {
    type: 'document',
    data: {
      documentId: 'case-law-markarian',
      title: 'State ex rel. Markarian v. City of Cudahy',
      content:
        'The court held that the uniformity clause of the Wisconsin Constitution requires that all property be assessed at full value, and that systematic underassessment violates this constitutional mandate.',
      source: 'Case Law',
      authorityLevel: 3,
      discoveryTag: 'fetched' as const,
    },
  },
];

const MOCK_RESPONSE = `Under **Wisconsin Statute §70.32**, all real property must be assessed at its **full value** — defined as the amount that could ordinarily be obtained at private sale.

The assessor is required to value property either from actual view or from the best information practicably obtainable, following the methods outlined in the **Wisconsin Property Assessment Manual (WPAM)**.

## Key Assessment Approaches

The WPAM recognizes three primary approaches to value:

1. **Sales Comparison Approach** — compares the subject property to recent sales of similar properties
2. **Income Approach** — estimates value based on the property's net income-producing capability (WPAM Chapter 9)
3. **Cost Approach** — estimates the cost to reproduce or replace the improvements, minus depreciation

The Wisconsin Supreme Court in *State ex rel. Markarian v. City of Cudahy* reinforced that the **uniformity clause** of the Wisconsin Constitution requires all property to be assessed at full value, and that systematic underassessment violates this constitutional mandate.

### Sources
- Wisconsin Statute §70.32
- WPAM Chapter 9
- *Markarian v. City of Cudahy*`;

export default function MockChatPage() {
  const {
    currentQueryId,
    queries,
    addQuery,
    setCurrentQueryId,
    updateQueryStatus,
    appendQueryResponse,
    updateQueryResources,
    setChatState,
  } = useChatStore();

  const selectedQuery = currentQueryId ? queries[currentQueryId] : null;
  const items: ResourceItem[] = selectedQuery?.resources || [];
  const timeoutsRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const sendMessage = useCallback(
    async (message: string) => {
      // Clear any pending timeouts from previous messages
      timeoutsRef.current.forEach(clearTimeout);
      timeoutsRef.current = [];

      const queryId = `mock-${Date.now()}`;
      const query: Query = {
        query: message,
        queryId,
        type: 'outbound',
        timestamp: new Date().toISOString(),
        status: 'pending',
        response: { type: 'stream', content: '' },
      };
      addQuery(query);
      setCurrentQueryId(queryId);

      // Simulate backend delay, then stream documents + response
      timeoutsRef.current.push(
        setTimeout(() => {
          updateQueryResources(queryId, MOCK_DOCUMENTS);
        }, 4500)
      );

      timeoutsRef.current.push(
        setTimeout(() => {
          updateQueryStatus(queryId, 'streaming');
          setChatState('streaming');

          // Stream the response word by word
          const words = MOCK_RESPONSE.split(' ');
          words.forEach((word, i) => {
            timeoutsRef.current.push(
              setTimeout(() => {
                appendQueryResponse(queryId, (i === 0 ? '' : ' ') + word);
                if (i === words.length - 1) {
                  updateQueryStatus(queryId, 'completed');
                  setChatState('idle');
                }
              }, i * 30)
            );
          });
        }, 5000)
      );
    },
    [
      addQuery,
      setCurrentQueryId,
      updateQueryResources,
      updateQueryStatus,
      appendQueryResponse,
      setChatState,
    ]
  );

  return (
    <ChatErrorProvider>
      <QueryClientProvider client={queryClient}>
        <ShaderBackground />
      <div className="flex justify-center">
        <div className="grid h-screen grid-cols-[minmax(0,64rem)_400px] gap-6 p-32">
          <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border shadow-sm">
            <div className="min-h-0 flex-1">
              <ChatContainer variant="wide" />
            </div>
            <ChatInput
              placeholder="Ask me anything (mock mode)."
              onSendMessage={sendMessage}
            />
          </div>
          <div className="min-h-0">
            <DocumentList title="Documents" items={items} />
          </div>
        </div>
      </div>
      </QueryClientProvider>
    </ChatErrorProvider>
  );
}
