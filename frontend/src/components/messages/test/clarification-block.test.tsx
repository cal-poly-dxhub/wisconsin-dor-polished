/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { ClarificationBlock } from '../clarification-block';

describe('ClarificationBlock', () => {
  test('renders the axis label, the question and every option', () => {
    const html = renderToString(
      <ClarificationBlock
        queryId="q-1"
        choices={['Agricultural', 'Undeveloped', 'Answer in general terms']}
        question="Is the parcel classified agricultural or undeveloped?"
        axis="property classification"
      />,
    );

    // Axis becomes the label, sentence-cased (it is rendered in small caps).
    expect(html).toContain('Property classification');
    expect(html).toContain('Is the parcel classified agricultural or undeveloped?');
    expect(html).toContain('Agricultural');
    expect(html).toContain('Undeveloped');
    expect(html).toContain('Answer in general terms');
  });

  test('uses the generic label and omits the question line when both are absent', () => {
    const html = renderToString(
      <ClarificationBlock queryId="q-3" choices={['Yes', 'No']} />,
    );

    expect(html).toContain('To narrow this down');
    expect(html).toContain('Yes');
    expect(html).not.toContain('<p class="mt-1');
  });

  test('renders nothing without options', () => {
    const html = renderToString(
      <ClarificationBlock queryId="q-4" choices={[]} question="Which year?" />,
    );

    expect(html).toBe('');
  });
});
