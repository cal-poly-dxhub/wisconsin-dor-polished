'use client';

import { Plus, Trash2, ChevronDown } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { useFeedbackStore } from '@/stores/feedback-store';
import { YesNo } from './yes-no';
import type { SourceEntry } from './sources';

export function SourceNotes({
  queryId,
  entries,
}: {
  queryId: string;
  entries: SourceEntry[];
}) {
  const notes = useFeedbackStore((s) => s.getDraft(queryId).sourceNotes);
  const addSourceNote = useFeedbackStore((s) => s.addSourceNote);
  const updateSourceNote = useFeedbackStore((s) => s.updateSourceNote);
  const removeSourceNote = useFeedbackStore((s) => s.removeSourceNote);

  const titleFor = (id: string) => entries.find((e) => e.id === id)?.title;

  return (
    <div className="space-y-3">
      <AnimatePresence initial={false}>
        {notes.map((note) => (
          <motion.div
            key={note.id}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="space-y-3 rounded-lg border border-border bg-background/50 p-3">
              <div className="flex items-center gap-2">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className="flex flex-1 items-center justify-between gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-left text-xs text-foreground cursor-pointer hover:border-muted-foreground/40"
                    >
                      <span className={`truncate ${note.sourceId ? '' : 'text-muted-foreground'}`}>
                        {titleFor(note.sourceId) ?? 'Choose a source'}
                      </span>
                      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="max-h-64 w-[var(--radix-dropdown-menu-trigger-width)] overflow-y-auto">
                    {entries.map((e) => (
                      <DropdownMenuItem
                        key={e.id}
                        onClick={() => updateSourceNote(queryId, note.id, { sourceId: e.id })}
                      >
                        <span className="truncate">{e.title}</span>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
                <button
                  type="button"
                  aria-label="Remove source feedback"
                  onClick={() => removeSourceNote(queryId, note.id)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-2.5 px-0.5">
                <div className="flex items-center justify-between gap-4">
                  <span className="text-sm font-medium text-foreground">
                    Was everything relevant from this source cited?
                  </span>
                  <YesNo
                    value={note.citedFully}
                    ariaLabel="Was everything relevant from this source cited?"
                    onChange={(v) => updateSourceNote(queryId, note.id, { citedFully: v })}
                  />
                </div>
                <AnimatePresence initial={false}>
                  {note.citedFully === 'no' && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.16 }}
                      className="overflow-hidden"
                    >
                      <Textarea
                        value={note.missedDetail ?? ''}
                        onChange={(e) => updateSourceNote(queryId, note.id, { missedDetail: e.target.value })}
                        placeholder="What should have been cited?"
                        className="min-h-[56px] resize-none"
                        autoFocus
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              <input
                value={note.comment}
                onChange={(e) => updateSourceNote(queryId, note.id, { comment: e.target.value })}
                placeholder="Any further comments"
                className="w-full rounded-md border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
          </motion.div>
        ))}
      </AnimatePresence>

      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => addSourceNote(queryId)}
        disabled={entries.length === 0}
      >
        <Plus className="h-4 w-4" />
        {notes.length === 0 ? 'Add source feedback' : 'Add feedback for another source'}
      </Button>
    </div>
  );
}
