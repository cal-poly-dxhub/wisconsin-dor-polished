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
      discoveryTag: 'vector-search',
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
      discoveryTag: 'graph-neighbor',
    },
  },
  {
    type: 'document',
    data: {
      documentId: 'wpam-ch7',
      title: 'WPAM Chapter 7 — Sales Comparison Approach',
      content:
        'The sales comparison approach estimates value by comparing the subject property to similar properties that have recently sold, making adjustments for differences in characteristics.',
      source: 'WPAM',
      authorityLevel: 5,
      discoveryTag: 'fetched',
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
      discoveryTag: 'opinion-fetched',
    },
  },
  {
    type: 'document',
    data: {
      documentId: 'admin-rules-tax-11',
      title: 'Admin Rule Tax 11.01 — Assessment Standards',
      content:
        'The department of revenue shall prescribe the methods and standards to be used in the valuation of all property subject to general property taxation.',
      source: 'Admin Rules',
      authorityLevel: 4,
      discoveryTag: 'vector-search',
    },
  },
];

const MOCK_AGENT_TRACE: AgentTraceEvent[] = [
  { kind: 'loop_start', seq: 0, timestamp: 1, payload: {} },
  // Query Refinement
  { kind: 'tool_call', seq: 1, timestamp: 2, payload: { toolName: 'refine_query', summary: '"How is real property assessed under Wisconsin law?"', status: 'pending' } },
  { kind: 'tool_result', seq: 2, timestamp: 3, payload: { toolName: 'refine_query', summary: 'Refined to "Wisconsin real property assessment full value standard §70.32"', status: 'ok', metadata: { refined: true, refinedQuery: 'Wisconsin real property assessment full value standard §70.32', latencyMs: 280 } } },
  // FAQ Search
  { kind: 'tool_call', seq: 3, timestamp: 4, payload: { toolName: 'faq_search', summary: '"Wisconsin real property assessment full value standard"', status: 'pending' } },
  { kind: 'tool_result', seq: 4, timestamp: 5, payload: { toolName: 'faq_search', summary: 'FAQ semantic match score 0.82', status: 'ok', metadata: { faqCount: 5, topScore: 0.82, faqScoreThreshold: 0.70, faqScores: [0.82, 0.78, 0.74, 0.65, 0.61], topFaqSnippet: 'All real property in Wisconsin must be assessed at its full value, defined as the price it would bring at private sale. The assessor uses the Wisconsin Property Assessment Manual...' } } },
  // Vector Search
  { kind: 'tool_call', seq: 5, timestamp: 6, payload: { toolName: 'vector_search', summary: '"property assessment full value standard §70.32"', status: 'pending' } },
  { kind: 'tool_result', seq: 6, timestamp: 7, payload: { toolName: 'vector_search', summary: 'Found 25 chunks across 7 sources', status: 'ok', metadata: { preDedupCount: 25, chunkCount: 12, targetWpamYear: 2024, authorityBreakdown: { '2': 3, '3': 2, '4': 1, '5': 4, '6': 2 }, scoreBuckets: { '0.9+': 2, '0.8-0.9': 5, '0.7-0.8': 3, '<0.7': 2 }, caseLawCount: 2, autoEnrichedCount: 3, docChunks: { 'statutes-70-32': 5, 'wpam-ch9-income-approach': 5, 'case-law-markarian': 2, 'admin-rules-tax-11': 3, 'wpam-ch7-sales-comparison': 5, 'gov-pubs-guide-assessors': 2, 'statutes-70-34': 3 } } } },
  // Graph Expansion
  { kind: 'tool_call', seq: 7, timestamp: 8, payload: { toolName: 'get_neighbors', summary: 'Wisconsin Statute §70.32', status: 'pending' } },
  { kind: 'tool_result', seq: 8, timestamp: 9, payload: { toolName: 'get_neighbors', summary: 'Retrieved 8 related documents from graph', status: 'ok', metadata: { neighborCount: 8, neighborEdges: [{ title: 'WPAM Ch. 7 — Sales Comparison', relationship: 'IMPLEMENTS' }, { title: 'WPAM Ch. 9 — Income Approach', relationship: 'IMPLEMENTS' }, { title: 'Admin Rule Tax 18.05', relationship: 'CITES' }, { title: 'Statute §70.34 — Personal Property', relationship: 'RELATED_TO' }, { title: 'State ex rel. Markarian v. City of Cudahy', relationship: 'CITES' }, { title: 'Admin Rule Tax 11.01', relationship: 'CITES' }], neighborTitles: ['WPAM Ch. 7 — Sales Comparison', 'WPAM Ch. 9 — Income Approach', 'Admin Rule Tax 18.05', 'Statute §70.34 — Personal Property', 'State ex rel. Markarian v. City of Cudahy', 'Admin Rule Tax 11.01'] } } },
  { kind: 'tool_call', seq: 9, timestamp: 10, payload: { toolName: 'get_authority_chain', summary: 'Wisconsin Statute §70.32', status: 'pending' } },
  { kind: 'tool_result', seq: 10, timestamp: 11, payload: { toolName: 'get_authority_chain', summary: 'Traced 4 authority steps', status: 'ok', metadata: { chainLength: 4 } } },
  // List Sections
  { kind: 'tool_call', seq: 11, timestamp: 12, payload: { toolName: 'list_sections', summary: 'WPAM Chapter 9 — Income Approach to Value', status: 'pending' } },
  { kind: 'tool_result', seq: 12, timestamp: 13, payload: { toolName: 'list_sections', summary: 'Found 8 sections in WPAM Chapter 9 — Income Approach to Value', status: 'ok', metadata: { sectionCount: 8, docId: 'wpam-ch9-income-approach', docTitle: 'WPAM Chapter 9 — Income Approach to Value', sectionHeadings: ['9.1 Introduction', '9.2 Potential Gross Income', '9.3 Vacancy & Collection Loss', '9.4 Effective Gross Income', '9.5 Operating Expenses', '9.6 Net Operating Income', '9.7 Capitalization Rate', '9.8 Direct Capitalization'], latencyMs: 95 } } },
  // Get Section
  { kind: 'tool_call', seq: 13, timestamp: 14, payload: { toolName: 'get_section', summary: '"9.7 Capitalization Rate" from WPAM Chapter 9 — Income Approach to Value', status: 'pending' } },
  { kind: 'tool_result', seq: 14, timestamp: 15, payload: { toolName: 'get_section', summary: 'Got "9.7 Capitalization Rate" (4 chunks) from WPAM Chapter 9 — Income Approach to Value', status: 'ok', metadata: { chunkCount: 4, docId: 'wpam-ch9-income-approach', docTitle: 'WPAM Chapter 9 — Income Approach to Value', heading: '9.7 Capitalization Rate', latencyMs: 120 } } },
  // Search Document
  { kind: 'tool_call', seq: 15, timestamp: 16, payload: { toolName: 'search_document', summary: '"full value assessment standard" in WPAM Chapter 7 — Sales Comparison', status: 'pending' } },
  { kind: 'tool_result', seq: 16, timestamp: 17, payload: { toolName: 'search_document', summary: 'Searched WPAM Chapter 7 — Sales Comparison', status: 'ok', metadata: { docId: 'wpam-ch7-sales-comparison', docTitle: 'WPAM Chapter 7 — Sales Comparison', chunkCount: 6, latencyMs: 340 } } },
  // List Framework Docs
  { kind: 'tool_call', seq: 17, timestamp: 18, payload: { toolName: 'list_framework_docs', summary: 'WPAM', status: 'pending' } },
  { kind: 'tool_result', seq: 18, timestamp: 19, payload: { toolName: 'list_framework_docs', summary: 'Listed 14 framework documents', status: 'ok', metadata: { documentCount: 14, latencyMs: 65 } } },
  // Find Case Law
  { kind: 'tool_call', seq: 19, timestamp: 20, payload: { toolName: 'find_case_law', summary: 'full value assessment uniformity', status: 'pending' } },
  { kind: 'tool_result', seq: 20, timestamp: 21, payload: { toolName: 'find_case_law', summary: 'Found 3 cases', status: 'ok', metadata: { docCount: 3, latencyMs: 180 } } },
  // Fetch Case Opinion
  { kind: 'tool_call', seq: 21, timestamp: 22, payload: { toolName: 'fetch_case_opinion', summary: 'State ex rel. Markarian v. City of Cudahy', status: 'pending' } },
  { kind: 'tool_result', seq: 22, timestamp: 23, payload: { toolName: 'fetch_case_opinion', summary: 'Fetched opinion for State ex rel. Markarian v. City of Cudahy', status: 'ok', metadata: { opinionChars: 14280, latencyMs: 520 } } },
  // Get Document (miss)
  { kind: 'tool_call', seq: 23, timestamp: 24, payload: { toolName: 'get_document', summary: 'admin-rules-tax-18', status: 'pending' } },
  { kind: 'tool_result', seq: 24, timestamp: 25, payload: { toolName: 'get_document', summary: 'Document not found', status: 'miss', metadata: { documentCount: 0 } } },
  // Reasoning + Synthesis
  { kind: 'reasoning', seq: 25, timestamp: 26, payload: { text: 'I have statute text from §70.32, WPAM guidance from chapters 7 and 9, and the Markarian case reinforcing the full-value standard. The authority chain traces from Constitution → Statute → WPAM → Case Law. I can now synthesize a comprehensive answer citing all primary sources.' } },
  { kind: 'tool_call', seq: 26, timestamp: 27, payload: { toolName: 'prepare_answer', summary: 'with 4 cited sources', status: 'pending' } },
  { kind: 'tool_result', seq: 27, timestamp: 28, payload: { toolName: 'prepare_answer', summary: 'Preparing answer with 4 sources', status: 'terminal', metadata: { citedDocCount: 4, hasPlan: true } } },
  { kind: 'loop_complete', seq: 28, timestamp: 29, payload: { elapsedMs: 6800, citedDocCount: 4, discoveryCounts: { 'vector-search': 5, 'graph-neighbor': 3, 'search-document': 2, 'opinion-fetched': 1, 'framework-list': 1 }, discoveryTitles: { 'statute-70-32': 'Wisconsin Statute §70.32', 'wpam-ch9': 'WPAM Chapter 9 — Income Approach', 'wpam-ch7': 'WPAM Chapter 7 — Sales Comparison', 'case-law-markarian': 'State ex rel. Markarian v. City of Cudahy', 'admin-rules-tax-11': 'Admin Rule Tax 11.01' } } },
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
          }, 800 + i * 200)
        );
      });

      const traceEndMs = 800 + MOCK_AGENT_TRACE.length * 200 + 500;

      // Simulate backend delay, then stream documents + response
      timeoutsRef.current.push(
        setTimeout(() => {
          updateQueryResources(queryId, MOCK_DOCUMENTS);
        }, traceEndMs)
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
        }, traceEndMs + 1500)
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
