import { z } from 'zod';

// Pydantic serializes Optional fields as JSON `null`, but z.optional() only
// accepts `undefined` (i.e., key absent). Use nullish() and normalize null
// to undefined so the runtime shape matches the TS Document type and a
// single null doesn't reject the whole documents frame.
const optStr = z.string().nullish().transform(v => v ?? undefined);
const optInt = z.number().int().nullish().transform(v => v ?? undefined);
const optNum = z.number().nullish().transform(v => v ?? undefined);

export const ChunkSnippetSchema = z.object({
  page: z.number().int(),
  text: z.string(),
});

export const SourceDocumentSchema = z.object({
  documentId: z.string(),
  title: z.string(),
  content: z.string(),
  source: optStr,
  sourceUrl: optStr,
  discoveryTag: optStr,
  authorityLevel: optNum,
  // Stable reference to the raw S3 object plus page range. Links use the
  // public sourceUrl; s3Key is kept for provenance/debugging only.
  s3Key: optStr,
  startPage: optInt,
  endPage: optInt,
  editionYear: optInt,
  chunks: z.array(ChunkSnippetSchema).optional().default([]),
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
  // Public revenue.wi.gov page for this FAQ; absent/null when unmatched.
  sourceUrl: optStr,
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
  'turn_usage',
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

export const ChoicesContentSchema = z.object({
  choices: z.array(z.string()),
});

export const ChoicesMessageSchema = z.object({
  responseType: z.literal('choices'),
  queryId: z.string(),
  content: ChoicesContentSchema,
});

export const SuggestionContentSchema = z.object({
  kind: z.literal('topic-shift'),
});

export const SuggestionMessageSchema = z.object({
  responseType: z.literal('suggestion'),
  queryId: z.string(),
  content: SuggestionContentSchema,
});

// ── Flowchart (interactive "Walk the flowchart") ──────────────────────────────
export const FlowchartAuthoritySchema = z.object({
  kind: z.string(),
  cite: z.string(),
  note: optStr,
});

export const FlowchartNodeSchema = z.object({
  id: z.string(),
  type: z.string(), // start | decision | terminal | end | note
  step: optInt,
  label: optStr,
  question: optStr,
  definition: optStr,
  guidance: optStr,
  note: optStr,
  context: optStr,
  criteria: z.array(z.string()).optional().default([]),
  outcome: optStr,
  action: optStr,
  authorities: z.array(FlowchartAuthoritySchema).optional().default([]),
});

export const FlowchartEdgeSchema = z.object({
  from: z.string(),
  to: z.string(),
  branch: optStr, // 'yes' | 'no' | undefined (pass-through)
  label: optStr,
});

export const FlowchartContentSchema = z.object({
  flowchartId: z.string(),
  title: z.string(),
  summary: optStr,
  statute: optStr,
  disclaimer: z.string(),
  wpamPage: optStr,
  sourceUrl: optStr,
  startNode: z.string(),
  nodes: z.array(FlowchartNodeSchema),
  edges: z.array(FlowchartEdgeSchema),
  routerScore: optNum,
});

export const FlowchartMessageSchema = z.object({
  responseType: z.literal('flowchart'),
  queryId: z.string(),
  content: FlowchartContentSchema,
});

export const MessageUnionSchema = z.discriminatedUnion('responseType', [
  DocumentsMessageSchema,
  FAQMessageSchema,
  ErrorMessageSchema,
  FragmentMessageSchema,
  AnswerEventTypeSchema,
  AgentEventSchema,
  ChoicesMessageSchema,
  SuggestionMessageSchema,
  FlowchartMessageSchema,
]);

export const WebSocketMessageSchema = z.object({
  streamId: z.enum([
    'answer-event',
    'answer',
    'resources',
    'error',
    'agent-trace',
    'choices',
    'suggestion',
  ]),
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
export type ChoicesContent = z.infer<typeof ChoicesContentSchema>;
export type ChoicesMessage = z.infer<typeof ChoicesMessageSchema>;
export type SuggestionContent = z.infer<typeof SuggestionContentSchema>;
export type SuggestionMessage = z.infer<typeof SuggestionMessageSchema>;
export type FlowchartAuthority = z.infer<typeof FlowchartAuthoritySchema>;
export type FlowchartNode = z.infer<typeof FlowchartNodeSchema>;
export type FlowchartEdge = z.infer<typeof FlowchartEdgeSchema>;
export type FlowchartContent = z.infer<typeof FlowchartContentSchema>;
export type FlowchartMessage = z.infer<typeof FlowchartMessageSchema>;
export type MessageUnion = z.infer<typeof MessageUnionSchema>;
export type WebSocketMessage = z.infer<typeof WebSocketMessageSchema>;

export type MessageHandler = (message: MessageUnion) => void;
