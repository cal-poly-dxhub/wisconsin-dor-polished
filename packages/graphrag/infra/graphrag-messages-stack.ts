import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';

export interface GraphRAGMessagesStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  sessionsTable: cdk.aws_dynamodb.ITable;
  chatHistoryTable: cdk.aws_dynamodb.ITable;
  websocketCallbackUrl: string;
  neptuneGraphId: string;
  neptuneGraphEndpoint: string;
  rawBucketName: string;
  responseStreamingFunction: lambda.IFunction;
  resourceStreamingFunction: lambda.IFunction;
  enabled: boolean;
  faqKnowledgeBaseId: string;
  faqUrlTable: cdk.aws_dynamodb.ITable;
}

export class GraphRAGMessagesStack extends cdk.NestedStack {
  public readonly graphragStateMachine: sfn.StateMachine;

  constructor(scope: Construct, id: string, props: GraphRAGMessagesStackProps) {
    super(scope, id, props);

    // Agentic Retrieval Lambda (replaces classifier + retrieval for GraphRAG path)
    const agenticRetrievalHandler = new lambda.Function(
      this,
      'AgenticRetrievalFunction',
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'main.handler',
        code: lambda.Code.fromAsset('bundle/agentic_retrieval', {
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
          'Agentic retrieval Lambda: Neptune graph + vector search with Claude tool loop',
        timeout: cdk.Duration.seconds(120),
        memorySize: 512,
        tracing: lambda.Tracing.ACTIVE,
        environment: {
          WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
          SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
          CHAT_HISTORY_TABLE_NAME: props.chatHistoryTable.tableName,
          NEPTUNE_GRAPH_ID: props.neptuneGraphId,
          AGENTIC_MODEL_ID: 'us.anthropic.claude-sonnet-4-6',
          RAW_BUCKET: props.rawBucketName,
          FAQ_KNOWLEDGE_BASE_ID: props.faqKnowledgeBaseId,
          FAQ_URL_TABLE_NAME: props.faqUrlTable.tableName,
          LOG_LEVEL: 'INFO',
          LOG_AGENT_TRACE: 'true',
          LOG_TOOL_TRACE: 'true',
          LOG_NEPTUNE_TRACE: 'true',
          LOG_QUERY_TEXT: 'true',
          LOG_NEPTUNE_QUERY_TEXT: 'true',
          LOG_MAX_TEXT_CHARS: '500',
          LOG_MAX_QUERY_CHARS: '1000',
          EMIT_AGENT_TRACE: 'true',
        },
      }
    );

    // Read-write access to chat history: the agent reads prior turns to
    // resolve follow-ups and writes the current turn as a persistence
    // fallback before ResponseStreaming runs.
    props.chatHistoryTable.grantReadWriteData(agenticRetrievalHandler);

    // Read access to the sessions table + permission to post to
    // API Gateway WebSocket connections, so report_error can surface
    // Lambda failures to the UI instead of the frontend spinning forever.
    props.sessionsTable.grantReadData(agenticRetrievalHandler);

    // Read access to the FAQ-URL table so _build_faq_resource can attach the
    // public revenue.wi.gov link to each FAQ at query time.
    props.faqUrlTable.grantReadData(agenticRetrievalHandler);

    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: ['*'],
      })
    );

    // Neptune Analytics permissions (scoped to specific graph)
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'neptune-graph:ExecuteQuery',
          'neptune-graph:ReadDataViaQuery',
          'neptune-graph:GetQueryStatus',
        ],
        resources: [
          `arn:aws:neptune-graph:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:graph/${props.neptuneGraphId}`,
        ],
      })
    );

    // S3 read permissions: fetch_case_opinion GETs case-law .txt files
    // to feed full opinion text into the agent prompt. Citation URLs are
    // minted by the citation_resolver Lambda, not here.
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [
          `arn:aws:s3:::${props.rawBucketName}/*`,
        ],
      })
    );

    // Bedrock permissions (scoped to specific models)
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: ['*'],
      })
    );

    // Bedrock KB Retrieve permissions for FAQ search
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:Retrieve'],
        resources: [
          `arn:aws:bedrock:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:knowledge-base/*`,
        ],
      })
    );

    // Step Functions: AgenticRetrieval -> Parallel(ResourceStreaming, ResponseStreaming)
    const agenticRetrievalTask = new tasks.LambdaInvoke(
      this,
      'AgenticRetrievalTask',
      {
        lambdaFunction: agenticRetrievalHandler,
        outputPath: '$.Payload',
      }
    );

    // Build StreamResourcesJob from the flat AgenticRetrieval output.
    // Shape: { query_id, session_id, faqs, documents }
    const selectResourceStreamingJob = new sfn.Pass(
      this,
      'SelectResourceStreamingJob',
      {
        parameters: {
          'query_id.$': '$.query_id',
          'session_id.$': '$.session_id',
          'faqs.$': '$.faqs',
          'documents.$': '$.documents',
        },
      }
    );

    // Build GenerateResponseJob from the flat AgenticRetrieval output.
    // Shape: { query, query_id, session_id, faqs, documents }
    const selectGenerateResponseJob = new sfn.Pass(
      this,
      'SelectGenerateResponseJob',
      {
        parameters: {
          'query.$': '$.query',
          'query_id.$': '$.query_id',
          'session_id.$': '$.session_id',
          'faqs.$': '$.faqs',
          'documents.$': '$.documents',
        },
      }
    );

    const resourceStreamingTask = new tasks.LambdaInvoke(
      this,
      'ResourceStreamingTask',
      {
        lambdaFunction: props.resourceStreamingFunction,
        outputPath: '$.Payload',
      }
    );

    const responseStreamingTask = new tasks.LambdaInvoke(
      this,
      'ResponseStreamingTask',
      {
        lambdaFunction: props.responseStreamingFunction,
        outputPath: '$.Payload',
      }
    );

    const checkSuccess = new sfn.Choice(this, 'CheckRetrievalSuccess')
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'RetrievalFailed', {
          error: 'Agentic retrieval failed',
          cause:
            'The agentic retrieval lambda returned successful=false',
        })
      )
      .otherwise(
        new sfn.Parallel(this, 'ParallelGraphRAGStreaming')
          .branch(
            selectResourceStreamingJob.next(resourceStreamingTask)
          )
          .branch(
            selectGenerateResponseJob.next(responseStreamingTask)
          )
      );

    const definition = agenticRetrievalTask.next(checkSuccess);

    this.graphragStateMachine = new sfn.StateMachine(
      this,
      'GraphRAGStateMachine',
      {
        definition,
        stateMachineName: 'GraphRAGStreamingStateMachine',
        timeout: cdk.Duration.minutes(5),
        tracingEnabled: true,
        logs: {
          destination: new cdk.aws_logs.LogGroup(
            this,
            'GraphRAGStateMachineLogs',
            {
              logGroupName: `/aws/states/GraphRAGStreamingStateMachine`,
              retention: cdk.aws_logs.RetentionDays.ONE_WEEK,
            }
          ),
          level: sfn.LogLevel.ALL,
          includeExecutionData: true,
        },
      }
    );

    // EventBridge Rule (MUTUALLY EXCLUSIVE with existing rule)
    // Uses $.detail to extract just the UserQuery payload
    const triggerGraphRAGProcessing = new events.Rule(
      this,
      'TriggerGraphRAGProcessing',
      {
        ruleName: 'TriggerGraphRAGMessageProcessing',
        eventPattern: {
          source: ['wisconsin-dor.chat-api'],
          detailType: ['ChatMessageReceived'],
        },
        enabled: props.enabled,
      }
    );

    triggerGraphRAGProcessing.addTarget(
      new targets.SfnStateMachine(this.graphragStateMachine, {
        input: events.RuleTargetInput.fromEventPath('$.detail'),
      })
    );

    new cdk.CfnOutput(this, 'AgenticRetrievalFunctionArn', {
      value: agenticRetrievalHandler.functionArn,
      description: 'ARN of the Agentic Retrieval Lambda function',
    });

    new cdk.CfnOutput(this, 'GraphRAGStateMachineArn', {
      value: this.graphragStateMachine.stateMachineArn,
      description: 'ARN of the GraphRAG Step Functions state machine',
    });
  }
}
