/** @bun */
import { describe, test, expect, beforeAll } from 'bun:test';
import { JSDOM } from 'jsdom';

import {
  getPlainText,
  selectionToOffsets,
  offsetsToRange,
} from './text-annotation';

// text-annotation.ts uses DOM globals (document, TreeWalker, Range, Selection).
// Install a jsdom window before importing anything that touches them at runtime.
beforeAll(() => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  const g = globalThis as unknown as Record<string, unknown>;
  g.window = dom.window;
  g.document = dom.window.document;
  g.NodeFilter = dom.window.NodeFilter;
  g.Node = dom.window.Node;
});

function makeContainer(html: string): HTMLElement {
  const el = document.createElement('div');
  el.innerHTML = html;
  document.body.appendChild(el);
  return el;
}

describe('getPlainText', () => {
  test('concatenates text across nested elements in document order', () => {
    const el = makeContainer('<p>Hello <strong>brave</strong> world</p>');
    expect(getPlainText(el)).toBe('Hello brave world');
  });

  test('spans multiple block elements', () => {
    const el = makeContainer('<p>abc</p><p>def</p>');
    expect(getPlainText(el)).toBe('abcdef');
  });
});

describe('offsetsToRange', () => {
  test('round-trips: range built from offsets yields the same quote', () => {
    const el = makeContainer('<p>Hello <strong>brave</strong> world</p>');
    // "brave" is at offsets 6..11 in "Hello brave world"
    const range = offsetsToRange(el, 6, 11);
    expect(range).not.toBeNull();
    expect(range!.toString()).toBe('brave');
  });

  test('handles offsets that cross element boundaries', () => {
    const el = makeContainer('<p>Hello <strong>brave</strong> world</p>');
    // "lo brave wo" spans the plain "Hello ", the <strong>, and " world"
    const range = offsetsToRange(el, 3, 14);
    expect(range!.toString()).toBe('lo brave wo');
  });

  test('returns null for empty or inverted ranges', () => {
    const el = makeContainer('<p>abc</p>');
    expect(offsetsToRange(el, 2, 2)).toBeNull();
    expect(offsetsToRange(el, 3, 1)).toBeNull();
  });
});

describe('selectionToOffsets', () => {
  test('maps a selection over nested nodes to plain-text offsets', () => {
    const el = makeContainer('<p>Hello <strong>brave</strong> world</p>');
    const strong = el.querySelector('strong')!;
    const range = document.createRange();
    range.selectNodeContents(strong); // selects "brave"
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);

    const result = selectionToOffsets(el, selection);
    expect(result).not.toBeNull();
    expect(result!.quote).toBe('brave');
    expect(result!.start).toBe(6);
    expect(result!.end).toBe(11);
  });

  test('returns null for a collapsed selection', () => {
    const el = makeContainer('<p>abc</p>');
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    const range = document.createRange();
    range.setStart(el.querySelector('p')!.firstChild!, 1);
    range.collapse(true);
    selection.addRange(range);
    expect(selectionToOffsets(el, selection)).toBeNull();
  });

  test('returns null when selection is outside the container', () => {
    const el = makeContainer('<p>inside</p>');
    const outside = makeContainer('<p>outside</p>');
    const range = document.createRange();
    range.selectNodeContents(outside.querySelector('p')!);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    expect(selectionToOffsets(el, selection)).toBeNull();
  });
});
