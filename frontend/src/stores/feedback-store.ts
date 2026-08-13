'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// Three-way rating used by the top-level "usable as-is?" question.
export type Thumb = 'up' | 'mid' | 'down';

// Every other question in the form is a Yes/No.
export type YesNo = 'yes' | 'no';

// A yes/no answer with a comment that's only surfaced when the answer is "no".
export interface YesNoAnswer {
  answer: YesNo | null;
  comment: string;
}

export interface SourceNote {
  id: string;
  sourceId: string;
  // "Was everything relevant from this source cited?" — "yes" is positive;
  // "no" reveals the detail of what was missed.
  citedFully: string; // '' | 'yes' | 'no'
  // When citedFully === 'no': free-text of what should have been cited.
  missedDetail: string;
  comment: string;
}

export interface Annotation {
  id: string;
  startOffset: number;
  endOffset: number;
  quote: string;
  comment: string;
}

export interface FeedbackDraft {
  queryId: string;
  rating: Thumb | null;
  positiveComment: string;

  // Response — three yes/no questions, comment revealed on "no".
  response: Record<string, YesNoAnswer>;

  // Sources — two required yes/no questions.
  // "Were the sources accurate, current, and relevant?" — "yes" is positive;
  // "no" reveals the per-source feedback (content, versioning, relevance).
  sourcesOk: YesNo | null;
  sourceNotes: SourceNote[]; // revealed when sourcesOk === 'no'
  linksWork: YesNo | null; // "Did all links work?"
  brokenLinkIds: string[]; // revealed when linksWork === 'no'
  brokenLinksReason: string;

  annotations: Annotation[];

  // Speed — one optional yes/no question, comment revealed on "no".
  speedTimely: YesNo | null;
  speedComment: string;

  submittedAt?: string;
}

// Single place to declare the Response yes/no questions. Add more here and the
// Response section renders + validates them automatically. Phrased so "Yes" is
// the good answer and "No" reveals a comment box.
export const SUBSECTIONS: { key: string; label: string }[] = [
  { key: 'relevance', label: 'Did the answer address your question?' },
  { key: 'tone', label: 'Was the tone appropriate?' },
  { key: 'accuracy', label: 'Was the answer accurate?' },
];

function emptyResponse(): Record<string, YesNoAnswer> {
  const out: Record<string, YesNoAnswer> = {};
  for (const { key } of SUBSECTIONS) out[key] = { answer: null, comment: '' };
  return out;
}

function emptyDraft(queryId: string): FeedbackDraft {
  return {
    queryId,
    rating: null,
    positiveComment: '',
    response: emptyResponse(),
    sourcesOk: null,
    sourceNotes: [],
    linksWork: null,
    brokenLinkIds: [],
    brokenLinksReason: '',
    annotations: [],
    speedTimely: null,
    speedComment: '',
  };
}

// True if the user has entered anything into a draft (any rating, comment,
// note, or annotation) — used to show an "unsaved draft" indicator on messages
// whose feedback hasn't been submitted yet.
export function hasDraftContent(d: FeedbackDraft | undefined): boolean {
  if (!d) return false;
  const filled = (s: string | undefined) => (s ?? '').trim() !== '';
  return (
    d.rating != null ||
    filled(d.positiveComment) ||
    Object.values(d.response ?? {}).some((r) => r?.answer != null || filled(r?.comment)) ||
    d.sourcesOk != null ||
    (d.sourceNotes?.length ?? 0) > 0 ||
    d.linksWork != null ||
    (d.brokenLinkIds?.length ?? 0) > 0 ||
    filled(d.brokenLinksReason) ||
    (d.annotations?.length ?? 0) > 0 ||
    d.speedTimely != null ||
    filled(d.speedComment)
  );
}

// Stable per-query empty drafts. Selectors that read a draft before one is
// created (the modal is mounted for every message) must return the SAME object
// reference across renders — otherwise zustand v5's useSyncExternalStore sees a
// changing snapshot and throws "getSnapshot should be cached" in a render loop.
const emptyDraftCache = new Map<string, FeedbackDraft>();
function stableEmptyDraft(queryId: string): FeedbackDraft {
  let draft = emptyDraftCache.get(queryId);
  if (!draft) {
    draft = emptyDraft(queryId);
    emptyDraftCache.set(queryId, draft);
  }
  return draft;
}

function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

interface FeedbackState {
  // Persisted per-query drafts.
  drafts: Record<string, FeedbackDraft>;

  // Transient orchestration (not meaningfully persisted — reset on reload).
  activeModalQueryId: string | null;
  annotatingQueryId: string | null;

  // Modal lifecycle
  openModal: (queryId: string) => void;
  closeModal: () => void;

  // Section 1
  setRating: (queryId: string, rating: Thumb) => void;
  setPositiveComment: (queryId: string, comment: string) => void;

  // Response subsections
  setSubsection: (queryId: string, key: string, patch: Partial<YesNoAnswer>) => void;

  // Sources
  setSourcesOk: (queryId: string, answer: YesNo) => void;
  setLinksWork: (queryId: string, answer: YesNo) => void;
  toggleBrokenLink: (queryId: string, linkId: string) => void;
  setBrokenLinksReason: (queryId: string, reason: string) => void;
  addSourceNote: (queryId: string) => void;
  updateSourceNote: (queryId: string, noteId: string, patch: Partial<SourceNote>) => void;
  removeSourceNote: (queryId: string, noteId: string) => void;

  // Speed
  setSpeedTimely: (queryId: string, answer: YesNo) => void;
  setSpeedComment: (queryId: string, comment: string) => void;

  // Annotation mode
  enterAnnotate: (queryId: string) => void;
  exitAnnotate: () => void;
  addAnnotation: (queryId: string, a: Omit<Annotation, 'id'>) => string;
  updateAnnotation: (queryId: string, id: string, patch: Partial<Annotation>) => void;
  removeAnnotation: (queryId: string, id: string) => void;
  clearAnnotations: (queryId: string) => void;

  // Terminal
  submit: (queryId: string) => void;
  resetDraft: (queryId: string) => void;
  getDraft: (queryId: string) => FeedbackDraft;
}

// Immutable helper: return a copy of the store's drafts with `queryId`'s draft
// replaced by the result of `fn`, creating an empty draft first if needed.
function withDraft(
  drafts: Record<string, FeedbackDraft>,
  queryId: string,
  fn: (draft: FeedbackDraft) => FeedbackDraft
): Record<string, FeedbackDraft> {
  const current = drafts[queryId] ?? stableEmptyDraft(queryId);
  return { ...drafts, [queryId]: fn(current) };
}

export const useFeedbackStore = create<FeedbackState>()(
  persist(
    (set, get) => ({
      drafts: {},
      activeModalQueryId: null,
      annotatingQueryId: null,

      openModal: (queryId) =>
        set((s) => ({
          activeModalQueryId: queryId,
          drafts: s.drafts[queryId] ? s.drafts : withDraft(s.drafts, queryId, (d) => d),
        })),
      closeModal: () => set({ activeModalQueryId: null }),

      setRating: (queryId, rating) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => {
            // Switching to "up" discards the full-flow (mid/down) answers so a
            // submitted draft never carries contradictory hidden-branch data.
            if (rating === 'up' && d.rating !== 'up') {
              return {
                ...emptyDraft(queryId),
                annotations: d.annotations,
                positiveComment: d.positiveComment,
                rating,
              };
            }
            return { ...d, rating };
          }),
        })),

      setPositiveComment: (queryId, comment) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({ ...d, positiveComment: comment })),
        })),

      setSubsection: (queryId, key, patch) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => {
            const next = { ...(d.response[key] ?? { answer: null, comment: '' }), ...patch };
            // The comment box only shows on "no"; drop stale text if flipped back.
            if (next.answer === 'yes') next.comment = '';
            return { ...d, response: { ...d.response, [key]: next } };
          }),
        })),

      setSourcesOk: (queryId, answer) =>
        set((s) => ({
          // "yes" hides the per-source notes, so discard them.
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            sourcesOk: answer,
            sourceNotes: answer === 'yes' ? [] : d.sourceNotes,
          })),
        })),

      setLinksWork: (queryId, answer) =>
        set((s) => ({
          // "yes" hides the broken-link picker, so discard its selections/reason.
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            linksWork: answer,
            brokenLinkIds: answer === 'yes' ? [] : d.brokenLinkIds,
            brokenLinksReason: answer === 'yes' ? '' : d.brokenLinksReason,
          })),
        })),

      toggleBrokenLink: (queryId, linkId) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => {
            const current = d.brokenLinkIds ?? [];
            return {
              ...d,
              brokenLinkIds: current.includes(linkId)
                ? current.filter((id) => id !== linkId)
                : [...current, linkId],
            };
          }),
        })),

      setBrokenLinksReason: (queryId, reason) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({ ...d, brokenLinksReason: reason })),
        })),

      addSourceNote: (queryId) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            sourceNotes: [
              ...d.sourceNotes,
              { id: newId(), sourceId: '', citedFully: '', missedDetail: '', comment: '' },
            ],
          })),
        })),

      updateSourceNote: (queryId, noteId, patch) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            sourceNotes: d.sourceNotes.map((n) => {
              if (n.id !== noteId) return n;
              const next = { ...n, ...patch };
              // "What was missed?" only shows on "no"; clear it otherwise.
              if (next.citedFully === 'yes') next.missedDetail = '';
              return next;
            }),
          })),
        })),

      removeSourceNote: (queryId, noteId) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            sourceNotes: d.sourceNotes.filter((n) => n.id !== noteId),
          })),
        })),

      setSpeedTimely: (queryId, answer) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({ ...d, speedTimely: answer })),
        })),

      setSpeedComment: (queryId, comment) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({ ...d, speedComment: comment })),
        })),

      enterAnnotate: (queryId) => set({ annotatingQueryId: queryId }),
      exitAnnotate: () => set({ annotatingQueryId: null }),

      addAnnotation: (queryId, a) => {
        const id = newId();
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            annotations: [...d.annotations, { ...a, id }],
          })),
        }));
        return id;
      },

      updateAnnotation: (queryId, id, patch) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            annotations: d.annotations.map((an) => (an.id === id ? { ...an, ...patch } : an)),
          })),
        })),

      removeAnnotation: (queryId, id) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            annotations: d.annotations.filter((an) => an.id !== id),
          })),
        })),

      clearAnnotations: (queryId) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({ ...d, annotations: [] })),
        })),

      submit: (queryId) =>
        set((s) => ({
          drafts: withDraft(s.drafts, queryId, (d) => ({
            ...d,
            submittedAt: new Date().toISOString(),
          })),
        })),

      resetDraft: (queryId) =>
        set((s) => ({ drafts: { ...s.drafts, [queryId]: emptyDraft(queryId) } })),

      getDraft: (queryId) => get().drafts[queryId] ?? stableEmptyDraft(queryId),
    }),
    {
      name: 'wisco:feedback',
      version: 1,
      // Only persist the drafts. Modal/annotation session state should not
      // survive a reload — a refresh must not leave the app in annotate mode.
      partialize: (s) => ({ drafts: s.drafts }),
      // Backfill any fields added since a draft was saved. The form's shape has
      // evolved (yes/no questions, broken-link picker, speed, ...); normalizing
      // every persisted draft against emptyDraft() guarantees new fields exist
      // with sane defaults instead of being undefined at read time.
      migrate: (persisted) => {
        const state = (persisted ?? {}) as { drafts?: Record<string, Partial<FeedbackDraft>> };
        const drafts: Record<string, FeedbackDraft> = {};
        for (const [queryId, draft] of Object.entries(state.drafts ?? {})) {
          // Deep-normalize each response entry so a persisted `{ answer }` that
          // predates the `comment` field can't leave `comment` undefined (reads
          // like hasDraftContent call `.trim()` on it during render).
          const response: Record<string, YesNoAnswer> = { ...emptyResponse() };
          for (const [key, val] of Object.entries(draft.response ?? {})) {
            response[key] = {
              answer: val?.answer ?? null,
              comment: val?.comment ?? '',
            };
          }
          drafts[queryId] = { ...emptyDraft(queryId), ...draft, response };
        }
        return { drafts };
      },
    }
  )
);
