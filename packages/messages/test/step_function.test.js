/** @jest-environment node */
/* eslint-env node, jest */
/* global process, setTimeout, clearTimeout, Buffer, fetch, console */
const {
  SFNClient,
  StartExecutionCommand,
  DescribeExecutionCommand,
} = require('@aws-sdk/client-sfn');

const { LambdaClient, InvokeCommand } = require('@aws-sdk/client-lambda');
const WebSocket = require('ws');

// Import fetch for API calls

describe('Step Function Integration Tests', () => {
  const STEP_FUNCTION_ARN = process.env.STEP_FUNCTION_ARN;
  const AWS_REGION = process.env.AWS_REGION;
  const API_BASE_URL = process.env.API_BASE_URL;
  const WEBSOCKET_API_URL = process.env.WEBSOCKET_API_URL;

  // Lambda function ARNs
  const CLASSIFIER_ARN = process.env.CLASSIFIER_ARN;
  const RETRIEVAL_ARN = process.env.RETRIEVAL_ARN;
  const STREAMING_ARN = process.env.STREAMING_ARN;
  const RESOURCE_STREAMING_ARN = process.env.RESOURCE_STREAMING_ARN;

  let sharedSessionId;
  let sharedWebSocket;

  beforeAll(async () => {
    console.log('API_BASE_URL:', API_BASE_URL);

    if (!STEP_FUNCTION_ARN) {
      throw new Error('STEP_FUNCTION_ARN env var is required');
    }
    if (!AWS_REGION) {
      throw new Error('AWS_REGION env var is required');
    }
    if (!API_BASE_URL) {
      throw new Error('API_BASE_URL env var is required');
    }
    if (!WEBSOCKET_API_URL) {
      throw new Error('WEBSOCKET_API_URL env var is required');
    }
    if (!CLASSIFIER_ARN) {
      throw new Error('CLASSIFIER_ARN env var is required');
    }
    if (!RETRIEVAL_ARN) {
      throw new Error('RETRIEVAL_ARN env var is required');
    }
    if (!STREAMING_ARN) {
      throw new Error('STREAMING_ARN env var is required');
    }
    if (!RESOURCE_STREAMING_ARN) {
      throw new Error('RESOURCE_STREAMING_ARN env var is required');
    }

    // Get shared session ID for all tests
    try {
      console.log('Creating session...');
      sharedSessionId = await getSessionId();
      console.log('Session created:', sharedSessionId);

      if (!sharedSessionId) {
        throw new Error(
          'Failed to create session - received undefined/null session ID'
        );
      }
    } catch (error) {
      console.error('Error creating session:', error);
      throw error;
    }

    // Create shared WebSocket connection for all tests
    try {
      console.log('Creating WebSocket connection...');
      sharedWebSocket = new WebSocket(
        `${WEBSOCKET_API_URL}?sessionId=${sharedSessionId}`
      );

      // Wait for WebSocket connection to open
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          reject(new Error('WebSocket connection timeout'));
        }, 10000);

        sharedWebSocket.on('open', () => {
          clearTimeout(timeout);
          console.log('WebSocket connection established');
          resolve();
        });

        sharedWebSocket.on('error', error => {
          clearTimeout(timeout);
          reject(new Error(`WebSocket connection failed: ${error.message}`));
        });
      });
    } catch (error) {
      console.error('Error creating WebSocket connection:', error);
      throw error;
    }
  });

  afterAll(async () => {
    // Clean up WebSocket connection
    if (sharedWebSocket) {
      sharedWebSocket.close();
      console.log('WebSocket connection closed');
    }
  });

  /**
   * Get a session ID from the API
   */
  const getSessionId = async () => {
    const response = await fetch(`${API_BASE_URL}/session`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(
        `Failed to create session: ${response.status} - ${errorText}`
      );
    }

    const responseData = await response.json();

    // Parse the actual response format
    if (responseData.body) {
      const bodyData = JSON.parse(responseData.body);
      return bodyData.sessionId;
    }

    // Fallback for direct session ID response
    return responseData.sessionId || responseData.session_id;
  };

  /**
   * Invoke a Lambda function directly
   */
  const invokeLambda = async (functionArn, payload) => {
    const client = new LambdaClient({ region: AWS_REGION });

    const command = new InvokeCommand({
      FunctionName: functionArn,
      InvocationType: 'RequestResponse',
      Payload: Buffer.from(JSON.stringify(payload)),
    });

    const response = await client.send(command);

    if (response.StatusCode !== 200) {
      throw new Error(
        `Lambda invocation failed with status: ${response.StatusCode}`
      );
    }

    const decoded = Buffer.from(response.Payload).toString('utf-8');
    return JSON.parse(decoded);
  };

  /**
   * Polls the step function for completion.
   */
  const waitForExecution = async (executionArn, client) => {
    let status = 'RUNNING';
    let result;

    while (status === 'RUNNING') {
      const describeCommand = new DescribeExecutionCommand({
        executionArn,
      });

      const execution = await client.send(describeCommand);
      status = execution.status;

      if (status === 'SUCCEEDED') {
        result = JSON.parse(execution.output || '{}');
      } else if (status === 'FAILED') {
        throw new Error(`Step function execution failed: ${execution.cause}`);
      } else if (status === 'ABORTED') {
        throw new Error('Step function execution was aborted');
      }

      if (status === 'RUNNING') {
        await new Promise(resolve => setTimeout(resolve, 2000)); // Wait 2 seconds before checking again
      }
    }

    return result;
  };

  // Individual Lambda Function Tests
  describe('Individual Lambda Function Tests', () => {
    test('classifier lambda should return ClassifierResult with FAQ classification', async () => {
      jest.setTimeout(30000);

      // MessageEvent input for classifier
      const payload = {
        query: 'What is the Wisconsin State Capitol?',
        query_id: `test-classifier-${Date.now()}`,
        session_id: sharedSessionId,
      };

      const result = await invokeLambda(CLASSIFIER_ARN, payload);

      // Verify ClassifierResult structure
      expect(result).toBeDefined();
      expect(result).toHaveProperty('successful');
      expect(result).toHaveProperty('query_class');
      expect(result).toHaveProperty('stream_documents_job');
      expect(result).toHaveProperty('generate_response_job');
      expect(result).toHaveProperty('retrieve_job');

      // Should classify as either 'faq' or 'rag'
      expect(['faq', 'rag']).toContain(result.query_class);
      expect(result.successful).toBe(true);
    });

    test('retrieval lambda should return RetrieveResult with RAG documents', async () => {
      jest.setTimeout(30000);

      // RetrieveJob input for retrieval (RAG route - no "FAQ" in query)
      const payload = {
        query: 'How do I apply for a driver license in Wisconsin?',
        query_id: `test-retrieval-rag-${Date.now()}`,
        session_id: sharedSessionId,
      };

      const result = await invokeLambda(RETRIEVAL_ARN, payload);

      // Verify RetrieveResult structure
      expect(result).toBeDefined();
      expect(result).toHaveProperty('successful');
      expect(result).toHaveProperty('stream_documents_job');
      expect(result).toHaveProperty('generate_response_job');
      expect(result.successful).toBe(true);

      // Verify RAG-specific structure
      expect(result.stream_documents_job).toBeDefined();
      expect(result.stream_documents_job).toHaveProperty('query_id');
      expect(result.stream_documents_job).toHaveProperty('session_id');
      expect(result.stream_documents_job.resource_type).toBe('documents');
      expect(result.stream_documents_job).toHaveProperty('content');
      expect(result.stream_documents_job.content).toHaveProperty('documents');

      expect(result.generate_response_job).toBeDefined();
      expect(result.generate_response_job).toHaveProperty('query');
      expect(result.generate_response_job).toHaveProperty('query_id');
      expect(result.generate_response_job).toHaveProperty('session_id');
      expect(result.generate_response_job.resource_type).toBe('documents');
      expect(result.generate_response_job).toHaveProperty('resources');
      expect(result.generate_response_job.resources).toHaveProperty(
        'documents'
      );
    });

    test('retrieval lambda should return RetrieveResult with FAQ resource', async () => {
      jest.setTimeout(30000);

      // RetrieveJob input for retrieval (FAQ route - contains "FAQ" in query)
      const payload = {
        query: 'Show me FAQ about Wisconsin services',
        query_id: `test-retrieval-faq-${Date.now()}`,
        session_id: sharedSessionId,
      };

      const result = await invokeLambda(RETRIEVAL_ARN, payload);

      // Verify RetrieveResult structure
      expect(result).toBeDefined();
      expect(result).toHaveProperty('successful');
      expect(result).toHaveProperty('stream_documents_job');
      expect(result).toHaveProperty('generate_response_job');
      expect(result.successful).toBe(true);

      // Verify FAQ-specific structure
      expect(result.stream_documents_job).toBeDefined();
      expect(result.stream_documents_job).toHaveProperty('query_id');
      expect(result.stream_documents_job).toHaveProperty('session_id');
      expect(result.stream_documents_job.resource_type).toBe('faq');
      expect(result.stream_documents_job).toHaveProperty('content');
      expect(result.stream_documents_job.content).toHaveProperty('question');
      expect(result.stream_documents_job.content).toHaveProperty('answer');

      expect(result.generate_response_job).toBeDefined();
      expect(result.generate_response_job).toHaveProperty('query');
      expect(result.generate_response_job).toHaveProperty('query_id');
      expect(result.generate_response_job).toHaveProperty('session_id');
      expect(result.generate_response_job.resource_type).toBe('faq');
      expect(result.generate_response_job).toHaveProperty('resources');
      expect(result.generate_response_job.resources).toHaveProperty('question');
      expect(result.generate_response_job.resources).toHaveProperty('answer');
    });

    test('streaming lambda should return GenerateResponseResult', async () => {
      jest.setTimeout(30000);

      // GenerateResponseJob input for streaming
      const payload = {
        query: 'What is the Wisconsin State Capitol?',
        query_id: `test-streaming-${Date.now()}`,
        session_id: sharedSessionId,
        resource_type: 'faq',
        resources: {
          question: 'What is the Wisconsin State Capitol?',
          answer: 'The Wisconsin State Capitol is located in Madison.',
        },
      };

      const result = await invokeLambda(STREAMING_ARN, payload);

      // Verify GenerateResponseResult structure
      expect(result).toBeDefined();
      expect(result).toHaveProperty('successful');
      expect(typeof result.successful).toBe('boolean');
    });

    test('resource_streaming lambda should return StreamResourcesResult', async () => {
      jest.setTimeout(30000);

      // StreamResourcesJob input for resource streaming
      const payload = {
        query_id: `test-resource-streaming-${Date.now()}`,
        session_id: sharedSessionId,
        resource_type: 'faq',
        content: {
          question: 'What is the Wisconsin State Capitol?',
          answer: 'The Wisconsin State Capitol is located in Madison.',
        },
      };

      const result = await invokeLambda(RESOURCE_STREAMING_ARN, payload);

      // Verify StreamResourcesResult structure
      expect(result).toBeDefined();
      expect(result).toHaveProperty('successful');
      expect(typeof result.successful).toBe('boolean');
    });
  });

  // Step Function Integration Tests
  describe('Step Function Integration Tests', () => {
    test('should execute step function successfully with RAG query', async () => {
      jest.setTimeout(60000); // 60 seconds for step function execution

      const client = new SFNClient({ region: AWS_REGION });

      const input = {
        query: 'How do I renew my Wisconsin driver license?',
        query_id: `itest-rag-${Date.now()}`,
        session_id: sharedSessionId,
      };

      const command = new StartExecutionCommand({
        stateMachineArn: STEP_FUNCTION_ARN,
        input: JSON.stringify(input),
      });

      const response = await client.send(command);
      expect(response.executionArn).toBeDefined();

      const result = await waitForExecution(response.executionArn, client);

      // Verify the step function completed successfully
      expect(result).toBeDefined();

      // Step function returns an array of results from parallel execution
      expect(Array.isArray(result)).toBe(true);

      // Check that all parallel execution results are successful
      result.forEach(parallelResult => {
        expect(parallelResult).toHaveProperty('successful');
        expect(parallelResult.successful).toBe(true);
      });
    });

    test('should execute step function successfully with FAQ query', async () => {
      jest.setTimeout(60000); // 60 seconds for step function execution

      const client = new SFNClient({ region: AWS_REGION });

      const input = {
        query: 'Show me FAQ about Wisconsin state services',
        query_id: `itest-faq-${Date.now()}`,
        session_id: sharedSessionId,
      };

      const command = new StartExecutionCommand({
        stateMachineArn: STEP_FUNCTION_ARN,
        input: JSON.stringify(input),
      });

      const response = await client.send(command);
      expect(response.executionArn).toBeDefined();

      const result = await waitForExecution(response.executionArn, client);

      // Verify the step function completed successfully
      expect(result).toBeDefined();

      // Step function returns an array of results from parallel execution
      expect(Array.isArray(result)).toBe(true);

      // Check that all parallel execution results are successful
      result.forEach(parallelResult => {
        expect(parallelResult).toHaveProperty('successful');
        expect(parallelResult.successful).toBe(true);
      });
    });

    test('should handle step function with invalid input format', async () => {
      jest.setTimeout(60000); // 60 seconds for step function execution

      const client = new SFNClient({ region: AWS_REGION });

      const input = {
        // Missing required fields
        query_id: `itest-invalid-${Date.now()}`,
      };

      const command = new StartExecutionCommand({
        stateMachineArn: STEP_FUNCTION_ARN,
        input: JSON.stringify(input),
      });

      const response = await client.send(command);
      expect(response.executionArn).toBeDefined();

      // This should fail due to invalid input
      await expect(
        waitForExecution(response.executionArn, client)
      ).rejects.toThrow();
    });
  });
});
