'use client';

import { useEffect, useState } from 'react';
import type { FAQ } from '@messages/websocket-interface';
import type { ResourceItem } from '@/stores/types';
import { buildResolverUrl } from '@/lib/citation-resolver';
import { appendPageFragment, chooseSourceTarget } from '@/components/documents/document-card/source-target';
import type { Document } from '@/components/documents/document-card/document-card';

export function useDocUrlMap(resourceItems: ResourceItem[]): Record<string, string> {
  const [docUrls, setDocUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;

    async function build() {
      const map: Record<string, string> = {};
      for (const item of resourceItems) {
        if (item.type === 'document') {
          const doc = item.data as Document;
          let url: string | undefined;
          const target = chooseSourceTarget(doc);
          if (target?.kind === 'url') {
            url = appendPageFragment(target.url, doc.startPage);
          } else if (target?.kind === 's3') {
            url = (await buildResolverUrl(target.s3Key, doc.startPage)) ?? undefined;
          }
          if (url) {
            map[doc.documentId] = url;
            const rawId = doc.documentId.replace(/-[a-f0-9]{7}$/, '');
            if (rawId !== doc.documentId) map[rawId] = url;
          }
        } else if (item.type === 'faq') {
          const faq = item.data as FAQ;
          if (faq.sourceUrl) {
            map[faq.faqId] = faq.sourceUrl;
          }
        }
      }
      if (!cancelled) setDocUrls(map);
    }

    build();
    return () => {
      cancelled = true;
    };
  }, [resourceItems]);

  return docUrls;
}
