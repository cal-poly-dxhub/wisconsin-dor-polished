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
const SendMessageRequest = z.object({ message: z.string().min(1) });
const SendMessageResponse = z.object({
  message: z.string(),
  queryId: z.string().uuid(),
});
const FeedbackRequest = z.object({
  queryId: z.string().uuid(),
  thumbUp: z.boolean(),
  feedback: z.string().optional(),
});
const FeedbackResponse = z.object({
  successful: z.boolean(),
});
const Session = z.object({
  sessionId: z.string().uuid(),
  createdAt: z.string().optional(),
  lastMessageAt: z.string().optional(),
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

// Unwraps the common response format and enforces a body schema
async function handleApiCall<T>(
  responsePromise: Promise<unknown>,
  responseSchema: z.ZodSchema<T>
): Promise<T> {
  const response = ApiResponse.parse(await responsePromise);

  if (response.statusCode >= 400) {
    const errorBody = JSON.parse(response.body);
    throw new Error(errorBody.message || `HTTP ${response.statusCode} Error`);
  }

  const body = JSON.parse(response.body);
  // TODO: map Zod validation errors to standard error type
  return responseSchema.parse(body);
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
