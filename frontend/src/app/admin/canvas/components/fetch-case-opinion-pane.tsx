'use client';

import { Scale } from 'lucide-react';

interface FetchCaseOpinionData {
  citation: string;
  opinionChars: number;
  status: 'ok' | 'miss';
  latencyMs?: number;
}

export function FetchCaseOpinionPane({ data }: { data: FetchCaseOpinionData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Case Opinion</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Scale className="h-5 w-5 text-purple-500" />
        <span className="text-sm text-neutral-700">{data.citation || 'unknown'}</span>
        {data.status === 'miss' && (
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
            NOT FOUND
          </span>
        )}
      </div>
      {data.status === 'ok' && data.opinionChars > 0 && (
        <p className="mt-2 text-xs text-neutral-500">
          {(data.opinionChars / 1000).toFixed(1)}K characters fetched
        </p>
      )}
    </div>
  );
}
