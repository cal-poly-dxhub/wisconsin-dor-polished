import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export class LambdaLayersStack extends cdk.NestedStack {
  public readonly stepFunctionTypesLayer: lambda.LayerVersion;
  public readonly websocketUtilsLayer: lambda.LayerVersion;

  constructor(scope: Construct, id: string, props?: cdk.NestedStackProps) {
    super(scope, id, props);

    this.stepFunctionTypesLayer = new lambda.LayerVersion(
      this,
      'StepFunctionTypesLayer',
      {
        code: lambda.Code.fromAsset('bundle/step_function_types', {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              'bash',
              '-c',
              [
                'pip install -r python/step_function_types/requirements.txt -t /asset-output/python',
                'cp -r python/. /asset-output/python',
              ].join(' && '),
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
        description:
          'Shared types for step functions - contains Pydantic models',
        layerVersionName: 'wisconsin-bot-step-function-types',
      }
    );

    this.websocketUtilsLayer = new lambda.LayerVersion(
      this,
      'WebSocketUtilsLayer',
      {
        code: lambda.Code.fromAsset('bundle/websocket_utils', {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              'bash',
              '-c',
              [
                'pip install -r python/websocket_utils/requirements.txt -t /asset-output/python',
                'cp -r python/. /asset-output/python',
              ].join(' && '),
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
        description:
          'Shared WebSocket utilities - contains WebSocket server and models',
        layerVersionName: 'wisconsin-bot-websocket-utils',
      }
    );

    new cdk.CfnOutput(this, 'StepFunctionTypesLayerArn', {
      value: this.stepFunctionTypesLayer.layerVersionArn,
      description: 'ARN of the step function types layer',
    });

    new cdk.CfnOutput(this, 'StepFunctionTypesLayerVersionArn', {
      value: this.stepFunctionTypesLayer.layerVersionArn,
      description: 'Version ARN of the step function types layer',
    });

    new cdk.CfnOutput(this, 'WebSocketUtilsLayerArn', {
      value: this.websocketUtilsLayer.layerVersionArn,
      description: 'ARN of the websocket utils layer',
    });

    new cdk.CfnOutput(this, 'WebSocketUtilsLayerVersionArn', {
      value: this.websocketUtilsLayer.layerVersionArn,
      description: 'Version ARN of the websocket utils layer',
    });
  }
}
