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
  resources?: ResourceItem[];

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
  items: ResourceItem[];
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
  faqId: string;
  question: string;
  answer: string;
}

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

  setSessionStatus: (status: SessionStatus) => void;
  setConnectionState: (state: ConnectionState) => void;
  setChatState: (state: ChatState) => void;
  setCurrentQueryId: (queryId: string | null) => void;
  setSessionId: (sessionId: string | null) => void;

  addQuery: (query: Query) => void;
  updateQueryStatus: (queryId: string, status: QueryStatus) => void;
  updateQueryResponse: (queryId: string, content: string) => void;
  appendQueryResponse: (queryId: string, fragment: string) => void;
  updateQueryResources: (queryId: string, resources: ResourceItem[]) => void;

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
