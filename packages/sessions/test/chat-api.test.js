/** @jest-environment node */
import fetch from 'node-fetch';

describe('Chat API Integration Tests', () => {
  const API_BASE_URL = process.env.API_BASE_URL;
  if (!API_BASE_URL) {
    throw new Error('API_BASE_URL environment variable is not set');
  }

  describe('POST /session', () => {
    it('should return a valid response for session creation', async () => {
      const response = await fetch(`${API_BASE_URL}/session`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      expect(response).toBeDefined();
      expect(response.status).toBeDefined();
      expect(response.headers.get('content-type')).toContain(
        'application/json'
      );

      const responseBody = await response.json();

      expect(responseBody).toBeDefined();
      expect(typeof responseBody).toBe('object');
    });
  });

  describe('POST /session/{session_id}/message', () => {
    it('should return a valid response for message processing', async () => {
      const testSessionId = 'test-session-id';
      const testMessage = 'Hello, how can I help you?';

      const response = await fetch(
        `${API_BASE_URL}/session/${testSessionId}/message`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message: testMessage,
          }),
        }
      );

      expect(response).toBeDefined();
      expect(response.status).toBeDefined();
      expect(response.headers.get('content-type')).toContain(
        'application/json'
      );

      const responseBody = await response.json();

      expect(responseBody).toBeDefined();
      expect(typeof responseBody).toBe('object');
    });
  });
});
