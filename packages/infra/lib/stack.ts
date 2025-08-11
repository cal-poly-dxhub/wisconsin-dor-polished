import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { SessionsStack } from '../../sessions/infra/sessions-stack';
import { LambdaLayersStack } from '../../shared/lambda_layers/infra/lambda-layers-stack';

export class WisconsinBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const lambdaLayersStack = new LambdaLayersStack(this, 'LambdaLayersStack', {
      description: 'Shared lambda layers for the Wisconsin bot.',
    });

    new SessionsStack(this, 'WisconsinSessionsStack', {
      description:
        'Stack providing API and WebSocket session services for the Wisconsin bot.',
      stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
    });
  }
}
