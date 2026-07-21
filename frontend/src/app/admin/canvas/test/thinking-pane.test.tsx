/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { ThinkingPane } from '../components/thinking-pane';

describe('ThinkingPane', () => {
  test('renders the emitted reasoning sentence', () => {
    const html = renderToString(
      <ThinkingPane text="I should inspect the governing statute next." />,
    );

    expect(html).toContain('Thinking');
    expect(html).toContain('I should inspect the governing statute next.');
  });
});
