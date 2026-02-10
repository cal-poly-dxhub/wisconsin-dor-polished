import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import { bedrock } from '@cdklabs/generative-ai-cdk-constructs';

export interface MessagesStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  sessionsTable: cdk.aws_dynamodb.ITable;
  websocketCallbackUrl: string;
  faqKnowledgeBase: bedrock.VectorKnowledgeBase;
  ragKnowledgeBase: bedrock.VectorKnowledgeBase;
  chatHistoryTable: dynamodb.Table;
}

export class MessagesStack extends cdk.NestedStack {
  public readonly classifierFunction: lambda.Function;
  public readonly classifierStateMachine: sfn.StateMachine;
  public readonly modelConfigTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: MessagesStackProps) {
    super(scope, id, props);

    // Model Configuration Table
    this.modelConfigTable = new dynamodb.Table(this, 'ModelConfigTable', {
      partitionKey: {
        name: 'id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Classifier Lambda
    this.classifierFunction = new lambda.Function(this, 'ClassifierFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code: lambda.Code.fromAsset('bundle/classifier', {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash',
            '-c',
            [
              'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output',
              'cp -r . /asset-output',
            ].join(' && '),
          ],
        },
      }),
      layers: [props.stepFunctionTypesLayer, props.websocketUtilsLayer],
      description:
        'Classifier Lambda function that classifies user queries and returns a ClassifierResult',
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: {
        SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
        WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
        FAQ_KNOWLEDGE_BASE_ID: props.faqKnowledgeBase.knowledgeBaseId,
        MODEL_CONFIG_TABLE_NAME: this.modelConfigTable.tableName,
      },
    });

    // Grant DynamoDB read permissions to classifier function
    props.sessionsTable.grantReadData(this.classifierFunction);
    this.modelConfigTable.grantReadData(this.classifierFunction);

    // Grant Bedrock permissions for classifier function
    this.classifierFunction.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: ['*'],
      })
    );
    this.classifierFunction.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['bedrock-agent-runtime:Retrieve', 'bedrock:Retrieve'],
        resources: ['*'],
      })
    );

    // Grant API Gateway Management API permissions for WebSocket error reporting
    this.classifierFunction.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: ['*'],
      })
    );

    // Retrieval Lambda (accepts RetrieveJob)
    const retrievalHandler = new lambda.Function(this, 'RetrievalFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code: lambda.Code.fromAsset('bundle/retrieval', {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash',
            '-c',
            [
              'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output',
              'cp -r . /asset-output',
            ].join(' && '),
          ],
        },
      }),
      layers: [props.stepFunctionTypesLayer, props.websocketUtilsLayer],
      description:
        'Retrieval Lambda function that returns a RetrieveResult with jobs',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      environment: {
        WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
        RAG_KNOWLEDGE_BASE_ID: props.ragKnowledgeBase.knowledgeBaseId,
        MODEL_CONFIG_TABLE_NAME: this.modelConfigTable.tableName,
      },
    });

    // Grant DynamoDB read permissions to retrieval function
    this.modelConfigTable.grantReadData(retrievalHandler);

    // Grant Bedrock permissions for retrieval function
    retrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['bedrock-agent-runtime:Retrieve', 'bedrock:Retrieve'],
        resources: ['*'],
      })
    );

    // Response Streaming Lambda (accepts GenerateResponseJob)
    // This lambda now handles both resource streaming AND response generation
    const streamingHandler = new lambda.Function(
      this,
      'ResponseStreamingFunction',
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'main.handler',
        code: lambda.Code.fromAsset('bundle/streaming', {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              'bash',
              '-c',
              [
                'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output',
                'cp -r . /asset-output',
              ].join(' && '),
            ],
          },
        }),
        layers: [props.stepFunctionTypesLayer, props.websocketUtilsLayer],
        description:
          'Streaming Lambda for mixing/filtering documents, sending resources via WebSocket, and streaming LLM responses',
        timeout: cdk.Duration.seconds(60),
        memorySize: 512,
        environment: {
          SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
          WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
          MODEL_CONFIG_TABLE_NAME: this.modelConfigTable.tableName,
          CHAT_HISTORY_TABLE_NAME: props.chatHistoryTable.tableName,
        },
      }
    );

    // Grant DynamoDB read permissions to response streaming function
    props.sessionsTable.grantReadData(streamingHandler);
    this.modelConfigTable.grantReadData(streamingHandler);
    props.chatHistoryTable.grantReadWriteData(streamingHandler);

    // Grant API Gateway Management API permissions for sending WebSocket messages
    streamingHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: ['*'],
      })
    );

    // Grant Bedrock permissions for invoking models with response streaming
    streamingHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: ['*'],
      })
    );

    // Step Function Tasks
    const classifierTask = new tasks.LambdaInvoke(this, 'ClassifierTask', {
      lambdaFunction: this.classifierFunction,
      outputPath: '$.Payload',
    });

    const isFaq = sfn.Condition.stringEquals('$.query_class', 'faq');
    const isRag = sfn.Condition.stringEquals('$.query_class', 'rag');

    const retrievalTaskRag = new tasks.LambdaInvoke(this, 'RetrievalTaskRag', {
      lambdaFunction: retrievalHandler,
      outputPath: '$.Payload',
    });

    // Single streaming task that handles both resource and response streaming
    const streamingTaskFaq = new tasks.LambdaInvoke(this, 'StreamingTaskFaq', {
      lambdaFunction: streamingHandler,
      outputPath: '$.Payload',
    });

    const streamingTaskRag = new tasks.LambdaInvoke(this, 'StreamingTaskRag', {
      lambdaFunction: streamingHandler,
      outputPath: '$.Payload',
    });

    // Check if streaming was successful
    const checkStreamingSuccessFaq = new sfn.Choice(
      this,
      'CheckStreamingSuccessFaq'
    )
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'StreamingFailedFaq', {
          error: 'Streaming failed for FAQ',
          cause: 'The streaming lambda returned successful=false',
        })
      )
      .otherwise(new sfn.Pass(this, 'StreamingSuccessFaq'));

    const checkStreamingSuccessRag = new sfn.Choice(
      this,
      'CheckStreamingSuccessRag'
    )
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'StreamingFailedRag', {
          error: 'Streaming failed for RAG',
          cause: 'The streaming lambda returned successful=false',
        })
      )
      .otherwise(new sfn.Pass(this, 'StreamingSuccessRag'));

    // Pass state to select generate_response_job for streaming (FAQ)
    const selectGenerateResponseJobFaq = new sfn.Pass(
      this,
      'SelectGenerateResponseJobFaq',
      {
        parameters: {
          'job.$': '$.generate_response_job',
        },
        outputPath: '$.job',
      }
    );

    const selectGenerateResponseJobRag = new sfn.Pass(
      this,
      'SelectGenerateResponseJobRag',
      {
        parameters: {
          'job.$': '$.generate_response_job',
        },
        outputPath: '$.job',
      }
    );

    const selectRetrieveJob = new sfn.Pass(this, 'SelectRetrieveJob', {
      parameters: {
        'job.$': '$.retrieve_job',
      },
      outputPath: '$.job',
    });

    // FAQ branch: directly stream response (streaming lambda handles both docs and response)
    const faqBranch = selectGenerateResponseJobFaq
      .next(streamingTaskFaq)
      .next(checkStreamingSuccessFaq);

    // RAG branch: retrieve documents first, then stream (streaming lambda handles both docs and response)
    const ragBranch = selectRetrieveJob
      .next(retrievalTaskRag)
      .next(selectGenerateResponseJobRag)
      .next(streamingTaskRag)
      .next(checkStreamingSuccessRag);

    // Add error handling for when query_class is null (validation errors)
    const errorChoice = new sfn.Choice(this, 'BranchOnQueryClass')
      .when(isFaq, faqBranch)
      .when(isRag, ragBranch)
      .otherwise(
        new sfn.Fail(this, 'ClassificationFailed', {
          error: 'Classification failed - invalid query or processing error',
          cause:
            'The classifier returned null query_class, indicating a validation or processing error',
        })
      );

    const definition = classifierTask.next(errorChoice);

    this.classifierStateMachine = new sfn.StateMachine(
      this,
      'ChatStateMachine',
      {
        definition,
        stateMachineName: 'ChatStreamingStateMachine',
        timeout: cdk.Duration.minutes(5),
        tracingEnabled: true, // Enable X-Ray tracing for better observability
        logs: {
          destination: new cdk.aws_logs.LogGroup(this, 'StateMachineLogs', {
            logGroupName: `/aws/states/ChatStreamingStateMachine-${crypto.randomUUID()}`,
            retention: cdk.aws_logs.RetentionDays.ONE_WEEK,
          }),
          level: sfn.LogLevel.ALL,
          includeExecutionData: true,
        },
      }
    );

    const triggerMessageProcessing = new events.Rule(
      this,
      'TriggerMessageProcessing',
      {
        eventPattern: {
          source: ['wisconsin-dor.chat-api'],
          detailType: ['ChatMessageReceived'],
        },
      }
    );

    triggerMessageProcessing.addTarget(
      new targets.SfnStateMachine(this.classifierStateMachine, {
        input: events.RuleTargetInput.fromEventPath('$'),
      })
    );

    new cdk.CfnOutput(this, 'ClassifierFunctionArn', {
      value: this.classifierFunction.functionArn,
      description: 'ARN of the Classifier Lambda function',
    });

    new cdk.CfnOutput(this, 'RetrievalFunctionArn', {
      value: retrievalHandler.functionArn,
      description: 'ARN of the Retrieval Lambda function',
    });

    new cdk.CfnOutput(this, 'StreamingFunctionArn', {
      value: streamingHandler.functionArn,
      description: 'ARN of the Streaming Lambda function (handles both resource and response streaming)',
    });

    new cdk.CfnOutput(this, 'ChatStateMachineArn', {
      value: this.classifierStateMachine.stateMachineArn,
      description: 'ARN of the classifier Step Functions state machine',
    });

    new cdk.CfnOutput(this, 'ModelConfigTableName', {
      value: this.modelConfigTable.tableName,
      description: 'Name of the Model Configuration DynamoDB table',
    });
  }
}
