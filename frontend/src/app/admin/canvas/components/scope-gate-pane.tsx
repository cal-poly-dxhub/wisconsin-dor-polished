'use client';

import { Ban, Shuffle } from 'lucide-react';

// The pre-loop classifier (disambiguation.classify_query) returns one of
// PROCEED | DISAMBIGUATE | OUT_OF_SCOPE | TOPIC_SHIFT, emitted as
// `phase: generality_classified`. PROCEED/DISAMBIGUATE render in the
// disambiguation pane; this pane covers the two verdicts that are about the
// conversation rather than the property type.
//
// Whether a verdict actually stops the run is now a runtime question:
// with the adequacy judge ON and the scope gate OFF, OUT_OF_SCOPE no longer
// short-circuits — every query runs the research loop and the judge decides
// afterwards with the corpus in front of it. TOPIC_SHIFT keeps its own flag
// and still short-circuits. `shortCircuited` is derived from the trace (did
// any tool event follow this verdict?) rather than assumed.

export type ScopeVerdict = 'out_of_scope' | 'topic_shift';

interface ScopeGateData {
  verdict: ScopeVerdict;
  label: string;
  shortCircuited: boolean;
}

const VERDICT_COPY: Record<ScopeVerdict, { title: string; icon: typeof Ban; note: string }> = {
  out_of_scope: {
    title: 'Out of Scope',
    icon: Ban,
    note: 'Classifier judged the question outside property tax scope.',
  },
  topic_shift: {
    title: 'Topic Shift',
    icon: Shuffle,
    note: 'Classifier judged this a new subject; the user is offered new-chat / continue-here controls.',
  },
};

export function ScopeGatePane({ data }: { data: ScopeGateData }) {
  const copy = VERDICT_COPY[data.verdict] ?? VERDICT_COPY.out_of_scope;
  const Icon = copy.icon;

  return (
    <div className="flex flex-col px-5 py-5">
      <div className="mb-3">
        <h2 className="text-2xl font-bold text-neutral-900">{copy.title}</h2>
        <p className="mt-1 text-sm text-neutral-500">{data.label}</p>
      </div>

      <div className="flex items-center gap-3">
        <Icon className="h-5 w-5 shrink-0 text-neutral-400" />
        <p className="text-sm text-neutral-700">{copy.note}</p>
      </div>

      <div
        className={`mt-5 rounded-md px-3 py-2 ${data.shortCircuited ? 'bg-red-50' : 'bg-amber-50'}`}
      >
        <p
          className={`text-xs font-medium uppercase tracking-wide ${
            data.shortCircuited ? 'text-red-700' : 'text-amber-700'
          }`}
        >
          {data.shortCircuited
            ? 'Agent loop short-circuited — no retrieval ran'
            : 'Scope gate off — verdict recorded, research loop ran anyway'}
        </p>
      </div>
    </div>
  );
}
