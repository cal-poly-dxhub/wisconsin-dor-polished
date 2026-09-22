'use client';

import React, { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  DOC_HREF_PREFIX,
  parseDocHref,
  type DocHrefTarget,
} from '@/lib/parse-doc-href';

export interface AnimatedMarkdownProps {
  content: string;
  animate: boolean;
  animationDuration?: string;
  docUrls?: Record<string, string>;
  docTones?: Record<string, SourceTone>;
  /** doc_id → human title, keyed exactly like docUrls. Used by the dual-source popover. */
  docTitles?: Record<string, string>;
}

const WORD_SPLIT = /(\s+)/;

const animatedSpanStyle = (animationDuration: string): React.CSSProperties => ({
  animationName: 'ft-blurAndSharpen',
  animationDuration,
  animationTimingFunction: 'ease',
  animationIterationCount: 1,
  display: 'inline-block',
  whiteSpace: 'pre-wrap',
});

// Recursively walk ReactMarkdown children, splitting string leaves into
// per-word animated spans. Non-string elements pass through so their own
// `components` mapping animates their children. This mirrors flowtoken's
// sep="word" behavior but without the per-token diff machinery — streaming
// re-renders the tree and CSS animation-iteration-count: 1 takes care of
// only-animate-once per DOM mount.
function splitAnimated(
  children: React.ReactNode,
  style: React.CSSProperties,
  keyPrefix: string
): React.ReactNode {
  if (typeof children === 'string') {
    return children
      .split(WORD_SPLIT)
      .filter(token => token.length > 0)
      .map((token, i) => (
        <span key={`${keyPrefix}-${i}`} style={style}>
          {token}
        </span>
      ));
  }
  if (Array.isArray(children)) {
    return children.map((child, i) =>
      splitAnimated(child, style, `${keyPrefix}-${i}`)
    );
  }
  return children;
}


// `dual` is not an authority tier — it marks a link that carries TWO targets
// (a primary document plus the statute/admin-rule section its label names).
export type SourceTone = 'statute' | 'case-law' | 'admin-rule' | 'wpam' | 'faq' | 'gov-pub' | 'iaao' | 'uspap' | 'dual' | 'default';

// Authority level → tone. This is the deterministic classification: the
// backend already assigns every document an authority tier (1–9), which maps
// 1:1 to a source tone. Prefer this over classifySource() text-guessing.
// (Level 1 = Constitution has no dedicated tone yet → falls back.)
const AUTHORITY_TONE: Record<number, SourceTone> = {
  2: 'statute',
  3: 'case-law',
  4: 'admin-rule',
  5: 'wpam',
  6: 'faq',
  7: 'gov-pub',
  8: 'iaao',
  9: 'uspap',
};

export function toneForAuthorityLevel(level?: number): SourceTone | undefined {
  if (level == null) return undefined;
  return AUTHORITY_TONE[level];
}

// Heuristic fallback for links whose doc_id we can't map to a source card
// (e.g. synthetic statute-fallback URLs or raw external hrefs). Ambiguous by
// nature — only used when the deterministic authority-level tone is absent.
function classifySource(text: string, href?: string): SourceTone {
  const t = text.toLowerCase();
  const h = (href ?? '').toLowerCase();
  if (t.includes('stat.') || t.includes('statute') || t.includes('§') || h.includes('statutes-') || h.includes('/statutes/statutes/')) return 'statute';
  if (t.includes('v.') || t.includes('case') || t.includes('f.4th') || t.includes('wis.2d') || h.includes('case-law') || h.includes('case_law') || h.includes('courtlistener.com')) return 'case-law';
  if (t.includes('admin') || t.includes('rule') || t.includes('tax ') || h.includes('admin_rules') || h.includes('/admin_code/')) return 'admin-rule';
  if (t.includes('wpam') || h.includes('wpam')) return 'wpam';
  if (t.includes('faq') || h.includes('faq')) return 'faq';
  if (t.includes('guide') || t.includes('bulletin') || t.includes('publication') || h.includes('gov_publications') || h.includes('dor%20publications') || h.includes('dor+publications')) return 'gov-pub';
  if (t.includes('iaao') || h.includes('iaao')) return 'iaao';
  if (t.includes('uspap') || h.includes('uspap')) return 'uspap';
  return 'default';
}

function extractText(node: React.ReactNode): string {
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (React.isValidElement(node) && node.props) {
    return extractText((node.props as { children?: React.ReactNode }).children);
  }
  return '';
}

function ExternalGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M6 3H3.5A1.5 1.5 0 0 0 2 4.5v8A1.5 1.5 0 0 0 3.5 14h8a1.5 1.5 0 0 0 1.5-1.5V10m-4-8h5m0 0v5m0-5L7.5 8.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

function SourceLink({ href, tone, children }: { href: string; tone: SourceTone; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={`source-link source-link--${tone}`}>
      {children}
      <ExternalGlyph />
    </a>
  );
}

const STATUTE_DOC_RE = /^statutes-(\d+[A-Za-z]*)$/;
const ADMIN_DOC_RE = /^admin_rules-tax-(\d+[A-Za-z]*)$/;

// Extract the bare PRIMARY doc_id from a `doc:` href (single or dual form).
function docIdFromHref(href: string | undefined): string | undefined {
  return parseDocHref(href)?.docId;
}

// Resolve a doc_id's source tone. Prefer the deterministic authority-level tone
// keyed by doc_id (docTones, built from the source cards the backend sent);
// fall back to text/href heuristics only when the doc isn't a known card.
function toneForDoc(
  docId: string | undefined,
  docTones: Record<string, SourceTone> | undefined,
  labelText: string,
  resolvedHref: string
): SourceTone {
  if (docId && docTones) {
    const tone = docTones[docId];
    if (tone) return tone;
  }
  return classifySource(labelText, resolvedHref || (docId ?? ''));
}

function resolveTone(
  href: string | undefined,
  children: React.ReactNode,
  resolvedHref: string,
  docTones?: Record<string, SourceTone>
): SourceTone {
  return toneForDoc(docIdFromHref(href), docTones, extractText(children), resolvedHref);
}

// "§ 74.37", "s. 70.32(2)(c)", "74.37" -> "74.37" when it belongs to `chapter`.
function sectionFromLabel(label: string, chapter: string): string | undefined {
  const m = label.match(new RegExp(`(?<![\\d.])${chapter}\\.(\\d+)`));
  return m ? `${chapter}.${m[1]}` : undefined;
}

/**
 * Turn one parsed `doc:` target into a real URL. Shared by single links and by
 * each row of a dual link, so a statute `ref` falls back to the legislature's
 * per-section page exactly the way a single statute link does.
 */
function resolveTarget(
  target: DocHrefTarget,
  docUrls: Record<string, string>,
  label: string
): string | undefined {
  const { docId, page } = target;
  let baseUrl = docUrls[docId];
  const statuteMatch = docId.match(STATUTE_DOC_RE);
  if (statuteMatch && page === undefined) {
    // A statute link with no page (the chapter was not among the retrieved
    // documents and the writer had no page to give). Send the reader to the
    // legislature's per-section page when the label names a section, e.g.
    // "§ 74.37" -> docs.legis.wisconsin.gov/document/statutes/74.37, instead
    // of page 1 of a 60-page chapter PDF.
    const section = sectionFromLabel(label, statuteMatch[1]);
    if (section) return `https://docs.legis.wisconsin.gov/document/statutes/${section}`;
  }
  if (!baseUrl) {
    if (statuteMatch) {
      baseUrl = `https://docs.legis.wisconsin.gov/statutes/statutes/${statuteMatch[1]}.pdf`;
    } else {
      return undefined;
    }
  }
  if (page !== undefined) {
    const stripped = baseUrl.replace(/#.*$/, '');
    return `${stripped}#page=${page}`;
  }
  return baseUrl;
}

function resolveHref(
  href: string | undefined,
  docUrls?: Record<string, string>,
  label = ''
): string | undefined {
  if (!href) return href;
  if (href.startsWith(DOC_HREF_PREFIX) && docUrls) {
    const parsed = parseDocHref(href);
    if (!parsed) return undefined;
    return resolveTarget(parsed, docUrls, label);
  }
  return href;
}

/**
 * A readable name for one target of a dual link. Prefers the title the source
 * card uses (docTitles is built from the same resource items); otherwise
 * renders statutes and admin rules in their citation form and falls back to
 * the bare doc id for everything else.
 */
function titleForTarget(
  target: DocHrefTarget,
  docTitles: Record<string, string> | undefined,
  label: string
): string {
  const known = docTitles?.[target.docId];
  if (known) return known;
  const statuteMatch = target.docId.match(STATUTE_DOC_RE);
  if (statuteMatch) {
    const section = sectionFromLabel(label, statuteMatch[1]);
    return section ? `§ ${section} · Wis. Stats.` : `Wis. Stat. ch. ${statuteMatch[1]}`;
  }
  const adminMatch = target.docId.match(ADMIN_DOC_RE);
  if (adminMatch) {
    const section = sectionFromLabel(label, adminMatch[1]);
    return section
      ? `Tax ${section} · Wis. Adm. Code`
      : `Wis. Adm. Code ch. Tax ${adminMatch[1]}`;
  }
  return target.docId;
}

interface DualRow {
  key: 'primary' | 'ref';
  url?: string;
  title: string;
  page?: number;
  tone: SourceTone;
}

/**
 * An inline citation that carries two targets: the document whose words the
 * label quotes (primary) and the statute / admin-rule section that label names
 * (ref).
 *
 * It renders as a real `<a href={primary}>` so middle-click, cmd/ctrl-click and
 * "open in new tab" behave like any other link. Only a PLAIN left click is
 * intercepted, and it opens a small popover offering both targets.
 */
function DualSourceLink({
  primaryUrl,
  rows,
  children,
}: {
  primaryUrl?: string;
  rows: DualRow[];
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popoverId = useId();

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      const node = wrapRef.current;
      if (node && e.target instanceof Node && !node.contains(e.target)) close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        close();
        // Put focus back on the link the popover belongs to.
        wrapRef.current?.querySelector('a')?.focus();
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, close]);

  // Plain left click opens the chooser; every modified click (cmd/ctrl/shift/
  // alt) and middle click falls through to normal anchor behavior on the
  // PRIMARY target.
  const onClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    setOpen(prev => !prev);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLAnchorElement>) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      setOpen(prev => !prev);
    }
  };

  return (
    <span className="source-link-dual" ref={wrapRef}>
      <a
        href={primaryUrl ?? '#'}
        target="_blank"
        rel="noopener noreferrer"
        className="source-link source-link--dual"
        role="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={onClick}
        onKeyDown={onKeyDown}
      >
        {children}
        {/* Two dots = two sources. Decorative; the popover carries the meaning. */}
        <svg
          className="source-link-dual-dots"
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="currentColor"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <circle cx="5" cy="8" r="2" />
          <circle cx="11" cy="8" r="2" />
        </svg>
      </a>
      <span
        id={popoverId}
        className="source-link-dual-popover"
        role="dialog"
        aria-label="Two sources"
        hidden={!open}
      >
        {rows.map(row =>
          row.url ? (
            <a
              key={row.key}
              href={row.url}
              target="_blank"
              rel="noopener noreferrer"
              className="source-link-dual-row"
              onClick={close}
            >
              <span className={`source-link-dual-dot source-link--${row.tone}`} aria-hidden="true" />
              <span className="source-link-dual-text">
                <span className="source-link-dual-title">{row.title}</span>
                {row.page !== undefined && (
                  <span className="source-link-dual-page">{`p. ${row.page}`}</span>
                )}
              </span>
              <ExternalGlyph />
            </a>
          ) : (
            <span key={row.key} className="source-link-dual-row source-link-dual-row--inert">
              <span className={`source-link-dual-dot source-link--${row.tone}`} aria-hidden="true" />
              <span className="source-link-dual-text">
                <span className="source-link-dual-title">{row.title}</span>
                {row.page !== undefined && (
                  <span className="source-link-dual-page">{`p. ${row.page}`}</span>
                )}
              </span>
            </span>
          )
        )}
      </span>
    </span>
  );
}

/**
 * The `a:` renderer shared by the animated and non-animated component maps.
 * Single-target hrefs take exactly the path they always took; only a href that
 * parses with a `ref` gets the dual treatment.
 */
function renderAnchor(
  children: React.ReactNode,
  href: string | undefined,
  docUrls?: Record<string, string>,
  docTones?: Record<string, SourceTone>,
  docTitles?: Record<string, string>
): React.ReactElement {
  const label = extractText(children);
  const parsed = href && docUrls ? parseDocHref(href) : null;

  if (parsed?.ref && docUrls) {
    const primaryUrl = resolveTarget(parsed, docUrls, label);
    const refUrl = resolveTarget(parsed.ref, docUrls, label);
    if (!primaryUrl && !refUrl) return <span>{children}</span>;
    const rows: DualRow[] = [
      {
        key: 'primary',
        url: primaryUrl,
        title: titleForTarget(parsed, docTitles, label),
        page: parsed.page,
        tone: toneForDoc(parsed.docId, docTones, label, primaryUrl ?? ''),
      },
      {
        key: 'ref',
        url: refUrl,
        title: titleForTarget(parsed.ref, docTitles, label),
        page: parsed.ref.page,
        tone: toneForDoc(parsed.ref.docId, docTones, label, refUrl ?? ''),
      },
    ];
    return (
      <DualSourceLink primaryUrl={primaryUrl ?? refUrl} rows={rows}>
        {children}
      </DualSourceLink>
    );
  }

  const resolved = resolveHref(href, docUrls, label);
  if (!resolved) return <span>{children}</span>;
  const tone = resolveTone(href, children, resolved, docTones);
  return <SourceLink href={resolved} tone={tone}>{children}</SourceLink>;
}

const AnimatedMarkdown = memo(function AnimatedMarkdown({
  content,
  animate,
  animationDuration = '1s',
  docUrls,
  docTones,
  docTitles,
}: AnimatedMarkdownProps) {
  const components = useMemo<Components>(() => {
    if (!animate) {
      return {
        a: ({ children, href }) =>
          renderAnchor(children, href, docUrls, docTones, docTitles),
      };
    }

    const style = animatedSpanStyle(animationDuration);
    const wrap = (children: React.ReactNode, keyPrefix: string) =>
      splitAnimated(children, style, keyPrefix);

    return {
      p: ({ children, ...props }) => <p {...props}>{wrap(children, 'p')}</p>,
      h1: ({ children, ...props }) => <h1 {...props}>{wrap(children, 'h1')}</h1>,
      h2: ({ children, ...props }) => <h2 {...props}>{wrap(children, 'h2')}</h2>,
      h3: ({ children, ...props }) => <h3 {...props}>{wrap(children, 'h3')}</h3>,
      h4: ({ children, ...props }) => <h4 {...props}>{wrap(children, 'h4')}</h4>,
      h5: ({ children, ...props }) => <h5 {...props}>{wrap(children, 'h5')}</h5>,
      h6: ({ children, ...props }) => <h6 {...props}>{wrap(children, 'h6')}</h6>,
      li: ({ children, ...props }) => <li {...props}>{wrap(children, 'li')}</li>,
      strong: ({ children, ...props }) => (
        <strong {...props}>{wrap(children, 'strong')}</strong>
      ),
      em: ({ children, ...props }) => <em {...props}>{wrap(children, 'em')}</em>,
      a: ({ children, href }) =>
        renderAnchor(children, href, docUrls, docTones, docTitles),
      blockquote: ({ children, ...props }) => (
        <blockquote {...props}>{wrap(children, 'bq')}</blockquote>
      ),
      // Render tables and their cells intact so GFM table layout survives.
      // Word-splitting `<td>` content shifts column widths unpredictably.
      td: ({ children, ...props }) => <td {...props}>{children}</td>,
      th: ({ children, ...props }) => <th {...props}>{children}</th>,
      // Code blocks render verbatim — no word splitting inside.
      code: ({ children, ...props }) => <code {...props}>{children}</code>,
      span: ({ children, className, ...props }) => (
        <span className={className} {...props}>
          {wrap(children, 'span')}
        </span>
      ),
      div: ({ children, className, ...props }) => (
        <div className={className} {...props}>
          {children}
        </div>
      ),
    };
  }, [animate, animationDuration, docUrls, docTones, docTitles]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={components}
      urlTransform={(url) => url}
    >
      {content}
    </ReactMarkdown>
  );
});

export default AnimatedMarkdown;
