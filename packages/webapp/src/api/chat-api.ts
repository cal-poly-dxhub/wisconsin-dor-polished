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

export type ApiResponse = z.infer<typeof ApiResponse>;
export type CreateSessionResponse = z.infer<typeof CreateSessionResponse>;
export type SendMessageRequest = z.infer<typeof SendMessageRequest>;
export type SendMessageResponse = z.infer<typeof SendMessageResponse>;

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
