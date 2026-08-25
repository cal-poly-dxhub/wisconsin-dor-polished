import type { FAQ } from '@messages/websocket-interface';

// Core message types used throughout the application
export interface MessageUnion {
  type: string;
  [key: string]: unknown;
}

export type SuggestionKind = 'topic-shift';

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
  // Soft, dismissible suggestion attached to this turn. 'topic-shift' offers
  // start-new-chat / continue-here controls when the classifier thinks the
  // question opens an unrelated subject. Cleared once the user picks or dismisses.
  suggestion?: SuggestionKind;

  error?: QueryError;

  retryCount?: number;
  thinkingDuration?: number;
  agentTrace?: AgentTraceEvent[];
}

export interface QueryError {
  message: string;
  userMessage: string;
  details?: Record<string, unknown>;
  retryable: boolean;
}

export interface QueryResources {
  items: ResourceItem[];
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

export interface DocumentsContent {
  documents: Array<Document>;
}

export type { FAQ };

export interface FAQContent {
  faqs: Array<FAQ>;
}

export interface ResourceItem {
  type: 'document' | 'faq';
  data: Document | FAQ;
}

export interface ChatError {
  id: string;
  type: 'session' | 'websocket' | 'api' | 'validation';
  message: string;
  details?: Record<string, unknown>;
  retryable: boolean;
  timestamp: Date;
}

// State enums
export type SessionStatus = 'idle' | 'creating' | 'ready' | 'error';
export type ConnectionState = 'connecting' | 'open' | 'closing' | 'closed';
export type ChatState =
  | 'idle'
  | 'sending'
  | 'waiting_for_response'
  | 'streaming';
export type QueryStatus =
  | 'pending'
  | 'sending'
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
  sessionStatus: SessionStatus;

  connectionState: ConnectionState;

  chatState: ChatState;
  currentQueryId: string | null;

  queries: Record<string, Query>; // queryId -> Query
  queryOrder: string[]; // Ordered list of queryIds for display

  errors: ChatError[];
  draftMessage: string;

  sessionId: string | null;
  sessionCache: Record<string, SessionSnapshot>;
  switchingSession: boolean;
  // One-shot: when true, the next message send suppresses the TOPIC_SHIFT
  // verdict (OUT_OF_SCOPE / DISAMBIGUATE still apply) and then resets to false.
  // Set when the user dismisses a topic-shift suggestion, so the nudge that was
  // just declined can't immediately re-fire on their next question.
  suppressTopicShiftOnNextSend: boolean;

  setSessionStatus: (status: SessionStatus) => void;
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

  setQueryChoices: (queryId: string, choices: string[]) => void;
  setQuerySuggestion: (queryId: string, suggestion: SuggestionKind) => void;
  clearQuerySuggestion: (queryId: string) => void;
  setQueryError: (queryId: string, error: QueryError) => void;
  clearQueryError: (queryId: string) => void;
  incrementQueryRetry: (queryId: string) => void;
  setThinkingDuration: (queryId: string, duration: number) => void;
  appendAgentTraceEvent: (queryId: string, event: AgentTraceEvent) => void;
  getQuery: (queryId: string) => Query | undefined;

  addError: (error: ChatError) => void;
  removeError: (errorId: string) => void;
  clearErrors: () => void;
  setDraftMessage: (draft: string) => void;
  setSuppressTopicShiftOnNextSend: (value: boolean) => void;
  clearHistory: () => void;
  stashSession: (sessionId: string) => void;
  restoreSession: (sessionId: string) => boolean;
  reset: () => void;
}
