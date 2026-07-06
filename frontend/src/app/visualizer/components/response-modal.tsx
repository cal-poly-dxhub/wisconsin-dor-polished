'use client';

import { useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { StreamResponse, InlineSources } from '@/components/messages/chat-message';
import { parseInlineCitations } from '@/lib/parse-inline-citations';
import type { ResourceItem } from '@/stores/types';
import '@/components/messages/chat-message.css';

interface ResponseModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  answerText: string;
  answerComplete: boolean;
  docUrls: Record<string, string>;
  resourceItems: ResourceItem[];
  query?: string | null;
}

export function ResponseModal({
  open,
  onOpenChange,
  answerText,
  answerComplete,
  docUrls,
  resourceItems,
  query,
}: ResponseModalProps) {
  const citationsByDoc = useMemo(() => {
    if (!answerText) return new Map();
    return parseInlineCitations(answerText);
  }, [answerText]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="!max-w-[min(92vw,80rem)] w-full max-h-[88vh] overflow-hidden flex flex-col p-0 gap-0">
        <DialogHeader className="px-6 pt-6 pb-4 border-b border-border/40 shrink-0">
          <DialogTitle className="text-base font-medium tracking-tight">
            Chat response
          </DialogTitle>
          {query && (
            <p className="text-sm text-muted-foreground leading-snug text-left pt-1">
              &ldquo;{query}&rdquo;
            </p>
          )}
        </DialogHeader>
        <div className="flex-1 overflow-y-auto px-6 py-5 min-h-0">
          {answerText ? (
            <div className="chat-response-aligned">
              <StreamResponse
                content={answerText}
                streamingComplete={answerComplete}
                docUrls={docUrls}
              />
              <InlineSources
                items={resourceItems}
                streamingComplete={answerComplete}
                citationsByDoc={citationsByDoc}
              />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Waiting for response...</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
