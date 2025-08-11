import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import * as path from 'path';

export class LambdaLayersStack extends cdk.Stack {
  public readonly stepFunctionTypesLayer: lambda.LayerVersion;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.stepFunctionTypesLayer = new lambda.LayerVersion(
      this,
      'StepFunctionTypesLayer',
      {
        code: lambda.Code.fromAsset(
          path.join(__dirname, '../step_function_types'),
          {
            bundling: {
              image: lambda.Runtime.PYTHON_3_12.bundlingImage,
              command: [
                'bash',
                '-c',
                [
                  'pip install -r requirements.txt -t /asset-output/python',
                  'cp -r *.py /asset-output/python/',
                ].join(' && '),
              ],
            },
          }
        ),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
        description:
          'Shared types for step functions - contains Pydantic models',
        layerVersionName: 'wisconsin-bot-step-function-types',
      }
    );

    new cdk.CfnOutput(this, 'StepFunctionTypesLayerArn', {
      value: this.stepFunctionTypesLayer.layerVersionArn,
      description: 'ARN of the step function types layer',
      exportName: 'WisconsinBot-StepFunctionTypesLayerArn',
    });

    new cdk.CfnOutput(this, 'StepFunctionTypesLayerVersionArn', {
      value: this.stepFunctionTypesLayer.layerVersionArn,
      description: 'Version ARN of the step function types layer',
    });
  }
}
