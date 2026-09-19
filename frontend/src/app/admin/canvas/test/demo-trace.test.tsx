/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { CanvasView } from '../canvas-view';
import { DEMO_ANSWER, DEMO_CHOICES, DEMO_RESOURCES, DEMO_TRACE } from '../fixtures/demo-trace';

// The demo trace is what /admin/canvas auto-plays, so it doubles as a
// full-canvas smoke test: every event shape in it must reach a real pane.
function renderDemo(): string {
  return renderToString(
    <CanvasView
      events={DEMO_TRACE}
      answer={{
        text: DEMO_ANSWER,
        streaming: false,
        complete: true,
        resources: DEMO_RESOURCES,
        choices: DEMO_CHOICES,
        error: null,
      }}
    />,
  );
}

describe('demo trace', () => {
  test('renders without falling back to a placeholder pane', () => {
    expect(renderDemo()).not.toContain('Coming soon');
  });

  test('exercises the panes added for the current pipeline', () => {
    const html = renderDemo();
    for (const marker of [
      'Request received', // request_received / history_loaded header
      'Auto Refine', // turn-0 refinement stage
      'Decision Flowchart', // router-seeded get_flowchart
      'Router seed',
      'Case Law Search', // find_case_law
      'Worksheet Registry', // list_worksheets
      'TID Worksheet', // get_worksheet
      'Adequacy Judge', // adequacy_judged phase
      'Answer Stream', // Phase B
      'Clarification chips', // choices from the CLARIFY finding
    ]) {
      expect(html).toContain(marker);
    }
  });
});
