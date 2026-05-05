'use client';
import {
  useMutation,
  UseMutationOptions,
  useQuery,
  UseQueryOptions,
} from '@tanstack/react-query';
import {
  createSession,
  sendMessage,
  assignFeedback,
  getSessions,
  getSessionHistory,
  deleteSession,
  SendMessageRequest,
  CreateSessionResponse,
  SendMessageResponse,
  FeedbackRequest,
  FeedbackResponse,
  SessionsListResponse,
  SessionHistoryResponse,
  DeleteSessionResponse,
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

export function useAssignFeedback(
  options?: UseMutationOptions<
    FeedbackResponse,
    Error,
    { sessionId: string; payload: FeedbackRequest }
  >
) {
  return useMutation({
    mutationKey: ['chat', 'session', 'feedback'],
    mutationFn: ({
      sessionId,
      payload,
    }: {
      sessionId: string;
      payload: FeedbackRequest;
    }) => assignFeedback(sessionId, payload),
    ...options,
  });
}

export function useSessionsList(
  options?: Omit<UseQueryOptions<SessionsListResponse, Error>, 'queryKey' | 'queryFn'>
) {
  return useQuery({
    queryKey: ['chat', 'sessions'],
    queryFn: getSessions,
    ...options,
  });
}

export function useSessionHistory(
  sessionId: string | null,
  options?: Omit<UseQueryOptions<SessionHistoryResponse, Error>, 'queryKey' | 'queryFn'>
) {
  return useQuery({
    queryKey: ['chat', 'session', sessionId, 'history'],
    queryFn: () => getSessionHistory(sessionId!),
    enabled: !!sessionId,
    ...options,
  });
}

export function useDeleteSession(
  options?: UseMutationOptions<DeleteSessionResponse, Error, string>
) {
  return useMutation({
    mutationKey: ['chat', 'session', 'delete'],
    mutationFn: (sessionId: string) => deleteSession(sessionId),
    ...options,
  });
}
