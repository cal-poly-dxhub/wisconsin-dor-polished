'use client';

import { HelpCircle } from 'lucide-react';

interface ClarifyData {
  summary: string;
  latencyMs?: number;
}

export function ClarifyPane({ data }: { data: ClarifyData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Clarification</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
        <HelpCircle className="h-5 w-5 shrink-0 text-amber-600" />
        <p className="text-sm text-amber-900">{data.summary}</p>
      </div>
    </div>
  );
}
