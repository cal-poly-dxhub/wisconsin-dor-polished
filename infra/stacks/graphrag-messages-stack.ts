import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import { getRetrievalEnv } from '../lib/retrieval-config';

export interface GraphRAGMessagesStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  sessionsTable: cdk.aws_dynamodb.ITable;
  chatHistoryTable: cdk.aws_dynamodb.ITable;
  websocketCallbackUrl: string;
  neptuneGraphId: string;
  neptuneGraphEndpoint: string;
  rawBucketName: string;
  faqKnowledgeBaseId: string;
  faqUrlTable: cdk.aws_dynamodb.ITable;
  modelConfigTable: cdk.aws_dynamodb.ITable;
}

export class GraphRAGMessagesStack extends cdk.NestedStack {

  constructor(scope: Construct, id: string, props: GraphRAGMessagesStackProps) {
    super(scope, id, props);

    // Agentic Retrieval Lambda (replaces classifier + retrieval for GraphRAG path)
    const agenticRetrievalHandler = new lambda.Function(
      this,
      'AgenticRetrievalFunction',
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'handler.handler',
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
          'Agentic retrieval: Neptune graph + vector search with Claude tool loop, paced replay',
        timeout: cdk.Duration.seconds(120),
        memorySize: 512,
        tracing: lambda.Tracing.ACTIVE,
        environment: getRetrievalEnv({
          WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
          SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
          CHAT_HISTORY_TABLE_NAME: props.chatHistoryTable.tableName,
          NEPTUNE_GRAPH_ID: props.neptuneGraphId,
          RAW_BUCKET: props.rawBucketName,
          FAQ_KNOWLEDGE_BASE_ID: props.faqKnowledgeBaseId,
          FAQ_URL_TABLE_NAME: props.faqUrlTable.tableName,
          MODEL_CONFIG_TABLE_NAME: props.modelConfigTable.tableName,
          LOG_LEVEL: 'INFO',
          // Overrides the retrieval.toml default (false) to preserve this
          // stack's currently-deployed behavior.
          ENABLE_DISAMBIGUATION: 'true',
          // Enables the TOPIC_SHIFT verdict: a follow-up that opens an
          // unrelated subject short-circuits with a soft "start a new chat?"
          // suggestion instead of running the loop. Only acts mid-conversation.
          ENABLE_TOPIC_SHIFT: 'true',
        }),
      }
    );

    // Read-write access to chat history: the agent reads prior turns to
    // resolve follow-ups and writes the current turn after streaming.
    props.chatHistoryTable.grantReadWriteData(agenticRetrievalHandler);

    // Read access to the sessions table + permission to post to
    // API Gateway WebSocket connections, so report_error can surface
    // Lambda failures to the UI instead of the frontend spinning forever.
    props.sessionsTable.grantReadData(agenticRetrievalHandler);

    // Read access to the FAQ-URL table so _build_faq_resource can attach the
    // public revenue.wi.gov link to each FAQ at query time.
    props.faqUrlTable.grantReadData(agenticRetrievalHandler);

    // Read access to the model config table for externalized system prompt.
    props.modelConfigTable.grantReadData(agenticRetrievalHandler);

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
    // to feed full opinion text into the agent prompt. Citation links use
    // each document's public source_url; no presigned URLs are minted.
    // ListBucket is required so that GetObject on a missing key returns
    // NoSuchKey (404) instead of AccessDenied (403).
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [
          `arn:aws:s3:::${props.rawBucketName}/*`,
        ],
      })
    );
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket'],
        resources: [
          `arn:aws:s3:::${props.rawBucketName}`,
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

    // EventBridge Rule — invokes AgenticRetrieval directly (no Step Function).
    // The Lambda handles retrieval, answer streaming, and resource delivery
    // all in one invocation over WebSocket.
    const triggerGraphRAGProcessing = new events.Rule(
      this,
      'TriggerGraphRAGProcessing',
      {
        ruleName: 'TriggerGraphRAGMessageProcessing',
        eventPattern: {
          source: ['wisconsin-dor.chat-api'],
          detailType: ['ChatMessageReceived'],
        },
        enabled: true,
      }
    );

    triggerGraphRAGProcessing.addTarget(
      new targets.LambdaFunction(agenticRetrievalHandler, {
        event: events.RuleTargetInput.fromEventPath('$.detail'),
        maxEventAge: cdk.Duration.minutes(3),
        retryAttempts: 0,
      })
    );

    new cdk.CfnOutput(this, 'AgenticRetrievalFunctionArn', {
      value: agenticRetrievalHandler.functionArn,
      description: 'ARN of the Agentic Retrieval Lambda function',
    });
  }
}
