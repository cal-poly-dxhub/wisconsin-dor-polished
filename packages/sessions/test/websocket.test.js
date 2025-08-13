const WebSocket = require('ws');

describe('WebSocket Integration Tests', () => {
  const WEBSOCKET_API_URL = process.env.WEBSOCKET_API_URL;
  const API_BASE_URL = process.env.API_BASE_URL;

  beforeAll(() => {
    if (!WEBSOCKET_API_URL) {
      throw new Error(
        'WEBSOCKET_API_URL environment variable is required for integration tests'
      );
    }
    if (!API_BASE_URL) {
      throw new Error(
        'SESSION_API_URL environment variable is required for integration tests'
      );
    }
  });

  // Helper function to create a new session
  const createSession = async () => {
    const response = await fetch(`${API_BASE_URL}/session`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(
        `Failed to create session: ${response.status} ${response.statusText}`
      );
    }

    const sessionData = await response.json();
    const sessionDataBody = JSON.parse(sessionData.body);
    return sessionDataBody.sessionId;
  };

  test('should connect, send message, and receive the same message back', async done => {
    const testMessage = 'Hello WebSocket Integration Test';
    let connectionEstablished = false;

    try {
      // First create a session using the session API
      const sessionId = await createSession();

      // Create WebSocket connection with the created sessionId query parameter
      const ws = new WebSocket(`${WEBSOCKET_API_URL}?sessionId=${sessionId}`);

      // Set a timeout to fail the test if it takes too long
      const timeout = setTimeout(() => {
        ws.close();
        done(new Error('Test timeout - WebSocket operations took too long'));
      }, 10000);

      ws.on('open', () => {
        connectionEstablished = true;

        // Send test message after connection is established
        ws.send(testMessage);
      });

      ws.on('message', data => {
        try {
          const receivedMessage = JSON.parse(data.toString());

          // Verify we received the exact same message back
          expect(receivedMessage).toBe(testMessage);

          // Clean up and complete the test
          clearTimeout(timeout);
          ws.close();
          done();
        } catch (error) {
          clearTimeout(timeout);
          ws.close();
          done(error);
        }
      });

      ws.on('error', error => {
        clearTimeout(timeout);
        console.error('WebSocket error:', error);
        done(new Error(`WebSocket error: ${error.message}`));
      });

      ws.on('close', (code, reason) => {
        clearTimeout(timeout);

        // If connection was never established, fail the test
        if (!connectionEstablished) {
          done(
            new Error(
              `WebSocket connection failed to establish. Close code: ${code}, reason: ${reason}`
            )
          );
        }
      });
    } catch (error) {
      // Handle session creation errors
      done(new Error(`Failed to create session: ${error.message}`));
    }
  });

  test('should handle connection with invalid session ID gracefully', done => {
    const invalidSessionId = 'non-existent-session-id';

    // Create WebSocket connection with invalid sessionId
    const ws = new WebSocket(
      `${WEBSOCKET_API_URL}?sessionId=${invalidSessionId}`
    );

    const timeout = setTimeout(() => {
      ws.close();
      done(
        new Error('Test timeout - expected connection to fail but it did not')
      );
    }, 10000);

    ws.on('open', () => {
      // Connection should not succeed with invalid session
      clearTimeout(timeout);
      ws.close();
      done(
        new Error(
          'WebSocket connection should have failed with invalid session ID'
        )
      );
    });

    ws.on('error', error => {
      // This is expected behavior for invalid session
      clearTimeout(timeout);
      console.log('Expected error for invalid session:', error.message);
      done(); // Test passes
    });

    ws.on('close', (code, reason) => {
      clearTimeout(timeout);
      // Connection closing is expected for invalid session
      done(); // Still pass - any close is acceptable for invalid session
    });
  });
});
