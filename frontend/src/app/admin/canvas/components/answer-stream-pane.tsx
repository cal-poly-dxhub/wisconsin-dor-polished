'use client';

import { AlertTriangle, FileText, MessageCircleQuestion } from 'lucide-react';
import type { ResourceItem, Document } from '@/stores/types';
import type { FAQ } from '@messages/websocket-interface';

// Phase B — everything that happens after the research loop hands off.
//
// The loop's trace ends at `prepare_answer`; the answer itself arrives on
// separate WebSocket frames (`answer-event` start/stop, `fragment`,
// `documents`, `faq`, `choices`, `error`), which the visualizer socket hook
// accumulates. This pane is the only place the canvas shows what the user
// actually saw.
//
// On a persisted replay only the stored answer text exists — resources and
// chips are not returned by the activity detail endpoint — so every section
// here is independently optional.

export interface AnswerStreamData {
  text: string;
  streaming: boolean;
  complete: boolean;
  resources: ResourceItem[];
  choices: string[];
  error?: string | null;
  /** Emitted as `phase: answer_streaming` when Phase B opens. */
  started: boolean;
  suggestion?: 'topic-shift' | null;
}

function authorityColor(doc: Document): string {
  const id = doc.documentId ?? '';
  if (id.startsWith('wpam-')) return '#3958E3';
  if (id.startsWith('admin_rules-')) return '#FD8709';
  if (id.startsWith('statutes-')) return '#FD0909';
  if (id.startsWith('case-law-')) return '#9333EA';
  if (id.startsWith('gov_publications-') || id.startsWith('news-')) return '#61B233';
  return '#000000';
}

export function AnswerStreamPane({
  data,
  onChoice,
}: {
  data: AnswerStreamData;
  onChoice?: (choice: string) => void;
}) {
  const documents = data.resources.filter((r) => r.type === 'document');
  const faqs = data.resources.filter((r) => r.type === 'faq');

  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Answer Stream</h2>
        <span className="text-xs font-bold uppercase tracking-wide text-neutral-400">Phase B</span>
        <div className="flex-1" />
        {data.error ? (
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-red-600">
            Error
          </span>
        ) : data.complete ? (
          <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-green-700">
            Complete
          </span>
        ) : data.streaming || data.started ? (
          <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-blue-700">
            Streaming
          </span>
        ) : null}
      </div>

      {data.error && (
        <div className="mt-3 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
          <p className="text-sm text-red-900">{data.error}</p>
        </div>
      )}

      {data.text ? (
        <div className="mt-3 max-h-[28rem] overflow-y-auto rounded-lg border border-neutral-100 bg-neutral-50 px-4 py-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-neutral-800">
            {data.text}
            {!data.complete && <span className="ml-0.5 text-neutral-400">▍</span>}
          </p>
        </div>
      ) : (
        !data.error && (
          <p className="mt-3 text-sm text-neutral-400">
            {data.started ? 'Waiting for the first fragment…' : 'No answer streamed yet.'}
          </p>
        )
      )}

      {data.choices.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-neutral-500">
            Clarification chips
          </h3>
          <div className="flex flex-wrap gap-2">
            {data.choices.map((choice) => (
              <button
                key={choice}
                type="button"
                onClick={() => onChoice?.(choice)}
                className="cursor-pointer rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-700 transition-colors hover:border-neutral-300 hover:bg-neutral-100"
              >
                {choice}
              </button>
            ))}
          </div>
        </div>
      )}

      {data.suggestion === 'topic-shift' && (
        <div className="mt-4 rounded-md bg-amber-50 px-3 py-2">
          <p className="text-xs font-medium uppercase tracking-wide text-amber-700">
            Topic-shift suggestion sent (new chat / continue here)
          </p>
        </div>
      )}

      {(documents.length > 0 || faqs.length > 0) && (
        <div className="mt-5">
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-neutral-500">
            {documents.length + faqs.length} resource
            {documents.length + faqs.length === 1 ? '' : 's'} delivered
          </h3>
          <ul className="space-y-1.5">
            {documents.map((item) => {
              const doc = item.data as Document;
              return (
                <li key={`doc-${doc.documentId}`} className="flex items-center gap-2">
                  <div
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: 2,
                      backgroundColor: authorityColor(doc),
                    }}
                  />
                  <FileText className="h-3.5 w-3.5 shrink-0 text-neutral-300" />
                  <span className="truncate text-xs text-neutral-700">
                    {doc.title || doc.documentId}
                  </span>
                  {doc.discoveryTag && (
                    <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-500">
                      {doc.discoveryTag}
                    </span>
                  )}
                </li>
              );
            })}
            {faqs.map((item) => {
              const faq = item.data as FAQ;
              return (
                <li key={`faq-${faq.faqId}`} className="flex items-center gap-2">
                  <MessageCircleQuestion className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                  <span className="truncate text-xs text-neutral-700">{faq.question}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
