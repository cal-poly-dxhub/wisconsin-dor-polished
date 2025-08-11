import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export class WisconsinBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Demo lambda function that returns hello world
    const helloWorldFunction = new lambda.Function(this, 'HelloWorldFunction', {
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(`
        exports.handler = async (event) => {
          console.log('Event:', JSON.stringify(event, null, 2));
          
          return {
            statusCode: 200,
            headers: {
              'Content-Type': 'application/json',
              'Access-Control-Allow-Origin': '*'
            },
            body: JSON.stringify({
              message: 'Hello World!',
              timestamp: new Date().toISOString(),
              event: event
            })
          };
        };
      `),
      description: 'Demo lambda function that returns hello world',
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
    });

    // Output the function ARN for reference
    new cdk.CfnOutput(this, 'HelloWorldFunctionArn', {
      value: helloWorldFunction.functionArn,
      description: 'ARN of the Hello World Lambda function',
    });

    // Output the function name
    new cdk.CfnOutput(this, 'HelloWorldFunctionName', {
      value: helloWorldFunction.functionName,
      description: 'Name of the Hello World Lambda function',
    });
  }
}
