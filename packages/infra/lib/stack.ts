import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { SessionsStack } from '../../sessions/infra/sessions-stack';
import { MessagesStack } from '../../messages/infra/messages-stack';
import { LambdaLayersStack } from '../../shared/lambda_layers/infra/lambda-layers-stack';
import { KnowledgeBaseStack } from '../../knowledge-base/infra/knowledge-base-stack';
import { GraphRAGStack } from '../../graphrag/infra/graphrag-stack';
import { GraphRAGMessagesStack } from '../../graphrag/infra/graphrag-messages-stack';
import { CloudWatchIam } from '../../cloudwatch-iam/infra/cloudwatch-iam';
import { WebAppStack } from '../../webapp/infra/webapp-stack';

const RESET_ClOUDWATCH_IAM_ROLE = false;

export class WisconsinBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const lambdaLayersStack = new LambdaLayersStack(this, 'LambdaLayersStack', {
      description: 'Shared lambda layers for the Wisconsin bot.',
    });

    const sessionsStack = new SessionsStack(this, 'WisconsinSessionsStack', {
      description:
        'Stack providing API and WebSocket session services for the Wisconsin bot.',
      stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
      websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
    });

    const knowledgeBaseStack = new KnowledgeBaseStack(
      this,
      'WisconsinKnowledgeBaseStack',
      {
        description:
          'Stack providing knowledge base services for the Wisconsin bot.',
      }
    );

    // GraphRAG feature flag: mutually exclusive EventBridge rules
    const USE_GRAPHRAG = this.node.tryGetContext('useGraphRAG') === 'true';

    const messagesStack = new MessagesStack(this, 'WisconsinMessagesStack', {
      description:
        'Stack providing messaging services (classifier and workflows).',
      stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
      websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
      sessionsTable: sessionsStack.sessionsTable,
      websocketCallbackUrl: sessionsStack.websocketCallbackUrl,
      faqKnowledgeBase: knowledgeBaseStack.faqKnowledgeBase,
      ragKnowledgeBase: knowledgeBaseStack.ragKnowledgeBase,
      chatHistoryTable: sessionsStack.chatHistoryTable,
      useGraphRAG: USE_GRAPHRAG,
    });

    // GraphRAG Backend (NEW, deployed alongside existing)
    const graphRAGStack = new GraphRAGStack(this, 'WisconsinGraphRAGStack', {
      description:
        'Stack providing GraphRAG services (Neptune Analytics + S3).',
    });

    const graphRAGMessagesStack = new GraphRAGMessagesStack(
      this,
      'WisconsinGraphRAGMessagesStack',
      {
        description:
          'GraphRAG messaging services (agentic retrieval + state machine).',
        stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
        websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
        sessionsTable: sessionsStack.sessionsTable,
        websocketCallbackUrl: sessionsStack.websocketCallbackUrl,
        neptuneGraphId: graphRAGStack.neptuneGraphId,
        neptuneGraphEndpoint: graphRAGStack.neptuneGraphEndpoint,
        responseStreamingFunction: messagesStack.responseStreamingFunction,
        resourceStreamingFunction: messagesStack.resourceStreamingFunction,
        enabled: USE_GRAPHRAG,
      }
    );

    const cloudWatchIam = new CloudWatchIam(this, 'WisconsinCloudWatchIam', {
      resetCloudWatchIamRole: RESET_ClOUDWATCH_IAM_ROLE,
      description:
        'IAM roles and policies for CloudWatch logging for API Gateway.',
    });

    const domainName = this.node.tryGetContext('domainName');
    const hostedZoneName = this.node.tryGetContext('hostedZoneName');
    const hostedZoneId = this.node.tryGetContext('hostedZoneId');

    const webAppStack = new WebAppStack(this, 'WisconsinWebAppStack', {
      description:
        'CloudFront + Lambda hosting for the Wisconsin bot web application.',
      userPool: sessionsStack.userPool,
      userPoolClient: sessionsStack.userPoolClient,
      httpApiUrl: sessionsStack.httpApiUrl,
      websocketApiUrl: sessionsStack.websocketApiUrl,
      domainName,
      hostedZoneName,
      hostedZoneId,
    });

    new cdk.CfnOutput(this, 'ApiBaseUrl', {
      value: sessionsStack.httpApiUrl,
      description: 'Base URL of the HTTP API',
      exportName: 'WisconsinBot-ApiBaseUrl',
    });

    new cdk.CfnOutput(this, 'WebSocketUrl', {
      value: sessionsStack.websocketApiUrl,
      description: 'URL of the WebSocket API',
      exportName: 'WisconsinBot-WebSocketUrl',
    });

    new cdk.CfnOutput(this, 'CognitoUserPoolId', {
      value: sessionsStack.userPool.userPoolId,
      description: 'Cognito User Pool ID',
      exportName: 'WisconsinBot-CognitoUserPoolId',
    });

    new cdk.CfnOutput(this, 'CognitoUserPoolClientId', {
      value: sessionsStack.userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
      exportName: 'WisconsinBot-CognitoUserPoolClientId',
    });

    new cdk.CfnOutput(this, 'WebAppUrl', {
      value: webAppStack.distributionUrl,
      description: 'URL of the web application',
    });

    new cdk.CfnOutput(this, 'FaqBucketName', {
      value: knowledgeBaseStack.faqBucketName,
      description: 'S3 bucket for FAQ documents',
      exportName: 'WisconsinBot-FaqBucketName',
    });

    new cdk.CfnOutput(this, 'RagBucketName', {
      value: knowledgeBaseStack.ragBucketName,
      description: 'S3 bucket for RAG documents',
      exportName: 'WisconsinBot-RagBucketName',
    });

    new cdk.CfnOutput(this, 'ModelConfigTableName', {
      value: messagesStack.modelConfigTable.tableName,
      description: 'Name of the Model Configuration DynamoDB table',
      exportName: 'WisconsinBot-ModelConfigTableName',
    });

    // GraphRAG outputs
    new cdk.CfnOutput(this, 'GraphRAGRawBucketName', {
      value: graphRAGStack.rawBucketName,
      description: 'S3 bucket for GraphRAG raw documents',
      exportName: 'WisconsinBot-GraphRAGRawBucketName',
    });

    new cdk.CfnOutput(this, 'GraphRAGWorkBucketName', {
      value: graphRAGStack.workBucketName,
      description: 'S3 bucket for GraphRAG work data',
      exportName: 'WisconsinBot-GraphRAGWorkBucketName',
    });

    new cdk.CfnOutput(this, 'GraphRAGNeptuneGraphId', {
      value: graphRAGStack.neptuneGraphId,
      description: 'Neptune Analytics Graph ID',
      exportName: 'WisconsinBot-NeptuneGraphId',
    });

    new cdk.CfnOutput(this, 'GraphRAGStateMachineArn', {
      value: graphRAGMessagesStack.graphragStateMachine.stateMachineArn,
      description: 'ARN of the GraphRAG Step Functions state machine',
      exportName: 'WisconsinBot-GraphRAGStateMachineArn',
    });
  }
}
