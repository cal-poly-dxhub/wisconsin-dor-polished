'use client';

import { Library } from 'lucide-react';

interface ListFrameworkDocsData {
  documentCount: number;
  latencyMs?: number;
  summary: string;
}

export function ListFrameworkDocsPane({ data }: { data: ListFrameworkDocsData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Framework Docs</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Library className="h-5 w-5 text-neutral-400" />
        <span className="text-sm text-neutral-700">
          {data.documentCount} document{data.documentCount === 1 ? '' : 's'}
        </span>
      </div>
    </div>
  );
}
