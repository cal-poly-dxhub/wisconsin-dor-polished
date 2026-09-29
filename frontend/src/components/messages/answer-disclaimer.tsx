/** Shown under every finished answer. Rendered by the UI, never streamed, so
 *  it stays out of the stored answer text and out of the prompts. Wording is
 *  pending DOR sign-off (Task 78). */
export const ANSWER_DISCLAIMER =
  'Answers are AI-generated and can be wrong. Check the linked sources before relying on them. ' +
  'This is not legal advice or an official Department of Revenue determination.';

export function AnswerDisclaimer() {
  return (
    <p className="chat-response-aligned mt-3 text-xs leading-relaxed text-muted-foreground">
      {ANSWER_DISCLAIMER}
    </p>
  );
}
