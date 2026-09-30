import { HTTPError } from 'ky';

export const RATE_LIMITED_MESSAGE =
  "You're sending messages faster than the assistant allows. Please wait a moment and try again.";

/**
 * User-facing text for a failed message send. A 429 comes from the per-user
 * limit in chat_api or the API Gateway throttle; both mean "slow down", not
 * "something broke".
 */
export function sendErrorMessage(error: unknown): string {
  if (error instanceof HTTPError && error.response.status === 429) {
    return RATE_LIMITED_MESSAGE;
  }
  return 'An error occurred while sending a message. Please try again.';
}
