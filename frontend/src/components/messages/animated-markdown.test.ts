/** @bun */
import { describe, test, expect } from 'bun:test';
import { toneForAuthorityLevel } from './animated-markdown';

// The deterministic classifier: authority level (1–9) → source tone. This is
// the backend-truth mapping that replaced fragile prose/href string-matching
// for coloring inline citation links.
describe('toneForAuthorityLevel', () => {
  test('maps each authority tier to its tone', () => {
    expect(toneForAuthorityLevel(2)).toBe('statute');
    expect(toneForAuthorityLevel(3)).toBe('case-law');
    expect(toneForAuthorityLevel(4)).toBe('admin-rule');
    expect(toneForAuthorityLevel(5)).toBe('wpam');
    expect(toneForAuthorityLevel(6)).toBe('faq');
    expect(toneForAuthorityLevel(7)).toBe('gov-pub');
    expect(toneForAuthorityLevel(8)).toBe('iaao');
    expect(toneForAuthorityLevel(9)).toBe('uspap');
  });

  test('returns undefined for missing level so callers fall back to heuristics', () => {
    expect(toneForAuthorityLevel(undefined)).toBeUndefined();
  });

  test('returns undefined for Constitution (level 1 — no dedicated tone yet)', () => {
    expect(toneForAuthorityLevel(1)).toBeUndefined();
  });

  test('returns undefined for out-of-range levels', () => {
    expect(toneForAuthorityLevel(0)).toBeUndefined();
    expect(toneForAuthorityLevel(99)).toBeUndefined();
  });

  // Regression: a news/gov-pub doc (authority 7) whose title contains "v." and
  // "Supreme Court" (e.g. "Wisconsin Supreme Court Decision: Lowe's v. Delavan")
  // was previously miscolored as case-law by prose string-matching. The
  // authority-level tone is unambiguous.
  test('gov-pub about a case is gov-pub, not case-law', () => {
    expect(toneForAuthorityLevel(7)).toBe('gov-pub');
  });
});
