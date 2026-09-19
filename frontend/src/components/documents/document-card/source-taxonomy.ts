/**
 * Source taxonomy for the "Sources (…)" grid under an answer.
 *
 * One place that answers three questions about a retrieved source:
 *   1. What kind is it?            -> classifySourceKind()
 *   2. What does its card look like? -> SOURCE_KIND_META (tint + accent hue)
 *   3. Which authority group does it sit in, and in what order? -> SOURCE_GROUPS
 *
 * The hues mirror the ones the inline source links already use in
 * `chat-message.css` (.source-link--statute, --case-law, ...) so a citation in
 * the prose and its card in the grid read as the same colour family.
 *
 * Tailwind class strings are written out in full on purpose — the v4 scanner
 * only sees literal class names, so these must never be built by concatenation.
 */

export type SourceKind =
  | 'constitution'
  | 'statute'
  | 'case-law'
  | 'admin-rule'
  | 'wpam'
  | 'gov-pub'
  | 'form'
  | 'faq'
  | 'news'
  | 'iaao'
  | 'uspap'
  | 'other';

export interface SourceKindMeta {
  /** Small-caps text that replaces the old type pill in the card header. */
  label: string;
  /** Card surface: very low-alpha tint + thin left accent border. */
  card: string;
  /** Header label colour (same hue as the card tint). */
  accent: string;
}

export const SOURCE_KIND_META: Record<SourceKind, SourceKindMeta> = {
  constitution: {
    label: 'Constitution',
    card: 'border-l-2 border-l-indigo-500/70 bg-indigo-500/[0.04] hover:bg-indigo-500/[0.08] dark:border-l-indigo-400/70 dark:bg-indigo-400/[0.07] dark:hover:bg-indigo-400/[0.12]',
    accent: 'text-indigo-700 dark:text-indigo-300',
  },
  statute: {
    label: 'Statute',
    card: 'border-l-2 border-l-blue-500/70 bg-blue-500/[0.04] hover:bg-blue-500/[0.08] dark:border-l-blue-400/70 dark:bg-blue-400/[0.07] dark:hover:bg-blue-400/[0.12]',
    accent: 'text-blue-700 dark:text-blue-300',
  },
  'case-law': {
    label: 'Case Law',
    card: 'border-l-2 border-l-violet-500/70 bg-violet-500/[0.04] hover:bg-violet-500/[0.08] dark:border-l-violet-400/70 dark:bg-violet-400/[0.07] dark:hover:bg-violet-400/[0.12]',
    accent: 'text-violet-700 dark:text-violet-300',
  },
  'admin-rule': {
    label: 'Admin Rule',
    card: 'border-l-2 border-l-teal-500/70 bg-teal-500/[0.04] hover:bg-teal-500/[0.08] dark:border-l-teal-400/70 dark:bg-teal-400/[0.07] dark:hover:bg-teal-400/[0.12]',
    accent: 'text-teal-700 dark:text-teal-300',
  },
  wpam: {
    label: 'WPAM',
    card: 'border-l-2 border-l-green-500/70 bg-green-500/[0.04] hover:bg-green-500/[0.08] dark:border-l-green-400/70 dark:bg-green-400/[0.07] dark:hover:bg-green-400/[0.12]',
    accent: 'text-green-700 dark:text-green-300',
  },
  'gov-pub': {
    label: 'Gov. Publication',
    card: 'border-l-2 border-l-amber-500/70 bg-amber-500/[0.04] hover:bg-amber-500/[0.08] dark:border-l-amber-400/70 dark:bg-amber-400/[0.07] dark:hover:bg-amber-400/[0.12]',
    accent: 'text-amber-700 dark:text-amber-300',
  },
  form: {
    label: 'Form Instructions',
    card: 'border-l-2 border-l-amber-500/70 bg-amber-500/[0.04] hover:bg-amber-500/[0.08] dark:border-l-amber-400/70 dark:bg-amber-400/[0.07] dark:hover:bg-amber-400/[0.12]',
    accent: 'text-amber-700 dark:text-amber-300',
  },
  faq: {
    label: 'FAQ',
    card: 'border-l-2 border-l-slate-400/70 bg-slate-500/[0.04] hover:bg-slate-500/[0.08] dark:border-l-slate-400/60 dark:bg-slate-300/[0.06] dark:hover:bg-slate-300/[0.10]',
    accent: 'text-slate-600 dark:text-slate-300',
  },
  news: {
    label: 'DOR News',
    card: 'border-l-2 border-l-slate-400/70 bg-slate-500/[0.04] hover:bg-slate-500/[0.08] dark:border-l-slate-400/60 dark:bg-slate-300/[0.06] dark:hover:bg-slate-300/[0.10]',
    accent: 'text-slate-600 dark:text-slate-300',
  },
  iaao: {
    label: 'IAAO Standard',
    card: 'border-l-2 border-l-rose-500/70 bg-rose-500/[0.04] hover:bg-rose-500/[0.08] dark:border-l-rose-400/70 dark:bg-rose-400/[0.07] dark:hover:bg-rose-400/[0.12]',
    accent: 'text-rose-700 dark:text-rose-300',
  },
  uspap: {
    label: 'USPAP Standard',
    card: 'border-l-2 border-l-rose-500/70 bg-rose-500/[0.04] hover:bg-rose-500/[0.08] dark:border-l-rose-400/70 dark:bg-rose-400/[0.07] dark:hover:bg-rose-400/[0.12]',
    accent: 'text-rose-700 dark:text-rose-300',
  },
  other: {
    label: 'Source',
    card: 'border-l-2 border-l-slate-400/70 bg-slate-500/[0.04] hover:bg-slate-500/[0.08] dark:border-l-slate-400/60 dark:bg-slate-300/[0.06] dark:hover:bg-slate-300/[0.10]',
    accent: 'text-slate-600 dark:text-slate-300',
  },
};

/**
 * doc_id prefix -> kind. Checked before the authority level because the id is
 * always present and is the only way to tell DOR news pages apart from the
 * other FW-GOV-PUBS documents (both land on authority level 7).
 *
 * Ids are normalised first: lowercased, `raw/` stripped, `_` folded to `-`.
 * Longest-first ordering matters ("form-instruction" before "faq" is
 * irrelevant, but "gov-publication" must not be shadowed by a shorter entry).
 */
const ID_PREFIX_KIND: ReadonlyArray<readonly [string, SourceKind]> = [
  ['constitution', 'constitution'],
  ['statute', 'statute'],
  ['case-law', 'case-law'],
  ['caselaw', 'case-law'],
  ['admin-rule', 'admin-rule'],
  ['wpam', 'wpam'],
  ['form-instruction', 'form'],
  ['gov-publication', 'gov-pub'],
  ['gov-pub', 'gov-pub'],
  ['complex-inquiry', 'gov-pub'],
  ['news', 'news'],
  ['faq', 'faq'],
  ['iaao', 'iaao'],
  ['uspap', 'uspap'],
];

/** Authority level (1–9) -> kind. Fallback when the id prefix is unfamiliar. */
const AUTHORITY_KIND: Record<number, SourceKind> = {
  1: 'constitution',
  2: 'statute',
  3: 'case-law',
  4: 'admin-rule',
  5: 'wpam',
  6: 'faq',
  7: 'gov-pub',
  8: 'iaao',
  9: 'uspap',
};

export interface ClassifiableSource {
  documentId?: string;
  authorityLevel?: number;
}

export function classifySourceKind(doc: ClassifiableSource): SourceKind {
  const id = (doc.documentId ?? '')
    .toLowerCase()
    .replace(/^raw\//, '')
    .replace(/_/g, '-');

  if (id) {
    for (const [prefix, kind] of ID_PREFIX_KIND) {
      if (id.startsWith(prefix)) return kind;
    }
  }

  if (doc.authorityLevel != null) {
    const byLevel = AUTHORITY_KIND[doc.authorityLevel];
    if (byLevel) return byLevel;
  }

  return 'other';
}

export interface SourceGroupDef {
  id: string;
  label: string;
  kinds: readonly SourceKind[];
}

/** Authority order, highest legal precedence first. `other` always last. */
export const SOURCE_GROUPS: readonly SourceGroupDef[] = [
  { id: 'statutes', label: 'Statutes & Constitution', kinds: ['constitution', 'statute'] },
  { id: 'case-law', label: 'Case Law', kinds: ['case-law'] },
  { id: 'admin-rules', label: 'Admin Rules', kinds: ['admin-rule'] },
  { id: 'wpam', label: 'Assessment Manual (WPAM)', kinds: ['wpam'] },
  { id: 'gov-pubs', label: 'Government Publications & Forms', kinds: ['gov-pub', 'form'] },
  { id: 'faqs-news', label: 'FAQs & News', kinds: ['faq', 'news'] },
  { id: 'other', label: 'Other', kinds: ['iaao', 'uspap', 'other'] },
];

const GROUP_ID_BY_KIND: Record<SourceKind, string> = (() => {
  const map = {} as Record<SourceKind, string>;
  for (const group of SOURCE_GROUPS) {
    for (const kind of group.kinds) map[kind] = group.id;
  }
  return map;
})();

export function groupIdForKind(kind: SourceKind): string {
  return GROUP_ID_BY_KIND[kind] ?? 'other';
}

export interface SourceGroup<T> {
  id: string;
  label: string;
  items: T[];
}

/**
 * Bucket entries into the authority groups, dropping empty ones. Group order is
 * fixed by SOURCE_GROUPS; within a group the caller's original order is kept.
 */
export function groupSourceEntries<T extends { kind: SourceKind }>(
  entries: readonly T[]
): SourceGroup<T>[] {
  const buckets = new Map<string, T[]>();
  for (const entry of entries) {
    const id = groupIdForKind(entry.kind);
    const bucket = buckets.get(id);
    if (bucket) bucket.push(entry);
    else buckets.set(id, [entry]);
  }

  return SOURCE_GROUPS.flatMap(group => {
    const items = buckets.get(group.id);
    return items && items.length
      ? [{ id: group.id, label: group.label, items }]
      : [];
  });
}

/**
 * Small result sets stay fully open; larger ones open only the two
 * highest-authority non-empty groups so the grid never dominates the answer.
 */
export const EXPAND_ALL_MAX_ITEMS = 6;

export function defaultOpenGroupIds<T>(
  groups: readonly SourceGroup<T>[],
  totalItems?: number
): string[] {
  const total =
    totalItems ?? groups.reduce((sum, group) => sum + group.items.length, 0);
  const ids = groups.map(group => group.id);
  return total <= EXPAND_ALL_MAX_ITEMS ? ids : ids.slice(0, 2);
}
