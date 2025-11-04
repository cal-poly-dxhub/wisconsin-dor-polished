'use client';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import type { Document } from '../document-card/document-card';
import { DocumentCard } from '../document-card/document-card';
import type { FAQ } from '../document-card/faq-card';
import { FAQCard } from '../document-card/faq-card';

type DocumentItem = { type: 'document'; data: Document };
type FAQItem = { type: 'faq'; data: FAQ };
type ListItem = DocumentItem | FAQItem;

interface DocumentListProps {
  items: ListItem[];
  title?: string;
}

export type { Document, FAQ, ListItem, DocumentItem, FAQItem };

const getLayoutClasses = (isNarrowLayout: boolean) => {
  const baseClasses =
    'grid gap-6 thin-scrollbar scrollbar-thin scrollbar-track-transparent scrollbar-thumb-gray-300/30 hover:scrollbar-thumb-gray-400/50 dark:scrollbar-thumb-gray-600/30 dark:hover:scrollbar-thumb-gray-500/50';

  if (isNarrowLayout) {
    return `${baseClasses} grid-flow-col auto-cols-[minmax(20rem,1fr)] overflow-x-auto overflow-y-hidden py-4 px-2 relative`;
  }

  return `${baseClasses} grid-cols-1 min-h-0 overflow-x-hidden overflow-y-auto py-3 pr-4 content-start relative`;
};

export function DocumentList({ items, title }: DocumentListProps) {
  const breakpoint = useBreakpoint();
  const isVerticalLayout = breakpoint === 'wide';
  const isNarrowLayout = breakpoint === 'narrow';

  const documentCount = items.filter(item => item.type === 'document').length;
  const faqCount = items.filter(item => item.type === 'faq').length;
  const itemCountText =
    documentCount && faqCount
      ? `${documentCount} documents, ${faqCount} FAQs`
      : documentCount
      ? `${documentCount} documents`
      : `${faqCount} FAQs`;

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
              item.type === 'document' ? item.data.documentId : item.data.faqId;
            return (
              <div
                key={key}
                className={isNarrowLayout ? 'w-80 min-w-[20rem]' : 'w-full'}
              >
                {item.type === 'document' ? (
                  <DocumentCard document={item.data} />
                ) : (
                  <FAQCard faq={item.data} />
                )}
              </div>
            );
          })}
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
