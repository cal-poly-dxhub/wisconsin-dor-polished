'use client';

import { ShieldCheck, HelpCircle, Ban } from 'lucide-react';

// The post-retrieval adequacy judge (backend/lambdas/agentic_retrieval/
// adequacy_judge.py) runs between Phase A and Phase B and records one
// Finding {verdict, supported, unsupported, clarification, rationale}.
//
// What actually reaches the browser is narrower than the Finding: handler.py
// emits `phase: adequacy_judged` with the verdict (`result`), a human label,
// and metadata {latencyMs, clarificationOffered, judgeModelId} — the prose
// fields stay server-side because the finding is internal. The optional
// supported/unsupported/rationale props below are rendered when present so the
// pane is already correct if the backend starts sending them; the
// clarification options are the real `choices` chips the backend sent after
// the stream, handed down by the canvas.

export type AdequacyVerdict = 'answer' | 'clarify' | 'decline';

interface AdequacyData {
  verdict: AdequacyVerdict;
  label: string;
  latencyMs?: number;
  clarificationOffered?: boolean;
  judgeModelId?: string;
  supported?: string;
  unsupported?: string;
  rationale?: string;
  clarificationQuestion?: string;
  clarificationOptions?: string[];
}

const VERDICT_STYLE: Record<AdequacyVerdict, { badge: string; icon: typeof ShieldCheck }> = {
  answer: { badge: 'bg-green-50 text-green-700', icon: ShieldCheck },
  clarify: { badge: 'bg-amber-50 text-amber-700', icon: HelpCircle },
  decline: { badge: 'bg-red-50 text-red-700', icon: Ban },
};

function shortModel(modelId: string): string {
  // "us.anthropic.claude-haiku-4-5-20251001-v1:0" → "claude-haiku-4-5"
  const match = modelId.match(/(claude-[a-z]+-[\d-]*\d)/);
  return match ? match[1] : modelId;
}

export function AdequacyPane({ data }: { data: AdequacyData }) {
  const style = VERDICT_STYLE[data.verdict] ?? VERDICT_STYLE.answer;
  const Icon = style.icon;

  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Adequacy Judge</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
        <div className="flex-1" />
        {data.judgeModelId && (
          <span className="font-mono text-[11px] text-neutral-400">
            {shortModel(data.judgeModelId)}
          </span>
        )}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <Icon className="h-5 w-5 shrink-0 text-neutral-400" />
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${style.badge}`}
        >
          {data.verdict}
        </span>
        <span className="text-sm text-neutral-700">{data.label}</span>
      </div>

      {data.supported && (
        <div className="mt-4">
          <h3 className="text-xs font-bold uppercase tracking-wide text-neutral-500">
            Supported by the cited material
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-neutral-700">{data.supported}</p>
        </div>
      )}

      {data.unsupported && (
        <div className="mt-3">
          <h3 className="text-xs font-bold uppercase tracking-wide text-neutral-500">
            Not supported
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-neutral-700">{data.unsupported}</p>
        </div>
      )}

      {(data.clarificationQuestion || (data.clarificationOptions?.length ?? 0) > 0) && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
          {data.clarificationQuestion && (
            <p className="text-sm text-amber-900">{data.clarificationQuestion}</p>
          )}
          {(data.clarificationOptions?.length ?? 0) > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {data.clarificationOptions?.map((option) => (
                <span
                  key={option}
                  className="rounded-full border border-amber-300 bg-white px-2.5 py-0.5 text-[11px] text-amber-900"
                >
                  {option}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {data.clarificationOffered && !data.clarificationOptions?.length && (
        <p className="mt-3 text-xs text-amber-700">
          Clarification offered — chips follow the streamed answer.
        </p>
      )}

      {data.rationale && (
        <p className="mt-3 text-xs italic text-neutral-500">{data.rationale}</p>
      )}

      <div className="mt-5 rounded-md bg-neutral-100 px-3 py-2">
        <p className="text-xs font-medium uppercase tracking-wide text-neutral-600">
          Finding injected into Phase B context
        </p>
      </div>
    </div>
  );
}
