import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { setLogRetention } from './log-retention';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import { getRetrievalEnv } from '../lib/retrieval-config';
import { CostAlarms, CostAlertsConfig } from './cost-alarms';

export interface GraphRAGMessagesStackProps extends cdk.StackProps {
  chatContractsLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  sessionsTable: cdk.aws_dynamodb.ITable;
  chatHistoryTable: cdk.aws_dynamodb.ITable;
  websocketCallbackUrl: string;
  /** Scopes execute-api:ManageConnections to the chat socket API. */
  websocketApiId: string;
  /**
   * Neptune Analytics graph to query, pinned by the `neptuneGraphId` CDK
   * context (infra/cdk.json). Scopes both the Lambda env and its IAM.
   */
  neptuneGraphId: string;
  rawBucketName: string;
  faqKnowledgeBaseId: string;
  faqUrlTable: cdk.aws_dynamodb.ITable;
  modelConfigTable: cdk.aws_dynamodb.ITable;
  /** Optional `costAlerts` context: alert email and thresholds (cost-alarms.ts). */
  costAlerts?: CostAlertsConfig;
}

export class GraphRAGMessagesStack extends cdk.NestedStack {

  constructor(scope: Construct, id: string, props: GraphRAGMessagesStackProps) {
    super(scope, id, props);

    // The single retrieval + answer Lambda: Phase A tool loop, adequacy judge,
    // and the Phase B answer stream over the chat WebSocket.
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
        layers: [props.chatContractsLayer, props.websocketUtilsLayer],
        description:
          'Agentic retrieval: Neptune graph + vector search with a Claude tool loop, adequacy judge, streamed answer',
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

    // Read access to the FAQ-URL table so faq.lookup_faq_url can attach the
    // public revenue.wi.gov link to each FAQ at query time.
    props.faqUrlTable.grantReadData(agenticRetrievalHandler);

    // Read access to the model config table for the externalized prompts.
    props.modelConfigTable.grantReadData(agenticRetrievalHandler);

    // Logs carry query text previews; keep 90 days rather than 731.
    setLogRetention(agenticRetrievalHandler, logs.RetentionDays.THREE_MONTHS);

    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: [
          `arn:aws:execute-api:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:${props.websocketApiId}/*/*/@connections/*`,
        ],
      })
    );

    // Neptune Analytics permissions (scoped to specific graph)
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'neptune-graph:ExecuteQuery',
          'neptune-graph:ReadDataViaQuery',
        ],
        resources: [
          `arn:aws:neptune-graph:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:graph/${props.neptuneGraphId}`,
        ],
      })
    );

    // S3 read permissions on the raw bucket: fetch_case_opinion and the
    // opinion backfill read case-law .txt files, and the get_worksheet /
    // get_flowchart tools read the worksheets/ and flowcharts/ sidecars.
    // Citation links use each document's public source_url; no presigned URLs
    // are minted.
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

    // Bedrock, scoped by model family rather than by exact model so a model
    // can still be swapped through an env var without a CDK change. The us.*
    // ids are cross-region inference profiles: calling one needs the profile
    // ARN (this account) AND the foundation model in each region it routes to
    // (us-east-1, us-east-2, us-west-2), hence the region wildcard. Models this
    // Lambda actually invokes (see config/retrieval.toml and
    // backend/lambdas/agentic_retrieval/config.py):
    //   us.anthropic.claude-sonnet-4-6              agent loop (Phase A),
    //                                               answer stream (Phase B),
    //                                               auto-refine
    //   us.anthropic.claude-haiku-4-5-20251001-v1:0 adequacy judge
    //   amazon.titan-embed-text-v2:0                vector_search query
    //                                               embedding
    // Nova 2 Lite is ingestion-only (Fargate), not invoked here.
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: [
          `arn:aws:bedrock:*:${cdk.Stack.of(this).account}:inference-profile/us.anthropic.*`,
          'arn:aws:bedrock:*::foundation-model/anthropic.*',
          `arn:aws:bedrock:${cdk.Stack.of(this).region}::foundation-model/amazon.titan-embed-text-v2:0`,
        ],
      })
    );

    // Bedrock KB Retrieve permissions for FAQ search
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:Retrieve'],
        resources: [
          `arn:aws:bedrock:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:knowledge-base/${props.faqKnowledgeBaseId}`,
        ],
      })
    );

    new CostAlarms(this, 'CostAlarms', {
      retrievalFunction: agenticRetrievalHandler,
      config: props.costAlerts,
    });

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
  }
}
