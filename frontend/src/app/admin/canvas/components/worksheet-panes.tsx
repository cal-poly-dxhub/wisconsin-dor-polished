'use client';

import { Table2, Rows3 } from 'lucide-react';

// TID worksheet panes (`list_worksheets` / `get_worksheet`).
//
// The backend builds rich metadata for these (worksheetId, sheetCount,
// sheetNames, worksheetCount — see tracing/summaries.py), but
// tracing/emitter.py ALLOWED_METADATA_KEYS does not list those keys yet, so on
// the wire only `latencyMs` survives. Both panes therefore lean on the result
// summary line plus the tool input (devPayload.toolInput), and read the
// metadata defensively so they light up for free if the allowlist grows.

interface ListWorksheetsData {
  summary: string;
  worksheetCount?: number;
  latencyMs?: number;
}

export function ListWorksheetsPane({ data }: { data: ListWorksheetsData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">Worksheet Registry</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Rows3 className="h-5 w-5 text-neutral-400" />
        <span className="text-sm text-neutral-700">
          {data.worksheetCount != null
            ? `${data.worksheetCount} worksheet${data.worksheetCount === 1 ? '' : 's'}`
            : data.summary || 'Listed TID worksheets'}
        </span>
      </div>
    </div>
  );
}

interface GetWorksheetData {
  worksheetId: string;
  sheet?: string;
  sheetCount?: number;
  sheetNames?: string[];
  status: 'ok' | 'miss';
  summary: string;
  latencyMs?: number;
}

export function GetWorksheetPane({ data }: { data: GetWorksheetData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-2xl font-bold text-neutral-900">TID Worksheet</h2>
        {data.latencyMs != null && data.latencyMs > 0 && (
          <span className="text-lg text-neutral-400">{data.latencyMs}ms</span>
        )}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <Table2 className="h-5 w-5 shrink-0 text-emerald-500" />
        <span className="font-mono text-sm text-neutral-700">
          {data.worksheetId || 'unknown worksheet'}
        </span>
        {data.status === 'miss' && (
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
            NOT FOUND
          </span>
        )}
      </div>

      {data.sheet && (
        <p className="mt-1 pl-8 text-xs text-neutral-500">sheet: {data.sheet}</p>
      )}

      {data.sheetNames && data.sheetNames.length > 0 ? (
        <ul className="mt-3 space-y-1">
          {data.sheetNames.map((name) => (
            <li key={name} className="truncate text-xs text-neutral-600">
              {name}
            </li>
          ))}
        </ul>
      ) : (
        data.summary && <p className="mt-3 text-xs text-neutral-500">{data.summary}</p>
      )}

      <p className="mt-4 text-[11px] text-neutral-400">
        Formulas are described, never evaluated — the agent presents the method only.
      </p>
    </div>
  );
}
