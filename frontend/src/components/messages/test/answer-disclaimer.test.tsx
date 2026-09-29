/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { AnswerDisclaimer, ANSWER_DISCLAIMER } from '../answer-disclaimer';

describe('AnswerDisclaimer', () => {
  test('renders the disclaimer text', () => {
    const html = renderToString(<AnswerDisclaimer />);
    expect(html).toContain('Answers are AI-generated and can be wrong.');
    expect(html).toContain('not legal advice');
  });

  test('uses no dashes as punctuation', () => {
    expect(ANSWER_DISCLAIMER).not.toMatch(/[—–]/);
  });
});
