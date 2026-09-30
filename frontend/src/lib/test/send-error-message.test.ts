/** @bun */
import { describe, expect, test } from 'bun:test';
import { HTTPError } from 'ky';
import { RATE_LIMITED_MESSAGE, sendErrorMessage } from '../send-error-message';

const httpError = (status: number) =>
  new HTTPError(
    new Response('{}', { status }),
    new Request('https://api.example.gov/session/s/message', { method: 'POST' }),
    {} as never,
  );

describe('sendErrorMessage', () => {
  test('a 429 asks the user to slow down', () => {
    expect(sendErrorMessage(httpError(429))).toBe(RATE_LIMITED_MESSAGE);
  });

  test('anything else is the generic retry message', () => {
    expect(sendErrorMessage(httpError(500))).toContain('Please try again');
    expect(sendErrorMessage(new Error('network'))).toContain('Please try again');
  });
});
