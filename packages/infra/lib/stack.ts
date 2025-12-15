import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { SessionsStack } from '../../sessions/infra/sessions-stack';
import { MessagesStack } from '../../messages/infra/messages-stack';
import { LambdaLayersStack } from '../../shared/lambda_layers/infra/lambda-layers-stack';
import { KnowledgeBaseStack } from '../../knowledge-base/infra/knowledge-base-stack';
import { CloudWatchIam } from '../../cloudwatch-iam/infra/cloudwatch-iam';

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
    });

    const cloudWatchIam = new CloudWatchIam(this, 'WisconsinCloudWatchIam', {
      resetCloudWatchIamRole: RESET_ClOUDWATCH_IAM_ROLE,
      description:
        'IAM roles and policies for CloudWatch logging for API Gateway.',
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
  }
}
