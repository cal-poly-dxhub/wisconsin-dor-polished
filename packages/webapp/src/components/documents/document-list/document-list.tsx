'use client';
import type { Document } from '../document-card/document-card';
import { DocumentCard } from '../document-card/document-card';
import type { FAQ } from '../document-card/faq-card';
import { FAQCard } from '../document-card/faq-card';
import type { ResourceItem } from '@/stores/types';
import { memo, useMemo } from 'react';

interface DocumentListProps {
  items: ResourceItem[];
  title?: string;
}

export const DocumentList = memo(function DocumentList({ items = [], title }: DocumentListProps) {
  const { hasItems, itemCountText } = useMemo(() => {
    const documentCount = items.filter(item => item.type === 'document').length;
    const faqCount = items.filter(item => item.type === 'faq').length;
    const hasItems = items.length > 0;

    let itemCountText: string;
    if (documentCount === 0 && faqCount === 0) {
      itemCountText = 'No sources yet';
    } else if (documentCount > 0 && faqCount > 0) {
      itemCountText = `${documentCount} documents, ${faqCount} FAQs`;
    } else if (documentCount > 0) {
      itemCountText = `${documentCount} documents`;
    } else {
      itemCountText = `${faqCount} FAQs`;
    }

    return { hasItems, itemCountText };
  }, [items]);

  return (
    <div className="relative grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] font-sans">
      {title ? (
        <div className="mb-2 hidden xl:block">
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="text-muted-foreground text-sm">{itemCountText}</p>
        </div>
      ) : (
        <div className="mb-6">
          <p className="text-muted-foreground">{itemCountText}</p>
        </div>
      )}

      <div className="relative flex min-h-0 w-full flex-col overflow-hidden">
        <div className="grid gap-6 thin-scrollbar scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50 grid-flow-col auto-cols-[minmax(20rem,1fr)] overflow-x-auto overflow-y-hidden py-4 px-2 relative xl:grid-flow-row xl:grid-cols-1 xl:min-h-0 xl:overflow-x-hidden xl:overflow-y-auto xl:pt-1 xl:pr-2 xl:content-start xl:py-0 xl:px-0 min-h-0 flex-1">
          {!hasItems && (
            <div className="text-muted-foreground flex h-full min-h-40 items-center justify-center rounded-lg border border-dashed p-6 text-center text-sm">
              Sources and FAQs used in an answer will appear here.
            </div>
          )}

          {items.map(item => {
            const key =
              item.type === 'document'
                ? `doc-${(item.data as Document).documentId}`
                : `faq-${(item.data as FAQ).faqId}`;

            return (
              <div
                key={key}
                className="w-80 min-w-[20rem] xl:w-full xl:min-w-0"
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
      </div>
    </div>
  );
});
