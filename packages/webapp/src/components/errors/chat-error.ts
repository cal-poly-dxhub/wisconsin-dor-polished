/**
 * Generic type for app errors handled by the top level error boundary.
 */

import { v4 as uuidv4 } from 'uuid';

export class ChatError extends Error {
  recoverable: boolean;
  userMessage: string;
  timestamp: Date;
  id: string;

  constructor(
    error: Error,
    options: { recoverable: boolean; userMessage: string }
  ) {
    super(error.message);
    this.recoverable = options.recoverable;
    this.userMessage = options.userMessage;
    this.id = uuidv4();
    this.timestamp = new Date();
  }
}
