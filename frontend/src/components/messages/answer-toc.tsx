'use client';

import { useCallback, useEffect, useState, type RefObject } from 'react';

/**
 * Notion-style floating table of contents for one assistant answer.
 *
 * At rest it is a slim stack of horizontal bars pinned to the right edge of the
 * answer prose (one bar per heading, shorter/dimmer the deeper the heading).
 * On hover the stack cross-fades into a small panel listing the heading texts;
 * clicking one smooth-scrolls the chat scroll container to that heading.
 *
 * Headings are collected from the rendered DOM (not by parsing the markdown a
 * second time) once streaming completes, and each heading element is given a
 * stable id scoped to the message so two answers never collide.
 */

export interface TocHeading {
  /** DOM id assigned to the heading element. */
  id: string;
  text: string;
  /** 1–6, from the tag name. */
  level: number;
  /** Level relative to the shallowest heading in this answer, clamped. */
  depth: number;
}

const HEADING_SELECTOR = 'h1, h2, h3, h4, h5, h6';

export const MAX_TOC_DEPTH = 3;

/** Bar width in px, by depth (0 = shallowest heading in the answer). */
const BAR_WIDTHS = [28, 20, 15, 12];
/** Resting bar opacity, by depth. */
const BAR_OPACITIES = [0.55, 0.45, 0.36, 0.3];
/** Panel indent in px per depth step. */
const INDENT_PER_DEPTH = 12;

/** Fraction of the scroll viewport the "you are here" line sits at. */
const ACTIVE_LINE_FRACTION = 1 / 3;
/** Breathing room above a heading we scroll to. */
const SCROLL_TOP_PADDING = 24;

const MIN_HEADINGS = 2;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** "Assessment & Appeals (2024)" -> "assessment-appeals-2024". */
export function slugifyHeading(text: string): string {
  const slug = text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
    .replace(/-+$/g, '');
  return slug || 'section';
}

/**
 * Stable per-message heading id. The message id scopes it (two answers in the
 * same page can repeat a heading) and the ordinal disambiguates repeats inside
 * one answer, so the id survives re-renders as long as the heading order does.
 */
export function headingDomId(messageId: string, text: string, index: number): string {
  return `toc-${messageId}-${index}-${slugifyHeading(text)}`;
}

/** Depth of a heading relative to the shallowest heading in the same answer. */
export function depthForLevel(level: number, baseLevel: number): number {
  return clamp(level - baseLevel, 0, MAX_TOC_DEPTH);
}

/** Resting bar width (px) for a depth. */
export function barWidthForDepth(depth: number): number {
  return BAR_WIDTHS[clamp(depth, 0, BAR_WIDTHS.length - 1)];
}

/** Resting bar opacity for a depth. */
export function barOpacityForDepth(depth: number): number {
  return BAR_OPACITIES[clamp(depth, 0, BAR_OPACITIES.length - 1)];
}

/** Panel text indent (px) for a depth. */
export function indentForDepth(depth: number): number {
  return clamp(depth, 0, MAX_TOC_DEPTH) * INDENT_PER_DEPTH;
}

/**
 * Read the rendered heading elements under `root`, assign each a stable id, and
 * return the TOC model. Mutates only the `id` attribute, and only when it
 * differs — so a MutationObserver watching children never loops on our writes.
 */
export function collectHeadings(root: Element, messageId: string): TocHeading[] {
  const elements = Array.from(root.querySelectorAll(HEADING_SELECTOR));
  const found: Omit<TocHeading, 'depth'>[] = [];

  elements.forEach((element, index) => {
    const text = (element.textContent ?? '').replace(/\s+/g, ' ').trim();
    if (!text) return;
    const level = Number(element.tagName.slice(1)) || 1;
    const id = headingDomId(messageId, text, index);
    if (element.id !== id) element.id = id;
    found.push({ id, text, level });
  });

  if (found.length === 0) return [];
  const baseLevel = Math.min(...found.map(h => h.level));
  return found.map(h => ({ ...h, depth: depthForLevel(h.level, baseLevel) }));
}

function sameHeadings(a: TocHeading[], b: TocHeading[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((h, i) => h.id === b[i].id && h.text === b[i].text && h.depth === b[i].depth);
}

/** The element that actually scrolls the chat (see chat-container.tsx). */
function findScrollContainer(from: Element | null): HTMLElement | null {
  return (from?.closest('[data-chat-scroll]') as HTMLElement | null) ?? null;
}

export interface AnswerTocProps {
  /** queryId — scopes the generated heading ids. */
  messageId: string;
  /** Ref to the element wrapping just the answer prose. */
  bodyRef: RefObject<HTMLDivElement | null>;
  /** Answer markdown; only used to re-collect when the text changes. */
  content?: string;
  streamingComplete?: boolean;
}

export function AnswerToc({ messageId, bodyRef, content, streamingComplete }: AnswerTocProps) {
  const [headings, setHeadings] = useState<TocHeading[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  // --- collect headings from the rendered DOM -----------------------------
  useEffect(() => {
    const body = bodyRef.current;
    // Nothing to collect yet; the render below gates on streamingComplete, so
    // stale headings can never reach the rail.
    if (!body || !streamingComplete) return undefined;

    let frame = 0;
    const sync = () => {
      const next = collectHeadings(body, messageId);
      setHeadings(prev => (sameHeadings(prev, next) ? prev : next));
    };

    sync();

    // Late re-renders (source cards arriving, link tones resolving) can rebuild
    // heading nodes; re-sync when the subtree changes. `attributes: false`
    // keeps our own id writes from re-triggering this.
    const observer = new MutationObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        sync();
      });
    });
    observer.observe(body, { childList: true, subtree: true, characterData: true });

    return () => {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [bodyRef, messageId, content, streamingComplete]);

  // --- track which heading is "in view" ------------------------------------
  useEffect(() => {
    if (headings.length < MIN_HEADINGS) return undefined;
    const body = bodyRef.current;
    const container = findScrollContainer(body);
    if (!body) return undefined;

    const elements = headings
      .map(h => document.getElementById(h.id))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return undefined;

    const recompute = () => {
      const viewportTop = container ? container.getBoundingClientRect().top : 0;
      const viewportHeight = container ? container.clientHeight : window.innerHeight;
      const line = viewportTop + viewportHeight * ACTIVE_LINE_FRACTION;
      let current = elements[0];
      for (const element of elements) {
        if (element.getBoundingClientRect().top <= line) current = element;
        else break;
      }
      setActiveId(current.id);
    };

    recompute();

    // The observer fires whenever a heading crosses the band that ends at the
    // "you are here" line, which is exactly when the answer can change.
    const observer = new IntersectionObserver(recompute, {
      root: container ?? null,
      rootMargin: `0px 0px -${Math.round((1 - ACTIVE_LINE_FRACTION) * 100)}% 0px`,
      threshold: 0,
    });
    elements.forEach(element => observer.observe(element));
    return () => observer.disconnect();
  }, [headings, bodyRef]);

  const scrollToHeading = useCallback(
    (id: string) => {
      const target = document.getElementById(id);
      if (!target) return;
      const container = findScrollContainer(bodyRef.current);
      if (!container) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      const delta =
        target.getBoundingClientRect().top - container.getBoundingClientRect().top;
      container.scrollTo({
        top: container.scrollTop + delta - SCROLL_TOP_PADDING,
        behavior: 'smooth',
      });
      setActiveId(id);
    },
    [bodyRef]
  );

  if (headings.length < MIN_HEADINGS || !streamingComplete) return null;

  return (
    <div className="answer-toc">
      <div className="answer-toc__sticky">
        <div className="answer-toc__rail" aria-hidden="true">
          {headings.map(h => (
            <span
              key={h.id}
              className="answer-toc__bar"
              data-active={h.id === activeId}
              style={{
                width: `${barWidthForDepth(h.depth)}px`,
                opacity: h.id === activeId ? undefined : barOpacityForDepth(h.depth),
              }}
            />
          ))}
        </div>
        <nav className="answer-toc__panel" aria-label="Answer outline">
          <ul className="answer-toc__list">
            {headings.map(h => (
              <li key={h.id}>
                <button
                  type="button"
                  className="answer-toc__item"
                  data-active={h.id === activeId}
                  style={{ paddingLeft: `${10 + indentForDepth(h.depth)}px` }}
                  onClick={() => scrollToHeading(h.id)}
                  title={h.text}
                >
                  {h.text}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </div>
  );
}

export default AnswerToc;
