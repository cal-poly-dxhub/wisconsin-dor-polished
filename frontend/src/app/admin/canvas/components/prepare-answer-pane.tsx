'use client';

interface PrepareAnswerData {
  citedDocCount: number;
  discoveryTitles: Record<string, string>;
  turnsUsed: number;
  elapsedMs: number;
}

function docColor(docId: string): string {
  if (docId.startsWith('wpam-')) return '#3958E3';
  if (docId.startsWith('admin_rules-')) return '#FD8709';
  if (docId.startsWith('statutes-')) return '#FD0909';
  if (docId.startsWith('gov_publications-') || docId.startsWith('news-')) return '#61B233';
  return '#000000';
}

export function PrepareAnswerPane({ data }: { data: PrepareAnswerData }) {
  const entries = Object.entries(data.discoveryTitles);

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-2xl font-bold text-neutral-900">Prepare Answer</h2>
        <p className="mt-1 text-sm text-neutral-500">
          {data.turnsUsed} turns · {(data.elapsedMs / 1000).toFixed(1)}s total
        </p>
      </div>

      {/* Cited docs */}
      <h3 className="text-base font-bold text-neutral-900 mb-3">
        {data.citedDocCount} Cited Sources
      </h3>

      <ul className="space-y-1.5">
        {entries.map(([docId, title]) => (
          <li key={docId} className="flex items-center gap-2">
            <div style={{ width: 12, height: 12, borderRadius: 2, backgroundColor: docColor(docId) }} />
            <span className="text-xs text-neutral-700 truncate">{title}</span>
          </li>
        ))}
      </ul>

      {/* Handoff banner */}
      <div className="mt-5 rounded-md bg-neutral-100 px-3 py-2">
        <p className="text-xs font-medium text-neutral-600 uppercase tracking-wide">
          Handing off to answer stream (Phase B)
        </p>
      </div>
    </div>
  );
}
