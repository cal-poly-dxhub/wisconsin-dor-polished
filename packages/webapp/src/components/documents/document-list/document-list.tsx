'use client';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import type { Document } from '../document-card/document-card';
import { DocumentCard } from '../document-card/document-card';

interface DocumentListProps {
  documents: Document[];
  title?: string;
}

export type { Document };

const getLayoutClasses = (isNarrowLayout: boolean) => {
  const baseClasses =
    'grid gap-6 thin-scrollbar scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50';

  if (isNarrowLayout) {
    return `${baseClasses} grid-flow-col auto-cols-[minmax(20rem,1fr)] overflow-x-auto overflow-y-hidden py-4 px-2 relative`;
  }

  return `${baseClasses} grid-cols-1 min-h-0 overflow-x-hidden overflow-y-auto py-3 pr-4 content-start relative`;
};

export function DocumentList({ documents, title }: DocumentListProps) {
  const breakpoint = useBreakpoint();
  const isVerticalLayout = breakpoint === 'wide';
  const isNarrowLayout = breakpoint === 'narrow';

  return (
    <div className="relative grid h-full grid-rows-[auto_1fr] font-sans">
      {title ? (
        <div className={`mb-6 ${isVerticalLayout ? 'block' : 'hidden'}`}>
          <h1 className="text-3xl font-bold">{title}</h1>
          <p className="text-muted-foreground mt-1">
            {documents.length} documents
          </p>
        </div>
      ) : (
        <div className="mb-6">
          <p className="text-muted-foreground">{documents.length} documents</p>
        </div>
      )}

      <div className="relative flex min-h-0 w-full flex-col overflow-x-auto">
        <div className={`${getLayoutClasses(isNarrowLayout)} min-h-0 flex-1`}>
          {documents.map(document => (
            <div
              key={document.documentId}
              className={isNarrowLayout ? 'w-80 min-w-[20rem]' : 'w-full'}
            >
              <DocumentCard document={document} />
            </div>
          ))}
        </div>

        {/* Fade-off effect for vertical scrolling */}
        {!isNarrowLayout && (
          <>
            <div className="from-background pointer-events-none absolute top-0 right-0 left-0 z-10 h-3 bg-gradient-to-b to-transparent" />
            <div className="from-background pointer-events-none absolute right-0 bottom-0 left-0 z-10 h-3 bg-gradient-to-t to-transparent" />
          </>
        )}
      </div>

      {/* Fade-off effect for horizontal scrolling */}
      {isNarrowLayout && (
        <>
          <div className="from-background pointer-events-none absolute top-[calc(100%-100%)] bottom-0 left-0 z-10 w-3 bg-gradient-to-r to-transparent" />
          <div className="from-background pointer-events-none absolute top-[calc(100%-100%)] right-0 bottom-0 z-10 w-3 bg-gradient-to-l to-transparent" />
        </>
      )}
    </div>
  );
}
