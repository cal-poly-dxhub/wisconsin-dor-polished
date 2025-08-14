import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { Construct } from 'constructs';

export interface MessagesStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
}

export class MessagesStack extends cdk.NestedStack {
  public readonly classifierFunction: lambda.Function;
  public readonly classifierStateMachine: sfn.StateMachine;

  constructor(scope: Construct, id: string, props: MessagesStackProps) {
    super(scope, id, props);

    this.classifierFunction = new lambda.Function(this, 'ClassifierFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code: lambda.Code.fromAsset('bundle/classifier', {
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
      layers: [props.stepFunctionTypesLayer],
      description:
        'Classifier Lambda function that classifies user queries and returns a ClassifierResult',
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
    });

    const classifierTask = new tasks.LambdaInvoke(this, 'ClassifierTask', {
      lambdaFunction: this.classifierFunction,
      // We only want the payload (the handler returns a dict already shaped as ClassifierResult)
      payloadResponseOnly: true,
      // The output of this task will be the ClassifierResult
      resultPath: '$',
    });

    const success = new sfn.Succeed(this, 'ClassifierSucceeded');

    const definition = classifierTask.next(success);

    this.classifierStateMachine = new sfn.StateMachine(
      this,
      'MessagesClassifierStateMachine',
      {
        definition,
        timeout: cdk.Duration.minutes(5),
      }
    );

    new cdk.CfnOutput(this, 'ClassifierFunctionArn', {
      value: this.classifierFunction.functionArn,
      description: 'ARN of the Classifier Lambda function',
    });

    new cdk.CfnOutput(this, 'ClassifierStateMachineArn', {
      value: this.classifierStateMachine.stateMachineArn,
      description: 'ARN of the classifier Step Functions state machine',
    });
  }
}
