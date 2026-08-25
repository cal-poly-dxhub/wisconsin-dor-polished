import { http } from '../lib/http';
import { z } from 'zod';

// Format for Chat API responses
const ApiResponse = z.object({
  statusCode: z.number(),
  body: z.string(),
  isBase64Encoded: z.boolean(),
  headers: z.record(z.string()),
});

// Possible types for body key
const CreateSessionResponse = z.object({
  sessionId: z.string().uuid(),
});
const SendMessageRequest = z.object({
  message: z.string().min(1),
  persona: z.enum(['citizen', 'government']).optional(),
  // Set when re-sending the original question after "Continue here" on a
  // topic-shift suggestion, so the backend skips pre-loop classification.
  forceProceed: z.boolean().optional(),
});
const SendMessageResponse = z.object({
  message: z.string(),
  queryId: z.string().uuid(),
});
// Structured feedback captured by the feedback modal. Mirrors the backend
// RichFeedback model (step_function_types/models.py) and the frontend
// FeedbackDraft store shape (minus transient/queryId fields).
const RichSubsection = z.object({
  answer: z.enum(['yes', 'no']).nullable(),
  comment: z.string(),
});
const RichSourceNote = z.object({
  id: z.string(),
  sourceId: z.string(),
  citedFully: z.string(),
  missedDetail: z.string(),
  comment: z.string(),
});
const RichAnnotation = z.object({
  id: z.string(),
  startOffset: z.number(),
  endOffset: z.number(),
  quote: z.string(),
  comment: z.string(),
});
const RichFeedback = z.object({
  rating: z.enum(['up', 'mid', 'down']).nullable(),
  positiveComment: z.string(),
  response: z.record(RichSubsection),
  sourcesOk: z.enum(['yes', 'no']).nullable(),
  sourceNotes: z.array(RichSourceNote),
  linksWork: z.enum(['yes', 'no']).nullable(),
  brokenLinkIds: z.array(z.string()),
  brokenLinksReason: z.string(),
  annotations: z.array(RichAnnotation),
  speedTimely: z.enum(['yes', 'no']).nullable(),
  speedComment: z.string(),
});
const FeedbackRequest = z.object({
  queryId: z.string().uuid(),
  thumbUp: z.boolean(),
  feedback: z.string().optional(),
  richFeedback: RichFeedback.optional(),
});
const FeedbackResponse = z.object({
  message: z.string(),
  queryId: z.string().uuid(),
});
const Session = z.object({
  sessionId: z.string().uuid(),
  createdAt: z.string().optional(),
  lastMessageAt: z.string().optional(),
  title: z.string().optional(),
});
const SessionsListResponse = z.object({
  sessions: z.array(Session),
});
const ChatMessage = z.object({
  queryId: z.string().uuid(),
  query: z.string(),
  answer: z.string(),
  timestamp: z.string().optional(),
  resources: z.any().optional(),
});
const SessionHistoryResponse = z.object({
  messages: z.array(ChatMessage),
});
const DeleteSessionResponse = z.object({
  message: z.string(),
});
const RenameSessionRequest = z.object({
  title: z.string().min(1),
});
const RenameSessionResponse = z.object({
  message: z.string(),
  title: z.string(),
});

export type ApiResponse = z.infer<typeof ApiResponse>;
export type CreateSessionResponse = z.infer<typeof CreateSessionResponse>;
export type SendMessageRequest = z.infer<typeof SendMessageRequest>;
export type SendMessageResponse = z.infer<typeof SendMessageResponse>;
export type FeedbackRequest = z.infer<typeof FeedbackRequest>;
export type FeedbackResponse = z.infer<typeof FeedbackResponse>;
export type Session = z.infer<typeof Session>;
export type SessionsListResponse = z.infer<typeof SessionsListResponse>;
export type ChatMessage = z.infer<typeof ChatMessage>;
export type SessionHistoryResponse = z.infer<typeof SessionHistoryResponse>;
export type DeleteSessionResponse = z.infer<typeof DeleteSessionResponse>;
export type RenameSessionRequest = z.infer<typeof RenameSessionRequest>;
export type RenameSessionResponse = z.infer<typeof RenameSessionResponse>;

// Parses the API response, handling both wrapped (Lambda proxy 1.0 style)
// and direct (HTTP API format 2.0) response formats.
async function handleApiCall<T>(
  responsePromise: Promise<unknown>,
  responseSchema: z.ZodSchema<T>
): Promise<T> {
  const raw = await responsePromise;

  const wrapped = ApiResponse.safeParse(raw);
  if (wrapped.success) {
    if (wrapped.data.statusCode >= 400) {
      const errorBody = JSON.parse(wrapped.data.body);
      throw new Error(errorBody.message || `HTTP ${wrapped.data.statusCode} Error`);
    }
    const body = JSON.parse(wrapped.data.body);
    return responseSchema.parse(body);
  }

  return responseSchema.parse(raw);
}

export async function createSession() {
  return handleApiCall(http.post('session').json(), CreateSessionResponse);
}

export async function sendMessage(
  sessionId: string,
  payload: SendMessageRequest
) {
  return handleApiCall(
    http.post(`session/${sessionId}/message`, { json: payload }).json(),
    SendMessageResponse
  );
}

export async function assignFeedback(
  sessionId: string,
  payload: FeedbackRequest
) {
  return handleApiCall(
    http.post(`session/${sessionId}/feedback`, { json: payload }).json(),
    FeedbackResponse
  );
}

export async function getSessions() {
  return handleApiCall(http.get('sessions').json(), SessionsListResponse);
}

export async function getSessionHistory(sessionId: string) {
  return handleApiCall(
    http.get(`session/${sessionId}/history`).json(),
    SessionHistoryResponse
  );
}

export async function deleteSession(sessionId: string) {
  return handleApiCall(
    http.delete(`session/${sessionId}`).json(),
    DeleteSessionResponse
  );
}

export async function renameSession(sessionId: string, title: string) {
  return handleApiCall(
    http.patch(`session/${sessionId}`, { json: { title } }).json(),
    RenameSessionResponse
  );
}
