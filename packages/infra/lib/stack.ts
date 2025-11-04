import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { SessionsStack } from '../../sessions/infra/sessions-stack';
import { MessagesStack } from '../../messages/infra/messages-stack';
import { LambdaLayersStack } from '../../shared/lambda_layers/infra/lambda-layers-stack';
import { KnowledgeBaseStack } from '../../knowledge-base/infra/knowledge-base-stack';

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

    new MessagesStack(this, 'WisconsinMessagesStack', {
      description:
        'Stack providing messaging services (classifier and workflows).',
      stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
      websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
      sessionsTable: sessionsStack.sessionsTable,
      websocketCallbackUrl: sessionsStack.websocketCallbackUrl,
      faqKnowledgeBase: knowledgeBaseStack.faqKnowledgeBase,
      ragKnowledgeBase: knowledgeBaseStack.ragKnowledgeBase,
    });
  }
}
