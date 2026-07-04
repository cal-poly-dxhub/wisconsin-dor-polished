'use client';

import { useState, useRef } from 'react';
import { ThumbsUp, ThumbsDown, FileText, HelpCircle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import type { ResourceItem } from '@/stores/types';

interface MentionOption {
  label: string;
  type: 'document' | 'faq';
}

function buildMentionOptions(items: ResourceItem[]): MentionOption[] {
  return items.map(item => {
    if (item.type === 'document') {
      const doc = item.data as { title: string };
      return { label: doc.title, type: 'document' as const };
    }
    const faq = item.data as { question: string };
    const q = faq.question.length > 60 ? faq.question.slice(0, 57) + '...' : faq.question;
    return { label: q, type: 'faq' as const };
  });
}

interface FeedbackModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  type: 'up' | 'down';
  onSubmit: (feedback?: string) => void;
  items?: ResourceItem[];
}

export function FeedbackModal({
  open,
  onOpenChange,
  type,
  onSubmit,
  items = [],
}: FeedbackModalProps) {
  const [feedback, setFeedback] = useState('');
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [cursorAtSign, setCursorAtSign] = useState<number | null>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const maxLength = 500;
  const remainingChars = maxLength - feedback.length;

  const mentionOptions = buildMentionOptions(items);
  const filtered = mentionQuery !== null
    ? mentionOptions.filter(o => o.label.toLowerCase().includes(mentionQuery.toLowerCase()))
    : [];

  function computeMenuPosition(atIndex: number, text: string) {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const mirror = mirrorRef.current;
    if (!mirror) return;

    const style = window.getComputedStyle(textarea);
    mirror.style.font = style.font;
    mirror.style.fontSize = style.fontSize;
    mirror.style.lineHeight = style.lineHeight;
    mirror.style.padding = style.padding;
    mirror.style.border = style.border;
    mirror.style.width = style.width;
    mirror.style.whiteSpace = 'pre-wrap';
    mirror.style.wordWrap = 'break-word';
    mirror.style.position = 'absolute';
    mirror.style.visibility = 'hidden';
    mirror.style.left = '-9999px';

    const textBefore = text.slice(0, atIndex);
    mirror.textContent = textBefore;
    const span = document.createElement('span');
    span.textContent = '@';
    mirror.appendChild(span);

    const spanRect = span.getBoundingClientRect();
    const mirrorRect = mirror.getBoundingClientRect();

    const top = spanRect.top - mirrorRect.top - textarea.scrollTop + textarea.offsetTop + span.offsetHeight + 4;
    const left = spanRect.left - mirrorRect.left + textarea.offsetLeft;

    setMenuPos({ top, left: Math.min(left, textarea.offsetWidth - 50) });
    mirror.textContent = '';
  }

  function closeMention() {
    setMentionQuery(null);
    setMentionIndex(0);
    setCursorAtSign(null);
    setMenuPos(null);
  }

  function insertMention(option: MentionOption) {
    if (cursorAtSign === null) return;
    const before = feedback.slice(0, cursorAtSign);
    const afterCursor = textareaRef.current?.selectionStart ?? feedback.length;
    const after = feedback.slice(afterCursor);
    const mention = `@[${option.label}]`;
    const newValue = before + mention + after;
    if (newValue.length <= maxLength) {
      setFeedback(newValue);
      setTimeout(() => {
        const pos = before.length + mention.length;
        textareaRef.current?.setSelectionRange(pos, pos);
        textareaRef.current?.focus();
      }, 0);
    }
    closeMention();
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    if (value.length > maxLength) return;
    setFeedback(value);

    const cursor = e.target.selectionStart ?? value.length;
    const textBeforeCursor = value.slice(0, cursor);
    const atIdx = textBeforeCursor.lastIndexOf('@');

    if (atIdx >= 0) {
      const charBefore = atIdx > 0 ? textBeforeCursor[atIdx - 1] : ' ';
      if (charBefore === ' ' || charBefore === '\n' || atIdx === 0) {
        const query = textBeforeCursor.slice(atIdx + 1);
        if (!query.includes(' ') || query.length < 30) {
          setMentionQuery(query);
          setMentionIndex(0);
          setCursorAtSign(atIdx);
          computeMenuPosition(atIdx, value);
          return;
        }
      }
    }
    closeMention();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionQuery === null || filtered.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setMentionIndex(i => (i + 1) % filtered.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setMentionIndex(i => (i - 1 + filtered.length) % filtered.length);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      insertMention(filtered[mentionIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeMention();
    }
  };

  const handleSubmit = () => {
    onSubmit(feedback || undefined);
    setFeedback('');
    onOpenChange(false);
  };

  const handleClose = () => {
    setFeedback('');
    closeMention();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) handleClose(); else onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {type === 'up' ? (
              <ThumbsUp className="h-5 w-5 text-green-500" />
            ) : (
              <ThumbsDown className="h-5 w-5 text-red-500" />
            )}
            {type === 'up' ? 'Helpful response' : 'Unhelpful response'}
          </DialogTitle>
          <DialogDescription>
            {type === 'up'
              ? 'Glad this was helpful! Any additional feedback?'
              : 'Sorry about that. What could be improved?'}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-4">
          <div className="relative space-y-2">
            <textarea
              ref={textareaRef}
              value={feedback}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              placeholder={
                items.length > 0
                  ? 'Type @ to cite a document...'
                  : type === 'up'
                    ? 'What did you find helpful? (optional)'
                    : 'What was wrong or missing?'
              }
              className="min-h-[200px] w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
              autoFocus
            />
            <div ref={mirrorRef} aria-hidden="true" />

            {mentionQuery !== null && filtered.length > 0 && menuPos && (
              <div
                ref={menuRef}
                className="absolute z-50 max-h-48 w-[280px] overflow-y-auto rounded-md border border-border bg-popover shadow-lg"
                style={{ top: menuPos.top, left: menuPos.left }}
              >
                {filtered.map((option, i) => (
                  <button
                    key={`${option.type}-${option.label}`}
                    className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm cursor-pointer transition-colors ${
                      i === mentionIndex
                        ? 'bg-accent text-accent-foreground'
                        : 'hover:bg-muted'
                    }`}
                    onMouseDown={e => {
                      e.preventDefault();
                      insertMention(option);
                    }}
                    onMouseEnter={() => setMentionIndex(i)}
                  >
                    {option.type === 'document' ? (
                      <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    ) : (
                      <HelpCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    )}
                    <span className="truncate">{option.label}</span>
                  </button>
                ))}
              </div>
            )}

            <div className="text-xs text-muted-foreground text-right">
              {remainingChars}/{maxLength}
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <button
              onClick={handleClose}
              className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted cursor-pointer"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 cursor-pointer"
            >
              Submit
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
