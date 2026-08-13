'use client';

import { Highlighter, Pencil, Trash2 } from 'lucide-react';
import { useFeedbackStore } from '@/stores/feedback-store';

export function AnnotateCta({ queryId }: { queryId: string }) {
  const count = useFeedbackStore((s) => s.getDraft(queryId).annotations.length);
  const enterAnnotate = useFeedbackStore((s) => s.enterAnnotate);
  const clearAnnotations = useFeedbackStore((s) => s.clearAnnotations);

  if (count === 0) {
    return (
      <button
        type="button"
        onClick={() => enterAnnotate(queryId)}
        className="flex w-full items-center gap-3 rounded-lg border border-dashed border-border px-3.5 py-3 text-left transition-colors hover:border-muted-foreground/40 hover:bg-muted/40 cursor-pointer"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-500/10 text-blue-500">
          <Highlighter className="h-[18px] w-[18px]" />
        </span>
        <span>
          <span className="block text-sm font-medium text-foreground">Annotate the response</span>
          <span className="block text-xs text-muted-foreground">
            Highlight any part of the answer to leave a comment
          </span>
        </span>
      </button>
    );
  }

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-blue-400/40 bg-blue-500/5 px-3.5 py-3">
      <span className="flex items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-500/15 text-blue-500">
          <Highlighter className="h-[18px] w-[18px]" />
        </span>
        <span className="text-sm font-medium text-foreground">
          {count} annotation{count === 1 ? '' : 's'} created
        </span>
      </span>
      <span className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => enterAnnotate(queryId)}
          className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium text-blue-500 transition-colors hover:bg-blue-500/10 cursor-pointer"
        >
          <Pencil className="h-3.5 w-3.5" />
          Edit again
        </button>
        <button
          type="button"
          onClick={() => clearAnnotations(queryId)}
          aria-label="Clear all annotations"
          title="Clear all annotations"
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-destructive cursor-pointer"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </span>
    </div>
  );
}
