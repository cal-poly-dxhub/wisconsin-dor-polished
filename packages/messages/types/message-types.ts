import { z } from 'zod';

export const SourceDocumentSchema = z.object({
  documentId: z.string(),
  title: z.string(),
  content: z.string(),
  source: z.string().optional(),
  sourceUrl: z.string().optional(),
  discoveryTag: z.string().optional(),
  authorityLevel: z.number().optional(),
  // Stable references to the raw S3 object. The frontend sends these to
  // GET /citation at click time; the resolver mints a 15-minute presigned
  // URL and 302-redirects.
  s3Key: z.string().optional(),
  startPage: z.number().int().optional(),
  endPage: z.number().int().optional(),
  editionYear: z.number().int().optional(),
});

export const DocumentsContentSchema = z.object({
  documents: z.array(SourceDocumentSchema),
});

export const DocumentsMessageSchema = z.object({
  responseType: z.literal('documents'),
  queryId: z.string(),
  content: DocumentsContentSchema,
});

export const FAQSchema = z.object({
  faqId: z.string(),
  question: z.string(),
  answer: z.string(),
});

export const FAQContentSchema = z.object({
  faqs: z.array(FAQSchema),
});

export const FAQMessageSchema = z.object({
  responseType: z.literal('faq'),
  queryId: z.string(),
  content: FAQContentSchema,
});

export const ErrorContentSchema = z.object({
  error: z.string(),
});

export const ErrorMessageSchema = z.object({
  responseType: z.literal('error'),
  queryId: z.string().optional(),
  content: ErrorContentSchema,
});

export const AnswerEventTypeSchema = z.object({
  responseType: z.literal('answer-event'),
  event: z.enum(['start', 'stop']),
  queryId: z.string(),
});

export const FragmentContentSchema = z.object({
  fragment: z.string(),
});

export const FragmentMessageSchema = z.object({
  responseType: z.literal('fragment'),
  queryId: z.string(),
  content: FragmentContentSchema,
});

export const AgentEventKindSchema = z.enum([
  'loop_start',
  'reasoning',
  'tool_call',
  'tool_result',
  'loop_complete',
  'phase',
]);

export const AgentEventSchema = z.object({
  responseType: z.literal('agent-event'),
  queryId: z.string(),
  kind: AgentEventKindSchema,
  turn: z.number().int().nullable().optional(),
  seq: z.number().int(),
  timestamp: z.number(),
  payload: z.record(z.string(), z.unknown()).default({}),
  devPayload: z.record(z.string(), z.unknown()).default({}),
});

export const MessageUnionSchema = z.discriminatedUnion('responseType', [
  DocumentsMessageSchema,
  FAQMessageSchema,
  ErrorMessageSchema,
  FragmentMessageSchema,
  AnswerEventTypeSchema,
  AgentEventSchema,
]);

export const WebSocketMessageSchema = z.object({
  streamId: z.enum(['answer-event', 'answer', 'resources', 'error', 'agent-trace']),
  body: MessageUnionSchema,
});

export type SourceDocument = z.infer<typeof SourceDocumentSchema>;
export type DocumentsContent = z.infer<typeof DocumentsContentSchema>;
export type DocumentsMessage = z.infer<typeof DocumentsMessageSchema>;
export type FAQ = z.infer<typeof FAQSchema>;
export type FAQContent = z.infer<typeof FAQContentSchema>;
export type FAQMessage = z.infer<typeof FAQMessageSchema>;
export type ErrorContent = z.infer<typeof ErrorContentSchema>;
export type ErrorMessage = z.infer<typeof ErrorMessageSchema>;
export type AnswerEventType = z.infer<typeof AnswerEventTypeSchema>;
export type FragmentContent = z.infer<typeof FragmentContentSchema>;
export type FragmentMessage = z.infer<typeof FragmentMessageSchema>;
export type AgentEventKind = z.infer<typeof AgentEventKindSchema>;
export type AgentEvent = z.infer<typeof AgentEventSchema>;
export type MessageUnion = z.infer<typeof MessageUnionSchema>;
export type WebSocketMessage = z.infer<typeof WebSocketMessageSchema>;

export type MessageHandler = (message: MessageUnion) => void;
