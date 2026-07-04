'use client';

import React, { memo, useMemo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

export interface AnimatedMarkdownProps {
  content: string;
  animate: boolean;
  animationDuration?: string;
  docUrls?: Record<string, string>;
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


type SourceTone = 'statute' | 'case-law' | 'admin-rule' | 'wpam' | 'faq' | 'gov-pub' | 'iaao' | 'uspap' | 'default';

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

function SourceLink({ href, children }: { href: string; children: React.ReactNode }) {
  const text = extractText(children);
  const tone = classifySource(text, href);
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={`source-link source-link--${tone}`}>
      {children}
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M6 3H3.5A1.5 1.5 0 0 0 2 4.5v8A1.5 1.5 0 0 0 3.5 14h8a1.5 1.5 0 0 0 1.5-1.5V10m-4-8h5m0 0v5m0-5L7.5 8.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    </a>
  );
}

const DOC_HREF_PREFIX = 'doc:';
const STATUTE_DOC_RE = /^statutes-(\d+[A-Za-z]*)$/;

function resolveHref(href: string | undefined, docUrls?: Record<string, string>): string | undefined {
  if (!href) return href;
  if (href.startsWith(DOC_HREF_PREFIX) && docUrls) {
    const rest = href.slice(DOC_HREF_PREFIX.length);
    const hashIdx = rest.indexOf('#page=');
    const docId = hashIdx >= 0 ? rest.slice(0, hashIdx) : rest;
    const pageOverride = hashIdx >= 0 ? parseInt(rest.slice(hashIdx + 6), 10) : NaN;
    let baseUrl = docUrls[docId];
    if (!baseUrl) {
      const statuteMatch = docId.match(STATUTE_DOC_RE);
      if (statuteMatch) {
        baseUrl = `https://docs.legis.wisconsin.gov/statutes/statutes/${statuteMatch[1]}.pdf`;
      } else {
        return undefined;
      }
    }
    if (!Number.isNaN(pageOverride) && pageOverride > 0) {
      const stripped = baseUrl.replace(/#.*$/, '');
      return `${stripped}#page=${pageOverride}`;
    }
    return baseUrl;
  }
  return href;
}

const AnimatedMarkdown = memo(function AnimatedMarkdown({
  content,
  animate,
  animationDuration = '1s',
  docUrls,
}: AnimatedMarkdownProps) {
  const components = useMemo<Components>(() => {
    if (!animate) {
      return {
        a: ({ children, href }) => {
          const resolved = resolveHref(href, docUrls);
          if (!resolved) return <span>{children}</span>;
          return <SourceLink href={resolved}>{children}</SourceLink>;
        },
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
      a: ({ children, href }) => {
        const resolved = resolveHref(href, docUrls);
        if (!resolved) return <span>{children}</span>;
        return <SourceLink href={resolved}>{children}</SourceLink>;
      },
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
  }, [animate, animationDuration, docUrls]);

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
