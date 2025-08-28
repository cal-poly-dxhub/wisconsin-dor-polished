'use client';
import { useMutation, UseMutationOptions } from '@tanstack/react-query';
import {
  createSession,
  sendMessage,
  SendMessageRequest,
  CreateSessionResponse,
  SendMessageResponse,
} from '../../api/chat-api';

export function useCreateSession(
  options?: UseMutationOptions<CreateSessionResponse, Error, void>
) {
  return useMutation({
    mutationKey: ['chat', 'session', 'create'],
    mutationFn: createSession,
    ...options,
  });
}

export function useSendMessage(
  options?: UseMutationOptions<
    SendMessageResponse,
    Error,
    { sessionId: string; payload: SendMessageRequest }
  >
) {
  return useMutation({
    mutationKey: ['chat', 'session', 'message'],
    mutationFn: ({
      sessionId,
      payload,
    }: {
      sessionId: string;
      payload: SendMessageRequest;
    }) => sendMessage(sessionId, payload),
    ...options,
  });
}
