'use client';
import { useBreakpoint } from '@/hooks/use-breakpoint';
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
  const breakpoint = useBreakpoint();
  const isVerticalLayout = breakpoint === 'wide';
  const isNarrowLayout = breakpoint === 'narrow';

  const { hasItems, itemCountText, layoutClasses } = useMemo(() => {
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

    const baseClasses =
      'grid gap-6 thin-scrollbar scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50';

    const layoutClasses = isNarrowLayout
      ? `${baseClasses} grid-flow-col auto-cols-[minmax(20rem,1fr)] overflow-x-auto overflow-y-hidden py-4 px-2 relative`
      : `${baseClasses} grid-cols-1 min-h-0 overflow-x-hidden overflow-y-auto pt-1 pr-2 content-start relative`;

    return { documentCount, faqCount, hasItems, itemCountText, layoutClasses };
  }, [items, isNarrowLayout]);

  return (
    <div className="relative grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] font-sans">
      {title ? (
        <div className={`mb-2 ${isVerticalLayout ? 'block' : 'hidden'}`}>
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="text-muted-foreground text-sm">{itemCountText}</p>
        </div>
      ) : (
        <div className="mb-6">
          <p className="text-muted-foreground">{itemCountText}</p>
        </div>
      )}

      <div className="relative flex min-h-0 w-full flex-col overflow-hidden">
        <div className={`${layoutClasses} min-h-0 flex-1`}>
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
});
