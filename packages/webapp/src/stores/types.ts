// Core message types used throughout the application
export interface MessageUnion {
  type: string;
  [key: string]: unknown;
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
  resources?: QueryResources;

  error?: QueryError;

  retryCount?: number;
}

export interface QueryError {
  message: string;
  userMessage: string;
  details?: Record<string, unknown>;
  retryable: boolean;
}

export interface QueryResources {
  type: 'documents' | 'faq';
  content: DocumentsContent | FAQContent;
}

export interface Document {
  documentId: string;
  title: string;
  content: string;
  source?: string;
}

export interface DocumentsContent {
  documents: Array<Document>;
}

export interface FAQ {
  question: string;
  answer: string;
}

export interface FAQContent {
  faq: FAQ;
}

export interface ChatError {
  id: string;
  type: 'session' | 'websocket' | 'api' | 'validation';
  message: string;
  userMessage: string; // User-facing error message
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

// Main store interface
export interface ChatStore {
  sessionId: string | null;
  sessionStatus: SessionStatus;

  connectionState: ConnectionState;

  chatState: ChatState;
  currentQueryId: string | null;

  queries: Record<string, Query>; // queryId -> Query
  queryOrder: string[]; // Ordered list of queryIds for display

  errors: ChatError[];
  draftMessage: string;

  setSessionId: (sessionId: string) => void;
  setSessionStatus: (status: SessionStatus) => void;
  setConnectionState: (state: ConnectionState) => void;
  setChatState: (state: ChatState) => void;
  setCurrentQueryId: (queryId: string | null) => void;

  addQuery: (query: Query) => void;
  updateQueryStatus: (queryId: string, status: QueryStatus) => void;
  updateQueryResponse: (queryId: string, content: string) => void;
  updateQueryResources: (queryId: string, resources: QueryResources) => void;

  setQueryError: (queryId: string, error: QueryError) => void;
  clearQueryError: (queryId: string) => void;
  incrementQueryRetry: (queryId: string) => void;
  getQuery: (queryId: string) => Query | undefined;

  addError: (error: ChatError) => void;
  removeError: (errorId: string) => void;
  clearErrors: () => void;
  setDraftMessage: (draft: string) => void;
  clearHistory: () => void;
  reset: () => void;
}
