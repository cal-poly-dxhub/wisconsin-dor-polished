import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigatewayv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as apigatewayv2Authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface SessionsStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
}

export class SessionsStack extends cdk.NestedStack {
  public readonly sessionsTable: cdk.aws_dynamodb.Table;
  public readonly websocketCallbackUrl: string;
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly devStageUrl: string;
  public readonly websocketUrl: string;
  public readonly userPoolId: string;
  public readonly userPoolClientId: string;

  constructor(scope: Construct, id: string, props: SessionsStackProps) {
    super(scope, id, props);

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'wisconsin-user-pool',
      selfSignUpEnabled: true,
      signInAliases: {
        email: true,
      },
      autoVerify: {
        email: true,
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: true,
        },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.userPoolClient = new cognito.UserPoolClient(this, 'UserPoolClient', {
      userPool: this.userPool,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      generateSecret: false,
    });

    this.sessionsTable = new cdk.aws_dynamodb.Table(this, 'SessionTable', {
      partitionKey: {
        name: 'sessionId',
        type: cdk.aws_dynamodb.AttributeType.STRING,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      billingMode: cdk.aws_dynamodb.BillingMode.PAY_PER_REQUEST,
    });

    this.sessionsTable.addGlobalSecondaryIndex({
      indexName: 'connectionId',
      partitionKey: {
        name: 'connectionId',
        type: cdk.aws_dynamodb.AttributeType.STRING,
      },
      projectionType: cdk.aws_dynamodb.ProjectionType.ALL,
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
      layers: [props.stepFunctionTypesLayer, props.websocketUtilsLayer],
      description: 'Lambda function that handles API requests',
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      environment: {
        SESSIONS_TABLE_NAME: this.sessionsTable.tableName,
      },
    });

    this.sessionsTable.grantReadWriteData(apiHandler);

    apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['events:PutEvents'],
        resources: ['*'],
      })
    );

    const connectHandler = new lambda.Function(this, 'ConnectHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'connect.handler',
      code: lambda.Code.fromAsset('bundle/connect', {
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
      environment: {
        SESSIONS_TABLE_NAME: this.sessionsTable.tableName,
      },
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
    });

    const disconnectHandler = new lambda.Function(this, 'DisconnectHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'disconnect.handler',
      code: lambda.Code.fromAsset('bundle/disconnect', {
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
      environment: {
        SESSIONS_TABLE_NAME: this.sessionsTable.tableName,
      },
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
    });

    const defaultHandler = new lambda.Function(this, 'DefaultHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'default.handler',
      code: lambda.Code.fromAsset('bundle/default', {
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
      layers: [props.websocketUtilsLayer],
      environment: {
        SESSIONS_TABLE_NAME: this.sessionsTable.tableName,
      },
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
    });

    // Grant permissions to WebSocket handlers
    this.sessionsTable.grantReadWriteData(connectHandler);
    this.sessionsTable.grantReadWriteData(disconnectHandler);
    disconnectHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:Query'],
        resources: [
          this.sessionsTable.tableArn,
          `${this.sessionsTable.tableArn}/index/connectionId`,
        ],
      })
    );

    // Grant API Gateway Management API permissions for sending WebSocket messages
    defaultHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: ['*'],
      })
    );

    const websocketApi = new apigatewayv2.WebSocketApi(
      this,
      'SessionsWebSocketApi',
      {
        apiName: 'Wisconsin Sessions WebSocket API',
        description: 'WebSocket API for managing chat sessions',
      }
    );

    const connectIntegration =
      new apigatewayv2Integrations.WebSocketLambdaIntegration(
        'ConnectIntegration',
        connectHandler
      );

    const disconnectIntegration =
      new apigatewayv2Integrations.WebSocketLambdaIntegration(
        'DisconnectIntegration',
        disconnectHandler
      );

    const defaultIntegration =
      new apigatewayv2Integrations.WebSocketLambdaIntegration(
        'DefaultIntegration',
        defaultHandler
      );

    websocketApi.addRoute('$connect', {
      integration: connectIntegration,
    });
    websocketApi.addRoute('$disconnect', {
      integration: disconnectIntegration,
    });
    websocketApi.addRoute('$default', {
      integration: defaultIntegration,
    });

    // Create CloudWatch log groups for WebSocket API logging
    const executionLogGroup = new logs.LogGroup(
      this,
      'WebSocketExecutionLogs',
      {
        logGroupName: `/aws/apigateway/websocket-execution-logs`,
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }
    );

    const websocketStage = new apigatewayv2.WebSocketStage(
      this,
      'WebSocketDevStage',
      {
        webSocketApi: websocketApi,
        stageName: 'dev',
        autoDeploy: true,
      }
    );

    // Use L1 construct to enable execution logging (not supported in L2 WebSocket constructs)
    const cfnStage = websocketStage.node.defaultChild as apigatewayv2.CfnStage;

    // Enable execution logging and detailed metrics
    cfnStage.defaultRouteSettings = {
      loggingLevel: 'INFO', // 'ERROR' | 'INFO' | 'OFF'
      dataTraceEnabled: false, // true => full req/resp (verbose)
      detailedMetricsEnabled: true,
    };

    // Grant API Gateway permission to write to CloudWatch Logs
    executionLogGroup.grantWrite(
      new iam.ServicePrincipal('apigateway.amazonaws.com')
    );

    // Add WebSocket callback URL to default handler environment
    defaultHandler.addEnvironment(
      'WEBSOCKET_CALLBACK_URL',
      websocketStage.callbackUrl
    );

    // Store the callback URL for use by other stacks
    this.websocketCallbackUrl = websocketStage.callbackUrl;

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

    const authorizer = new apigatewayv2Authorizers.HttpJwtAuthorizer(
      'CognitoAuthorizer',
      `https://cognito-idp.${cdk.Stack.of(this).region}.amazonaws.com/${
        this.userPool.userPoolId
      }`,
      {
        jwtAudience: [this.userPoolClient.userPoolClientId],
      }
    );

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
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}/message',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
      authorizer: authorizer,
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

    new cdk.CfnOutput(this, 'WebSocketApiUrl', {
      value: websocketStage.url,
      description: 'URL of the WebSocket API',
      exportName: 'WisconsinBot-WebSocketApiUrl',
    });

    new cdk.CfnOutput(this, 'WebSocketApiId', {
      value: websocketApi.apiId,
      description: 'ID of the WebSocket API',
    });

    new cdk.CfnOutput(this, 'WebSocketExecutionLogGroup', {
      value: executionLogGroup.logGroupName,
      description: 'CloudWatch Log Group for WebSocket execution logs',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: this.userPool.userPoolId,
      description: 'Cognito User Pool ID',
      exportName: 'WisconsinBot-UserPoolId',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: this.userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
      exportName: 'WisconsinBot-UserPoolClientId',
    });

    new cdk.CfnOutput(this, 'CognitoRegion', {
      value: cdk.Stack.of(this).region,
      description: 'AWS Region for Cognito',
      exportName: 'WisconsinBot-CognitoRegion',
    });

    this.devStageUrl = devStage.url;
    this.websocketUrl = websocketStage.url;
    this.userPoolId = this.userPool.userPoolId;
    this.userPoolClientId = this.userPoolClient.userPoolClientId;
  }
}
