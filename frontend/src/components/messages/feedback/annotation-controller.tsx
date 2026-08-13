'use client';

import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react';
import { Check, MessageSquarePlus, Trash2 } from 'lucide-react';
import { useFeedbackStore } from '@/stores/feedback-store';
import { selectionToOffsets, offsetsToRange } from '@/lib/text-annotation';

const HIGHLIGHT_NAME = 'feedback-annotation';
const HOVER_HIGHLIGHT_NAME = 'feedback-annotation-hover';

// Selection anchor in viewport coords: `top`/`bottom` are the selection's
// edges, `left` its horizontal center. The Comment button sits above `top`;
// the comment editor sits below `bottom`.
interface Anchor {
  top: number;
  bottom: number;
  left: number;
}

interface PendingSelection {
  start: number;
  end: number;
  quote: string;
  anchor: Anchor;
}

interface EditorState {
  annotationId: string;
  anchor: Anchor;
}

// Feature-detects the CSS Custom Highlight API. Older browsers simply get no
// visual highlight overlay, but annotation offsets/comments still work.
function highlightApiAvailable(): boolean {
  return typeof CSS !== 'undefined' && 'highlights' in CSS && typeof Highlight !== 'undefined';
}

// The ::highlight(feedback-annotation) rule can't live in a .css file because
// the build's CSS parser rejects the pseudo-element. Inject it once at runtime
// in browsers that support the API, where the native parser accepts it.
const HIGHLIGHT_STYLE_ID = 'feedback-annotation-highlight-style';
function ensureHighlightStyle(): void {
  if (typeof document === 'undefined') return;
  if (document.getElementById(HIGHLIGHT_STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = HIGHLIGHT_STYLE_ID;
  style.textContent = `::highlight(${HIGHLIGHT_NAME}) {
    background-color: rgba(59, 130, 246, 0.22);
    color: inherit;
    text-decoration: underline;
    text-decoration-color: rgba(59, 130, 246, 0.6);
    text-decoration-thickness: 2px;
  }
  ::highlight(${HOVER_HIGHLIGHT_NAME}) {
    background-color: rgba(59, 130, 246, 0.42);
    color: inherit;
    text-decoration: underline;
    text-decoration-color: rgba(59, 130, 246, 0.9);
    text-decoration-thickness: 2px;
  }`;
  document.head.appendChild(style);
}

export function AnnotationController({
  queryId,
  containerRef,
}: {
  queryId: string;
  // The ChatMessage root; the rendered markdown lives at `.markdown-container`.
  containerRef: RefObject<HTMLElement | null>;
}) {
  const annotatingQueryId = useFeedbackStore((s) => s.annotatingQueryId);
  const annotations = useFeedbackStore((s) => s.getDraft(queryId).annotations);
  const addAnnotation = useFeedbackStore((s) => s.addAnnotation);
  const updateAnnotation = useFeedbackStore((s) => s.updateAnnotation);
  const removeAnnotation = useFeedbackStore((s) => s.removeAnnotation);

  const active = annotatingQueryId === queryId;

  const [pending, setPending] = useState<PendingSelection | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [draftComment, setDraftComment] = useState('');

  const getContainer = useCallback((): HTMLElement | null => {
    return containerRef.current?.querySelector<HTMLElement>('.markdown-container') ?? null;
  }, [containerRef]);

  // Which annotation (if any) sits under a viewport point. `::highlight()` takes
  // no pointer events, so hit-testing is done by measuring each annotation's
  // client rects against the cursor.
  const annotationAt = useCallback(
    (container: HTMLElement, x: number, y: number) => {
      for (const a of annotations) {
        const range = offsetsToRange(container, a.startOffset, a.endOffset);
        if (!range) continue;
        for (const r of Array.from(range.getClientRects())) {
          if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
            return { annotation: a, rect: r };
          }
        }
      }
      return null;
    },
    [annotations]
  );

  // Paint existing annotations via the CSS Custom Highlight API — only while
  // this message is being annotated. Highlights are an annotate-mode affordance,
  // not a permanent overlay, and gating on `active` also prevents sibling
  // messages from clobbering the single global CSS.highlights registry entry.
  useLayoutEffect(() => {
    if (!active || !highlightApiAvailable()) return;
    ensureHighlightStyle();
    const container = getContainer();
    if (!container) return;

    const highlight = new Highlight();
    for (const a of annotations) {
      const range = offsetsToRange(container, a.startOffset, a.endOffset);
      if (range) highlight.add(range);
    }
    CSS.highlights.set(HIGHLIGHT_NAME, highlight);

    return () => {
      CSS.highlights.delete(HIGHLIGHT_NAME);
    };
  }, [annotations, getContainer, active]);

  // Capture text selections inside the response while annotate mode is active.
  useEffect(() => {
    if (!active) return;
    const container = getContainer();
    if (!container) return;

    function onMouseUp() {
      const selection = window.getSelection();
      const offsets = selectionToOffsets(container!, selection);
      if (!offsets) {
        setPending(null);
        return;
      }
      const rect = selection!.getRangeAt(0).getBoundingClientRect();
      setEditor(null);
      setPending({
        ...offsets,
        anchor: { top: rect.top, bottom: rect.bottom, left: rect.left + rect.width / 2 },
      });
    }

    container.addEventListener('mouseup', onMouseUp);
    return () => container.removeEventListener('mouseup', onMouseUp);
  }, [active, getContainer]);

  // Clicking an existing highlight opens its editor. Uses caret position → offset
  // to find which annotation was clicked.
  useEffect(() => {
    if (!active) return;
    const container = getContainer();
    if (!container) return;

    function onClick(e: MouseEvent) {
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) return; // a drag-select, not a click
      const hit = annotationAt(container!, e.clientX, e.clientY);
      if (hit) {
        setPending(null);
        setDraftComment(hit.annotation.comment);
        setEditor({
          annotationId: hit.annotation.id,
          anchor: {
            top: hit.rect.top,
            bottom: hit.rect.bottom,
            left: hit.rect.left + hit.rect.width / 2,
          },
        });
      }
    }

    container.addEventListener('click', onClick);
    return () => container.removeEventListener('click', onClick);
  }, [active, annotationAt, getContainer]);

  // Hover feedback: pointer cursor + a brighter highlight layer over the
  // annotation under the cursor. Cleared when the pointer leaves the container.
  useEffect(() => {
    if (!active || !highlightApiAvailable()) return;
    const container = getContainer();
    if (!container) return;

    let hoveredId: string | null = null;

    function paintHover(id: string | null) {
      if (id === hoveredId) return;
      hoveredId = id;
      const hovered = id ? annotations.find((a) => a.id === id) : null;
      if (hovered) {
        const range = offsetsToRange(container!, hovered.startOffset, hovered.endOffset);
        const highlight = new Highlight();
        if (range) highlight.add(range);
        CSS.highlights.set(HOVER_HIGHLIGHT_NAME, highlight);
        container!.style.cursor = 'pointer';
      } else {
        CSS.highlights.delete(HOVER_HIGHLIGHT_NAME);
        container!.style.cursor = '';
      }
    }

    function onMove(e: MouseEvent) {
      // A drag-select shouldn't flip to pointer mid-selection.
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) {
        paintHover(null);
        return;
      }
      const hit = annotationAt(container!, e.clientX, e.clientY);
      paintHover(hit ? hit.annotation.id : null);
    }

    function onLeave() {
      paintHover(null);
    }

    container.addEventListener('mousemove', onMove);
    container.addEventListener('mouseleave', onLeave);
    return () => {
      container.removeEventListener('mousemove', onMove);
      container.removeEventListener('mouseleave', onLeave);
      CSS.highlights.delete(HOVER_HIGHLIGHT_NAME);
      container.style.cursor = '';
    };
  }, [active, annotationAt, annotations, getContainer]);

  if (!active) return null;

  function commitNew() {
    if (!pending) return;
    const id = addAnnotation(queryId, {
      startOffset: pending.start,
      endOffset: pending.end,
      quote: pending.quote,
      comment: '',
    });
    setDraftComment('');
    setEditor({ annotationId: id, anchor: pending.anchor });
    setPending(null);
    window.getSelection()?.removeAllRanges();
  }

  function saveEditor() {
    if (!editor) return;
    updateAnnotation(queryId, editor.annotationId, { comment: draftComment.trim() });
    // Discard an annotation the user opened but left with no comment.
    if (draftComment.trim() === '') {
      removeAnnotation(queryId, editor.annotationId);
    }
    setEditor(null);
    setDraftComment('');
  }

  function deleteEditor() {
    if (!editor) return;
    removeAnnotation(queryId, editor.annotationId);
    setEditor(null);
    setDraftComment('');
  }

  return (
    <>
      {pending && (
        <button
          type="button"
          onClick={commitNew}
          style={{ position: 'fixed', top: pending.anchor.top - 40, left: pending.anchor.left, transform: 'translateX(-50%)', zIndex: 60 }}
          className="flex items-center gap-1.5 rounded-md bg-blue-600 px-2.5 py-1.5 text-xs font-medium text-white shadow-lg transition-colors hover:bg-blue-500 cursor-pointer"
        >
          <MessageSquarePlus className="h-3.5 w-3.5" />
          Comment
        </button>
      )}

      {editor && (
        <div
          style={{ position: 'fixed', top: editor.anchor.bottom + 8, left: editor.anchor.left, transform: 'translateX(-50%)', zIndex: 60 }}
          className="w-64 rounded-lg border border-border bg-popover p-2.5 shadow-xl"
        >
          <textarea
            value={draftComment}
            onChange={(e) => setDraftComment(e.target.value)}
            placeholder="Add a comment"
            autoFocus
            className="min-h-[64px] w-full resize-none rounded-md border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="mt-2 flex items-center justify-between">
            <button
              type="button"
              onClick={deleteEditor}
              aria-label="Delete annotation"
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-destructive cursor-pointer"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={saveEditor}
              className="flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 cursor-pointer"
            >
              <Check className="h-3.5 w-3.5" />
              Save
            </button>
          </div>
        </div>
      )}
    </>
  );
}
