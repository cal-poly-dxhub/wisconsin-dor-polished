import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigatewayv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface SessionsStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
}

export class SessionsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: SessionsStackProps) {
    super(scope, id, props);

    const sessionTable = new cdk.aws_dynamodb.Table(this, 'SessionTable', {
      partitionKey: {
        name: 'sessionId',
        type: cdk.aws_dynamodb.AttributeType.STRING,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      billingMode: cdk.aws_dynamodb.BillingMode.PAY_PER_REQUEST,
    });

    const apiHandler = new lambda.Function(this, 'ApiHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code: lambda.Code.fromAsset('bundle/chat_api', {
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
      description: 'Lambda function that handles API requests',
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      environment: {
        SESSIONS_TABLE_NAME: sessionTable.tableName,
      },
    });

    sessionTable.grantReadWriteData(apiHandler);

    apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['events:PutEvents'],
        resources: ['*'],
      })
    );

    const httpApi = new apigatewayv2.HttpApi(this, 'SessionsHttpApi', {
      apiName: 'Wisconsin Sessions API',
      description: 'HTTP API for managing chat sessions',
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [
          apigatewayv2.CorsHttpMethod.POST,
          apigatewayv2.CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: [
          'Content-Type',
          'Authorization',
          'X-Amz-Date',
          'X-Api-Key',
          'X-Amz-Security-Token',
        ],
      },
    });

    const devStage = new apigatewayv2.HttpStage(this, 'DevStage', {
      httpApi,
      stageName: 'dev',
      autoDeploy: true,
    });

    const lambdaIntegration =
      new apigatewayv2Integrations.HttpLambdaIntegration(
        'LambdaIntegration',
        apiHandler
      );

    httpApi.addRoutes({
      path: '/session',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}/message',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
    });

    new cdk.CfnOutput(this, 'ApiHandlerFunctionArn', {
      value: apiHandler.functionArn,
      description: 'ARN of the API handler Lambda function',
    });

    new cdk.CfnOutput(this, 'ApiHandlerFunctionName', {
      value: apiHandler.functionName,
      description: 'Name of the API handler Lambda function',
    });

    new cdk.CfnOutput(this, 'HttpApiUrl', {
      value: httpApi.url ?? '',
      description: 'URL of the HTTP API (API Gateway v2)',
      exportName: 'WisconsinBot-SessionsHttpApiUrl',
    });

    new cdk.CfnOutput(this, 'HttpApiId', {
      value: httpApi.httpApiId,
      description: 'ID of the HTTP API (API Gateway v2)',
    });

    new cdk.CfnOutput(this, 'DevStageUrl', {
      value: devStage.url,
      description: 'URL of the dev stage',
      exportName: 'WisconsinBot-SessionsDevStageUrl',
    });
  }
}
