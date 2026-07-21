'use client';

interface ListSectionsData {
  docTitle: string;
  sectionCount: number;
  sectionHeadings: string[];
  latencyMs: number;
}

export function ListSectionsPane({ data }: { data: ListSectionsData }) {
  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-2xl font-bold text-neutral-900">List Sections</h2>
          <span className="text-lg text-neutral-400 font-normal">{data.latencyMs}ms</span>
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-full border border-neutral-200 px-3 py-1.5">
          <span className="text-sm font-semibold text-neutral-500">Doc</span>
          <span className="text-sm text-neutral-600 truncate">{data.docTitle}</span>
        </div>
      </div>

      {/* Section count */}
      <h3 className="text-base font-bold text-neutral-900 mb-3">
        {data.sectionCount} Sections Found
      </h3>

      {/* TOC list */}
      <ol className="space-y-1.5">
        {data.sectionHeadings.map((heading, i) => (
          <li key={i} className="flex items-baseline gap-2">
            <span className="shrink-0 text-xs font-mono text-neutral-400 w-5 text-right">{i + 1}.</span>
            <span className="text-sm text-neutral-700">{heading}</span>
          </li>
        ))}
        {data.sectionCount > data.sectionHeadings.length && (
          <li className="flex items-baseline gap-2">
            <span className="shrink-0 text-xs font-mono text-neutral-400 w-5 text-right" />
            <span className="text-xs text-neutral-400">
              +{data.sectionCount - data.sectionHeadings.length} more
            </span>
          </li>
        )}
      </ol>
    </div>
  );
}
