import { z } from 'zod';

export const SourceDocumentSchema = z.object({
  documentId: z.string(),
  title: z.string(),
  content: z.string(),
  source: z.string().optional(),
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
  question: z.string(),
  answer: z.string(),
});

export const FAQContentSchema = z.object({
  faq: FAQSchema,
});

export const FAQMessageSchema = z.object({
  responseType: z.literal('faq'),
  queryId: z.string(),
  content: FAQContentSchema,
});

export const ErrorContentSchema = z.object({
  message: z.string(),
});

export const ErrorMessageSchema = z.object({
  responseType: z.literal('error'),
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

export const MessageUnionSchema = z.discriminatedUnion('responseType', [
  DocumentsMessageSchema,
  FAQMessageSchema,
  ErrorMessageSchema,
  FragmentMessageSchema,
  AnswerEventTypeSchema,
]);

export const WebSocketMessageSchema = z.object({
  streamId: z.enum(['answer-event', 'answer', 'resources', 'error']),
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
export type MessageUnion = z.infer<typeof MessageUnionSchema>;
export type WebSocketMessage = z.infer<typeof WebSocketMessageSchema>;

export type MessageHandler = (message: MessageUnion) => void;
