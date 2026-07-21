'use client';

const CELL = 22;
const GAP = 3;

function docColor(docId: string): string {
  if (docId.startsWith('wpam-')) return '#3958E3';
  if (docId.startsWith('admin_rules-')) return '#FD8709';
  if (docId.startsWith('statutes-')) return '#FD0909';
  if (docId.startsWith('gov_publications-') || docId.startsWith('news-')) return '#61B233';
  return '#000000';
}

interface SearchDocumentData {
  docId: string;
  docTitle: string;
  query: string;
  chunkCount: number;
  keywordFallback: boolean;
  latencyMs: number;
}

export function SearchDocumentPane({ data }: { data: SearchDocumentData }) {
  const color = docColor(data.docId);

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">Search Document</h2>
          <span className="text-lg text-neutral-400 font-normal">{data.latencyMs}ms</span>
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Doc</span>
          <span className="text-sm text-neutral-600 truncate">{data.docTitle}</span>
        </div>
      </div>

      {/* Query */}
      <div className="mb-4">
        <div className="flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Q</span>
          <span className="text-sm text-neutral-600">{data.query}</span>
        </div>
      </div>

      {/* Result chunks */}
      <h3 className="text-base font-bold text-neutral-900 mb-2">
        {data.chunkCount} Chunks Matched
      </h3>
      <div className="flex flex-wrap" style={{ gap: GAP }}>
        {Array.from({ length: data.chunkCount }, (_, i) => (
          <div
            key={i}
            style={{
              width: CELL,
              height: CELL,
              borderRadius: 3,
              backgroundColor: color,
            }}
          />
        ))}
      </div>

      {/* Keyword fallback badge */}
      {data.keywordFallback && (
        <div className="mt-4 inline-flex self-start rounded-md bg-amber-50 border border-amber-200 px-2.5 py-1">
          <span className="text-xs font-medium text-amber-700">Keyword fallback</span>
        </div>
      )}
    </div>
  );
}
