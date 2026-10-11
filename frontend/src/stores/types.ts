import type { FAQ, FlowchartContent } from '@messages/websocket-interface';

/** Framing that arrives alongside a turn's choice chips: the one question to
 *  put to the user and the axis (the fact) it asks about. */
export interface QueryClarification {
  question?: string;
  axis?: string;
}

export interface AgentTraceEvent {
  kind:
    | 'loop_start'
    | 'reasoning'
    | 'tool_call'
    | 'tool_result'
    | 'loop_complete'
    | 'phase'
    | 'turn_usage';
  turn?: number | null;
  seq: number;
  timestamp: number;
  payload: Record<string, unknown>;
  devPayload?: Record<string, unknown>;
}

export interface Query {
  query: string;
  queryId: string;

  type: 'outbound' | 'inbound';
  timestamp: string;
  status: QueryStatus;

  response: {
    type: 'stream';
    content?: string;
  };
  resources?: ResourceItem[];
  choices?: string[];
  // Question/axis that came with `choices`, rendered with the chips as one
  // clarification block under the answer.
  clarification?: QueryClarification;
  // Interactive decision flowchart seeded for this turn (router SEED). Drives
  // the "Walk the flowchart" affordance under the answer.
  flowchart?: FlowchartContent;

  error?: QueryError;

  thinkingDuration?: number;
  agentTrace?: AgentTraceEvent[];
}

export interface QueryError {
  message: string;
  userMessage: string;
  details?: Record<string, unknown>;
  retryable: boolean;
}

export interface Document {
  documentId: string;
  title: string;
  content?: string;
  source?: string;
  sourceUrl?: string;
  s3Key?: string;
  startPage?: number;
  endPage?: number;
  authorityLevel?: number;
  discoveryTag?: string;
  editionYear?: number;
}

export type { FAQ };

export interface ResourceItem {
  type: 'document' | 'faq';
  data: Document | FAQ;
}

// State enums
export type ConnectionState = 'connecting' | 'open' | 'closing' | 'closed';
export type ChatState =
  | 'idle'
  | 'sending'
  | 'waiting_for_response'
  | 'streaming';
// pending: optimistic, POST not yet answered. sent: the server accepted it and
// retrieval is running. streaming: the answer is arriving.
export type QueryStatus =
  | 'pending'
  | 'sent'
  | 'streaming'
  | 'completed'
  | 'failed';

export interface SessionSnapshot {
  queries: Record<string, Query>;
  queryOrder: string[];
  currentQueryId: string | null;
  chatState: ChatState;
}

// Main store interface
export interface ChatStore {
  connectionState: ConnectionState;

  chatState: ChatState;
  currentQueryId: string | null;

  queries: Record<string, Query>; // queryId -> Query
  queryOrder: string[]; // Ordered list of queryIds for display

  sessionId: string | null;
  sessionCache: Record<string, SessionSnapshot>;
  switchingSession: boolean;

  setSwitchingSession: (switching: boolean) => void;
  setConnectionState: (state: ConnectionState) => void;
  setChatState: (state: ChatState) => void;
  setCurrentQueryId: (queryId: string | null) => void;
  setSessionId: (sessionId: string | null) => void;

  addQuery: (query: Query) => void;
  replaceQueryId: (oldId: string, newId: string) => void;
  updateQueryStatus: (queryId: string, status: QueryStatus) => void;
  updateQueryResponse: (queryId: string, content: string) => void;
  appendQueryResponse: (queryId: string, fragment: string) => void;
  updateQueryResources: (queryId: string, resources: ResourceItem[]) => void;

  setQueryChoices: (
    queryId: string,
    choices: string[],
    clarification?: QueryClarification
  ) => void;
  setQueryFlowchart: (queryId: string, flowchart: FlowchartContent) => void;
  setQueryError: (queryId: string, error: QueryError) => void;
  setThinkingDuration: (queryId: string, duration: number) => void;
  appendAgentTraceEvent: (queryId: string, event: AgentTraceEvent) => void;

  clearHistory: () => void;
  stashSession: (sessionId: string) => void;
  restoreSession: (sessionId: string) => boolean;
  reset: () => void;
}
