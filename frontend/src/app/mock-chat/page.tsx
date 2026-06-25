'use client';

import { ChatContainer } from '@/components/messages/chat-container';
import { ChatInput } from '@/components/messages/chat-input';
import { useChatStore } from '@/stores/chat-store';
import type { ResourceItem, Query, AgentTraceEvent } from '@/stores/types';
import { useCallback, useEffect, useRef } from 'react';
import { GradientBackground } from '@/components/ui/gradient-background';
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

const MOCK_AGENT_TRACE: AgentTraceEvent[] = [
  { kind: 'loop_start', seq: 0, timestamp: 1, payload: {} },
  // FAQ Search
  { kind: 'tool_call', seq: 1, timestamp: 2, payload: { toolName: 'faq_search', summary: 'property assessment methods', status: 'pending' } },
  { kind: 'tool_result', seq: 2, timestamp: 3, payload: { toolName: 'faq_search', summary: 'Found 5 FAQ matches', status: 'ok', metadata: { faqCount: 5, topScore: 0.82, faqScoreThreshold: 0.70, faqScores: [0.82, 0.78, 0.74, 0.65, 0.61] } } },
  // Vector Search
  { kind: 'tool_call', seq: 3, timestamp: 4, payload: { toolName: 'vector_search', summary: 'property assessment full value', status: 'pending' } },
  { kind: 'tool_result', seq: 4, timestamp: 5, payload: { toolName: 'vector_search', summary: 'Retrieved 12 chunks', status: 'ok', metadata: { preDedupCount: 25, chunkCount: 12, targetWpamYear: 2024, authorityBreakdown: { '2': 3, '3': 2, '4': 1, '5': 4, '6': 2 }, scoreBuckets: { '0.9+': 2, '0.8-0.9': 5, '0.7-0.8': 3, '<0.7': 2 }, caseLawCount: 2, autoEnrichedCount: 3, docChunks: { 'statutes-70-32': 5, 'wpam-ch9-income-approach': 5, 'case-law-markarian': 2, 'admin-rules-tax-11': 3, 'wpam-ch7-sales-comparison': 5, 'gov-pubs-guide-assessors': 2, 'statutes-70-34': 3 } } } },
  // Graph Expansion
  { kind: 'tool_call', seq: 5, timestamp: 6, payload: { toolName: 'get_neighbors', summary: 'statute-70-32', status: 'pending' } },
  { kind: 'tool_result', seq: 6, timestamp: 7, payload: { toolName: 'get_neighbors', summary: 'Found 8 neighbors', status: 'ok', metadata: { neighborCount: 8, relationshipCounts: { 'CITES': 4, 'IMPLEMENTS': 2, 'COVERS_TOPIC': 3, 'RELATED_TO': 1 } } } },
  { kind: 'tool_call', seq: 7, timestamp: 8, payload: { toolName: 'get_authority_chain', summary: 'statute-70-32', status: 'pending' } },
  { kind: 'tool_result', seq: 8, timestamp: 9, payload: { toolName: 'get_authority_chain', summary: 'Chain length 4', status: 'ok', metadata: { chainLength: 4 } } },
  // Specialized Retrieval
  { kind: 'tool_call', seq: 9, timestamp: 10, payload: { toolName: 'search_document', summary: 'WPAM Chapter 9', status: 'pending' } },
  { kind: 'tool_result', seq: 10, timestamp: 11, payload: { toolName: 'search_document', summary: 'Searched WPAM Chapter 9', status: 'ok', metadata: { docId: 'wpam-ch9', chunkCount: 6, elapsedMs: 340 } } },
  { kind: 'tool_call', seq: 11, timestamp: 12, payload: { toolName: 'fetch_case_opinion', summary: 'Markarian v. City of Cudahy', status: 'pending' } },
  { kind: 'tool_result', seq: 12, timestamp: 13, payload: { toolName: 'fetch_case_opinion', summary: 'Fetched opinion', status: 'ok', metadata: { docId: 'case-law-markarian', elapsedMs: 520 } } },
  { kind: 'tool_call', seq: 13, timestamp: 14, payload: { toolName: 'get_document', summary: 'admin-rules-tax-18', status: 'pending' } },
  { kind: 'tool_result', seq: 14, timestamp: 15, payload: { toolName: 'get_document', summary: 'Document not found', status: 'miss', metadata: {} } },
  // Synthesis
  { kind: 'reasoning', seq: 15, timestamp: 16, payload: { text: 'Synthesizing answer from 12 chunks across 5 sources' } },
  { kind: 'tool_call', seq: 16, timestamp: 17, payload: { toolName: 'answer', summary: 'Generating response', status: 'pending' } },
  { kind: 'tool_result', seq: 17, timestamp: 18, payload: { toolName: 'answer', summary: 'Answer generated', status: 'ok', metadata: {} } },
  { kind: 'loop_complete', seq: 18, timestamp: 19, payload: { elapsedMs: 4200, citedDocCount: 4, discoveryCounts: { 'vector-search': 5, 'graph-neighbor': 3, 'fetched': 1 }, discoveryTitles: { 'statute-70-32': 'Wisconsin Statute §70.32', 'wpam-ch9': 'WPAM Chapter 9 — Income Approach', 'case-law-markarian': 'Markarian v. City of Cudahy', 'admin-rules-tax-11': 'Admin Rule Tax 11.01' } } },
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
    addQuery,
    setCurrentQueryId,
    updateQueryStatus,
    appendQueryResponse,
    updateQueryResources,
    appendAgentTraceEvent,
    setChatState,
  } = useChatStore();

  useEffect(() => {
    localStorage.setItem('wisco:devTrace', '1');

    // Pre-populate a completed query with full trace on mount for fast iteration
    const queryId = 'mock-preload';
    const preloadQuery: Query = {
      query: 'How is real property assessed under Wisconsin law?',
      queryId,
      type: 'outbound',
      timestamp: new Date().toISOString(),
      status: 'completed',
      response: { type: 'stream', content: MOCK_RESPONSE },
      resources: MOCK_DOCUMENTS,
    };
    addQuery(preloadQuery);
    setCurrentQueryId(queryId);
    for (const event of MOCK_AGENT_TRACE) {
      appendAgentTraceEvent(queryId, event);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

      // Drip-feed agent trace events to simulate the retrieval pipeline
      MOCK_AGENT_TRACE.forEach((event, i) => {
        timeoutsRef.current.push(
          setTimeout(() => {
            appendAgentTraceEvent(queryId, event);
          }, 800 + i * 250)
        );
      });

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

          // Stream token-by-token (3-8 chars per chunk, 15-25ms apart)
          // to simulate real LLM streaming cadence
          const chunks: string[] = [];
          let i = 0;
          while (i < MOCK_RESPONSE.length) {
            const chunkSize = 3 + Math.floor(Math.random() * 6);
            chunks.push(MOCK_RESPONSE.slice(i, i + chunkSize));
            i += chunkSize;
          }
          chunks.forEach((chunk, idx) => {
            timeoutsRef.current.push(
              setTimeout(() => {
                appendQueryResponse(queryId, chunk);
                if (idx === chunks.length - 1) {
                  updateQueryStatus(queryId, 'completed');
                  setChatState('idle');
                }
              }, idx * 20)
            );
          });
        }, 6000)
      );
    },
    [
      addQuery,
      setCurrentQueryId,
      updateQueryResources,
      updateQueryStatus,
      appendQueryResponse,
      appendAgentTraceEvent,
      setChatState,
    ]
  );

  return (
    <ChatErrorProvider>
      <QueryClientProvider client={queryClient}>
        <GradientBackground />
        <div className="flex h-screen w-screen overflow-hidden">
          {/* Mock sidebar */}
          <div className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-card">
            <div className="flex items-center gap-2 border-b border-border px-4 py-4">
              <div className="h-7 w-7 rounded bg-primary/20" />
              <div className="text-sm font-semibold">WisDOR</div>
            </div>
            <div className="flex-1 space-y-2 p-3">
              <div className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">Mock session</div>
            </div>
          </div>
          <div className="flex min-w-0 flex-1" style={{ contain: 'layout paint' }}>
            <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
              <div className="min-h-0 flex-1">
                <ChatContainer onSendMessage={sendMessage} />
              </div>
              <div className="pointer-events-none absolute inset-x-0 bottom-0">
                <ChatInput
                  placeholder="Ask me anything (mock mode)."
                  onSendMessage={sendMessage}
                />
              </div>
            </div>
          </div>
        </div>
      </QueryClientProvider>
    </ChatErrorProvider>
  );
}
