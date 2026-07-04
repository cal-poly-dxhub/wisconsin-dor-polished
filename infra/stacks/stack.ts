import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { SessionsStack } from './sessions-stack';
import { LambdaLayersStack } from './lambda-layers-stack';
import { GraphRAGStack } from './graphrag-stack';
import { GraphRAGMessagesStack } from './graphrag-messages-stack';
import { IngestionStack } from './ingestion-stack';
import { CloudWatchIam } from './cloudwatch-iam';
import { WebAppStack } from './webapp-stack';

const RESET_ClOUDWATCH_IAM_ROLE = false;

export class WisconsinBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const lambdaLayersStack = new LambdaLayersStack(this, 'LambdaLayersStack', {
      description: 'Shared lambda layers for the Wisconsin bot.',
    });

    const graphRAGStack = new GraphRAGStack(this, 'WisconsinGraphRAGStack', {
      description:
        'Stack providing GraphRAG services (Neptune Analytics + S3).',
    });

    const sessionsStack = new SessionsStack(this, 'WisconsinSessionsStack', {
      description:
        'Stack providing API and WebSocket session services for the Wisconsin bot.',
      stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
      websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
      rawBucketName: graphRAGStack.rawBucketName,
      workBucketName: graphRAGStack.workBucketName,
    });

    const graphRAGMessagesStack = new GraphRAGMessagesStack(
      this,
      'WisconsinGraphRAGMessagesStack',
      {
        description:
          'GraphRAG messaging services (agentic retrieval via EventBridge).',
        stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
        websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
        sessionsTable: sessionsStack.sessionsTable,
        chatHistoryTable: sessionsStack.chatHistoryTable,
        websocketCallbackUrl: sessionsStack.websocketCallbackUrl,
        neptuneGraphId: graphRAGStack.neptuneGraphId,
        neptuneGraphEndpoint: graphRAGStack.neptuneGraphEndpoint,
        rawBucketName: graphRAGStack.rawBucketName,
        faqKnowledgeBaseId: graphRAGStack.faqKnowledgeBaseId,
        faqUrlTable: graphRAGStack.faqUrlTable,
        modelConfigTable: graphRAGStack.modelConfigTable,
      }
    );

    const ingestionStack = new IngestionStack(this, 'WisconsinIngestionStack', {
      description:
        'Managed Fargate compute for GraphRAG ingestion pipeline.',
      rawBucketName: graphRAGStack.rawBucketName,
      workBucketName: graphRAGStack.workBucketName,
      neptuneGraphId: graphRAGStack.neptuneGraphId,
    });

    sessionsStack.apiHandler.addEnvironment(
      'INGESTION_CLUSTER_ARN',
      ingestionStack.cluster.clusterArn
    );
    sessionsStack.apiHandler.addEnvironment(
      'INGESTION_TASK_DEF_ARN',
      ingestionStack.taskDefinition.taskDefinitionArn
    );
    sessionsStack.apiHandler.addEnvironment(
      'INGESTION_SUBNET_IDS',
      ingestionStack.subnetIds
    );
    sessionsStack.apiHandler.addEnvironment(
      'INGESTION_SECURITY_GROUP_ID',
      ingestionStack.securityGroupId
    );
    sessionsStack.apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['ecs:RunTask'],
        resources: [ingestionStack.taskDefinition.taskDefinitionArn],
      })
    );
    sessionsStack.apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['iam:PassRole'],
        resources: [
          ingestionStack.taskDefinition.taskRole!.roleArn,
          ingestionStack.taskDefinition.executionRole!.roleArn,
        ],
      })
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

    new cdk.CfnOutput(this, 'ModelConfigTableName', {
      value: graphRAGStack.modelConfigTable.tableName,
      description: 'Name of the Model Configuration DynamoDB table',
      exportName: 'WisconsinBot-ModelConfigTableName',
    });

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

    new cdk.CfnOutput(this, 'GraphRAGFaqKnowledgeBaseId', {
      value: graphRAGStack.faqKnowledgeBaseId,
      description: 'FAQ Bedrock Knowledge Base ID (GraphRAG)',
      exportName: 'WisconsinBot-GraphRAGFaqKnowledgeBaseId',
    });

    new cdk.CfnOutput(this, 'GraphRAGFaqBucketName', {
      value: graphRAGStack.faqBucketName,
      description: 'S3 bucket for FAQ documents (GraphRAG)',
      exportName: 'WisconsinBot-GraphRAGFaqBucketName',
    });

    new cdk.CfnOutput(this, 'GraphRAGFaqDataSourceId', {
      value: graphRAGStack.faqDataSourceId,
      description: 'FAQ Bedrock KB Data Source ID (GraphRAG)',
      exportName: 'WisconsinBot-GraphRAGFaqDataSourceId',
    });

    new cdk.CfnOutput(this, 'IngestionClusterArn', {
      value: ingestionStack.cluster.clusterArn,
      description: 'Ingestion ECS Cluster ARN',
      exportName: 'WisconsinBot-IngestionClusterArn',
    });

    new cdk.CfnOutput(this, 'IngestionTaskDefinitionArn', {
      value: ingestionStack.taskDefinition.taskDefinitionArn,
      description: 'Ingestion Fargate Task Definition ARN',
      exportName: 'WisconsinBot-IngestionTaskDefinitionArn',
    });
  }
}
