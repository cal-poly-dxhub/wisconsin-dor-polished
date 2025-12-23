'use client';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import type { Document } from '../document-card/document-card';
import { DocumentCard } from '../document-card/document-card';
import type { FAQ } from '../document-card/faq-card';
import { FAQCard } from '../document-card/faq-card';
import type { ResourceItem } from '@/stores/types';

interface DocumentListProps {
  items: ResourceItem[];
  title?: string;
}

const getLayoutClasses = (isNarrowLayout: boolean) => {
  const baseClasses =
    'grid gap-6 thin-scrollbar scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50';

  if (isNarrowLayout) {
    return `${baseClasses} grid-flow-col auto-cols-[minmax(20rem,1fr)] overflow-x-auto overflow-y-hidden py-4 px-2 relative`;
  }

  return `${baseClasses} grid-cols-1 min-h-0 overflow-x-hidden overflow-y-auto py-3 pr-4 content-start relative`;
};

export function DocumentList({ items = [], title }: DocumentListProps) {
  const breakpoint = useBreakpoint();
  const isVerticalLayout = breakpoint === 'wide';
  const isNarrowLayout = breakpoint === 'narrow';

  const documentCount = items.filter(item => item.type === 'document').length;
  const faqCount = items.filter(item => item.type === 'faq').length;
  const itemCountText = (() => {
    if (documentCount === 0 && faqCount === 0) {
      return '0 documents, 0 FAQs';
    }

    if (documentCount > 0 && faqCount > 0) {
      return `${documentCount} documents, ${faqCount} FAQs`;
    }

    if (documentCount > 0) {
      return `${documentCount} documents`;
    }

    return `${faqCount} FAQs`;
  })();

  return (
    <div className="relative grid h-full grid-rows-[auto_1fr] font-sans">
      {title ? (
        <div className={`mb-6 ${isVerticalLayout ? 'block' : 'hidden'}`}>
          <h1 className="text-3xl font-bold">{title}</h1>
          <p className="text-muted-foreground mt-1">{itemCountText}</p>
        </div>
      ) : (
        <div className="mb-6">
          <p className="text-muted-foreground">{itemCountText}</p>
        </div>
      )}

      <div className="relative flex min-h-0 w-full flex-col overflow-x-auto">
        <div className={`${getLayoutClasses(isNarrowLayout)} min-h-0 flex-1`}>
          {items.map(item => {
            const key =
              item.type === 'document'
                ? `doc-${(item.data as Document).documentId}`
                : `faq-${(item.data as FAQ).faqId}`;

            return (
              <div
                key={key}
                className={isNarrowLayout ? 'w-80 min-w-[20rem]' : 'w-full'}
              >
                {item.type === 'document' ? (
                  <DocumentCard document={item.data as Document} />
                ) : (
                  <FAQCard faq={item.data as FAQ} />
                )}
              </div>
            );
          })}
        </div>

        {/* Fade-off effect for vertical scrolling */}
      </div>

      {/* Fade-off effect for horizontal scrolling */}
    </div>
  );
}
