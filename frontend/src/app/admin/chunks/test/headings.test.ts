import { describe, expect, it } from 'bun:test';
import { getHeadingPrefix, groupChunksByHeading } from '../lib/headings';
import type { ChunkIndexEntry } from '../lib/chunks-api';

function entry(pos: number, heading: string | null): ChunkIndexEntry {
  return {
    chunk_id: `c-${pos}`,
    pos,
    idx: pos,
    char_count: 100,
    heading,
    subheading: null,
    start_page: null,
    end_page: null,
  };
}

describe('getHeadingPrefix', () => {
  it('collapses statute subsections into the parent section', () => {
    expect(getHeadingPrefix('70.11(4) Property exempted')).toBe('§ 70.11');
    expect(getHeadingPrefix('70.11(4)(a) More detail')).toBe('§ 70.11');
    expect(getHeadingPrefix('§ 70.32 Real estate, how valued')).toBe('§ 70.32');
    expect(getHeadingPrefix('s. 70.995 Manufacturing')).toBe('§ 70.995');
  });

  it('keeps the Tax qualifier on admin rule headings', () => {
    expect(getHeadingPrefix('Tax 12.05(1)(a) Certification')).toBe('Tax 12.05');
    expect(getHeadingPrefix('TAX 12.50 Assessor certification')).toBe('Tax 12.50');
    // Without this the "Tax" would be dropped and the rule would collide with
    // a statute of the same number.
    expect(getHeadingPrefix('Tax 12.05')).not.toBe(getHeadingPrefix('12.05'));
  });

  it('recognises WPAM chapter headings', () => {
    expect(getHeadingPrefix('Chapter 9 — Agricultural Assessment')).toBe('Chapter 9');
    expect(getHeadingPrefix('Chapter 9: Land classification')).toBe('Chapter 9');
    expect(getHeadingPrefix('Ch. 12a Special properties')).toBe('Chapter 12A');
  });

  it('recognises appendix, part and section headings', () => {
    expect(getHeadingPrefix('Appendix B: Conversion charts')).toBe('Appendix B');
    expect(getHeadingPrefix('Part IV — Standards')).toBe('Part IV');
    expect(getHeadingPrefix('Section 3.1 Overview')).toBe('Section 3.1');
  });

  it('groups deep outline numbering under its first two levels', () => {
    expect(getHeadingPrefix('1.2.3 Overview')).toBe('1.2');
    expect(getHeadingPrefix('1.2.4 Detail')).toBe('1.2');
  });

  it('falls back to the whole heading, whitespace collapsed', () => {
    expect(getHeadingPrefix('Introduction   and   scope')).toBe('Introduction and scope');
    expect(getHeadingPrefix(null)).toBe('');
    expect(getHeadingPrefix('   ')).toBe('');
  });
});

describe('groupChunksByHeading', () => {
  it('merges consecutive chunks that share a section', () => {
    const groups = groupChunksByHeading([
      entry(0, '70.11(4) Property exempted'),
      entry(1, '70.11(4)(a) More'),
      entry(2, '70.32 Real estate'),
      entry(3, 'Chapter 9 — Agricultural'),
      entry(4, 'Chapter 9: Land classification'),
    ]);

    expect(groups.map(g => g.label)).toEqual(['§ 70.11', '§ 70.32', 'Chapter 9']);
    expect(groups.map(g => g.chunks.length)).toEqual([2, 1, 2]);
  });

  it('does not merge across a different section in between', () => {
    const groups = groupChunksByHeading([
      entry(0, 'Chapter 1'),
      entry(1, 'Chapter 2'),
      entry(2, 'Chapter 1'),
    ]);
    expect(groups).toHaveLength(3);
  });

  it('returns no groups for an empty list', () => {
    expect(groupChunksByHeading([])).toEqual([]);
  });
});
