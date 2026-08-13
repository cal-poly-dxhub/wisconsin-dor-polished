'use client';

import { useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Check, ExternalLink } from 'lucide-react';
import type { ResourceItem } from '@/stores/types';
import { useChatStore } from '@/stores/chat-store';
import { useFeedbackStore } from '@/stores/feedback-store';
import { Textarea } from '@/components/ui/textarea';
import { toSourceEntries, parseInlineLinks } from './sources';
import { SourceNotes } from './source-notes';
import { YesNo } from './yes-no';
import { RequiredMark } from './field-marker';

function Reveal({ show, children }: { show: boolean; children: React.ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.16 }}
          className="overflow-hidden"
        >
          <div className="pt-2.5">{children}</div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function SourcesSection({
  queryId,
  items,
}: {
  queryId: string;
  items: ResourceItem[];
}) {
  const entries = toSourceEntries(items);
  const responseContent = useChatStore((s) => s.queries[queryId]?.response.content);
  const links = useMemo(() => parseInlineLinks(responseContent), [responseContent]);

  const draft = useFeedbackStore((s) => s.getDraft(queryId));
  const setSourcesOk = useFeedbackStore((s) => s.setSourcesOk);
  const setLinksWork = useFeedbackStore((s) => s.setLinksWork);
  const toggleBrokenLink = useFeedbackStore((s) => s.toggleBrokenLink);
  const setBrokenLinksReason = useFeedbackStore((s) => s.setBrokenLinksReason);

  return (
    <div className="space-y-6">
      {/* Q1 — were the sources accurate and current? (no reveals per-source notes) */}
      <div>
        <div className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
            Were the sources accurate, current, and relevant?
            <RequiredMark />
          </span>
          <YesNo
            value={draft.sourcesOk ?? ''}
            ariaLabel="Were the sources accurate, current, and relevant?"
            onChange={(v) => setSourcesOk(queryId, v)}
          />
        </div>
        <Reveal show={draft.sourcesOk === 'no'}>
          <SourceNotes queryId={queryId} entries={entries} />
        </Reveal>
      </div>

      {/* Q2 — did all links work? */}
      <div>
        <div className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
            Did all links work?
            <RequiredMark />
          </span>
          <YesNo
            value={draft.linksWork ?? ''}
            ariaLabel="Did all links work?"
            onChange={(v) => setLinksWork(queryId, v)}
          />
        </div>
        <Reveal show={draft.linksWork === 'no'}>
          <div className="space-y-2 rounded-md border border-border bg-background p-2">
            {links.length === 0 ? (
              <p className="px-1 py-1 text-xs text-muted-foreground">
                No inline links were found in this response.
              </p>
            ) : (
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {links.map((link) => {
                  const selected = (draft.brokenLinkIds ?? []).includes(link.id);
                  return (
                    <button
                      key={link.id}
                      type="button"
                      onClick={() => toggleBrokenLink(queryId, link.id)}
                      className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs transition-colors cursor-pointer ${
                        selected
                          ? 'border-red-400/60 bg-red-500/10 text-foreground'
                          : 'border-border hover:border-muted-foreground/40'
                      }`}
                    >
                      <span
                        className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border ${
                          selected ? 'border-red-400 bg-red-500 text-white' : 'border-muted-foreground/40'
                        }`}
                      >
                        {selected && <Check className="h-2.5 w-2.5" />}
                      </span>
                      <ExternalLink className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{link.label}</span>
                    </button>
                  );
                })}
              </div>
            )}
            <Textarea
              value={draft.brokenLinksReason}
              onChange={(e) => setBrokenLinksReason(queryId, e.target.value)}
              placeholder="Which link failed and why? Wrong page, dead link, etc."
              className="min-h-[52px] resize-none border-0 bg-transparent p-1 shadow-none focus-visible:ring-0"
            />
          </div>
        </Reveal>
      </div>
    </div>
  );
}
