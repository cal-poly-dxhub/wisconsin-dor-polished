'use client';

import { FileText } from 'lucide-react';

interface GetDocumentData {
  docId: string;
  status: 'ok' | 'miss';
  latencyMs?: number;
}

export function GetDocumentPane({ data }: { data: GetDocumentData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Get Document</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <FileText className="h-5 w-5 text-neutral-400" />
        <span className="text-sm text-neutral-700">{data.docId || 'unknown'}</span>
        {data.status === 'miss' && (
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
            NOT FOUND
          </span>
        )}
      </div>
    </div>
  );
}
