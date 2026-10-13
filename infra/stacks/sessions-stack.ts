import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigatewayv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as apigatewayv2Authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { AdminsGroup } from './admins-group';
import { setLogRetention } from './log-retention';
import { SsoConfig, SsoResources } from './sso';

export interface SessionsStackProps extends cdk.StackProps {
  chatContractsLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  // chat_api writes documents under raw/ for /admin/ingest.
  rawBucketName: string;
  // chat_api reads extracted/ chunk artifacts for the /admin/chunks pages.
  workBucketName: string;
  /** DOR single sign-on (see ./sso.ts). Undefined leaves the pool as it was. */
  sso?: SsoConfig;
  /** Browser origins allowed to call the HTTP API (CORS). Unset allows any. */
  allowedOrigins?: string[];
}

export class SessionsStack extends cdk.NestedStack {
  public readonly sessionsTable: cdk.aws_dynamodb.Table;
  public readonly websocketCallbackUrl: string;
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly chatHistoryTable: dynamodb.Table;
  public readonly httpApiUrl: string;
  public readonly websocketApiUrl: string;
  public readonly apiHandler: lambda.Function;
  /** Scopes execute-api:ManageConnections for the Lambdas that post to sockets. */
  public readonly websocketApiId: string;
  /** Extra NEXT_PUBLIC_* env for the web app when SSO is on; empty otherwise. */
  public readonly ssoFrontendEnv: Record<string, string>;

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
      // Every user account lives here; never delete it with the stack.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // The `Admins` group gates every /admin/* API (require_admin() in chat_api)
    // and the /admin pages (<ProtectedRoute requireAdmin>). See admins-group.ts.
    new AdminsGroup(this, 'AdminsGroup', { userPool: this.userPool });

    const sso = props.sso
      ? new SsoResources(this, 'Sso', { userPool: this.userPool, config: props.sso })
      : undefined;
    this.ssoFrontendEnv = sso?.frontendEnv ?? {};

    this.userPoolClient = new cognito.UserPoolClient(this, 'UserPoolClient', {
      userPool: this.userPool,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      generateSecret: false,
      ...sso?.clientOptions,
    });
    // The client names the provider, so the provider must exist first.
    if (sso?.provider) this.userPoolClient.node.addDependency(sso.provider);

    this.sessionsTable = new dynamodb.Table(this, 'SessionTable', {
      partitionKey: {
        name: 'sessionId',
        type: cdk.aws_dynamodb.AttributeType.STRING,
      },
      // User data: kept if the stack is deleted, restorable to any second in
      // the last 35 days.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
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

    this.sessionsTable.addGlobalSecondaryIndex({
      indexName: 'userIdIndex',
      partitionKey: {
        name: 'userId',
        type: cdk.aws_dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'lastMessageAt',
        type: cdk.aws_dynamodb.AttributeType.STRING,
      },
      projectionType: cdk.aws_dynamodb.ProjectionType.ALL,
    });

    this.chatHistoryTable = new dynamodb.Table(this, 'ChatHistoryTable', {
      partitionKey: {
        name: 'queryId',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      // Every query and answer; kept if the stack is deleted, with PITR.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });

    this.chatHistoryTable.addGlobalSecondaryIndex({
      indexName: 'sessionIdKey',
      partitionKey: {
        name: 'sessionId',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Lean index backing the admin activity list. A GSI's projection can't be
    // modified in place (CloudFormation requires replacement, which the
    // one-GSI-per-update rule blocks), so each projection change is a new index
    // name — activityIndexV2 (superseded, removed) → activityIndexV3, which adds
    // the `rating` scalar. The list projects/filters on `rating` to surface the
    // middle ("mixed") rating; `thumbUp` alone can't distinguish mid from down.
    // The handler reads the index name from ACTIVITY_INDEX_NAME.
    this.chatHistoryTable.addGlobalSecondaryIndex({
      indexName: 'activityIndexV3',
      partitionKey: {
        name: 'gsi1pk',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.INCLUDE,
      nonKeyAttributes: ['sessionId', 'query', 'thumbUp', 'feedback', 'rating'],
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
      layers: [props.chatContractsLayer, props.websocketUtilsLayer],
      description: 'Lambda function that handles API requests',
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        SESSIONS_TABLE_NAME: this.sessionsTable.tableName,
        MESSAGES_TABLE_NAME: this.chatHistoryTable.tableName,
        ACTIVITY_INDEX_NAME: 'activityIndexV3',
        RAW_BUCKET_NAME: props.rawBucketName,
        WORK_BUCKET_NAME: props.workBucketName,
      },
    });

    this.apiHandler = apiHandler;

    // Per-user message limits (chat_api check_message_rate_limit): one
    // counter item per user per minute / hour window, expired by TTL. The API
    // Gateway throttle is shared by all users; this bounds each account's
    // Bedrock spend. Ephemeral data, so nothing to retain.
    const rateLimitTable = new dynamodb.Table(this, 'MessageRateLimitTable', {
      partitionKey: { name: 'key', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'expiresAt',
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    rateLimitTable.grantReadWriteData(apiHandler);
    apiHandler.addEnvironment('RATE_LIMIT_TABLE_NAME', rateLimitTable.tableName);
    apiHandler.addEnvironment('MESSAGES_PER_MINUTE', '8');
    apiHandler.addEnvironment('MESSAGES_PER_HOUR', '60');
    this.sessionsTable.grantReadWriteData(apiHandler);
    this.chatHistoryTable.grantReadWriteData(apiHandler);

    apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:PutObject'],
        resources: [`arn:aws:s3:::${props.rawBucketName}/raw/*`],
      })
    );
    apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [
          `arn:aws:s3:::${props.workBucketName}`,
          `arn:aws:s3:::${props.workBucketName}/extracted/*`,
        ],
      })
    );
    apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['events:PutEvents'],
        // emit_message_event publishes to the default bus only.
        resources: [
          `arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:event-bus/default`,
        ],
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
        // $connect verifies the caller's Cognito ID token against these and
        // binds the connection only to a session that caller owns.
        USER_POOL_ID: this.userPool.userPoolId,
        USER_POOL_CLIENT_ID: this.userPoolClient.userPoolClientId,
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

    // Grant permissions to WebSocket handlers
    this.sessionsTable.grantReadWriteData(connectHandler);
    this.sessionsTable.grantReadWriteData(disconnectHandler);

    // Logs carry request details; keep 90 days rather than the 731-day default.
    for (const fn of [apiHandler, connectHandler, disconnectHandler]) {
      setLogRetention(fn, logs.RetentionDays.THREE_MONTHS);
    }

    const websocketApi = new apigatewayv2.WebSocketApi(
      this,
      'SessionsWebSocketApi',
      {
        apiName: 'Wisconsin Sessions WebSocket API',
        description: 'WebSocket API for managing chat sessions',
      }
    );

    this.websocketApiId = websocketApi.apiId;

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

    websocketApi.addRoute('$connect', {
      integration: connectIntegration,
    });
    websocketApi.addRoute('$disconnect', {
      integration: disconnectIntegration,
    });

    const websocketStage = new apigatewayv2.WebSocketStage(
      this,
      'WebSocketDevStage',
      {
        webSocketApi: websocketApi,
        stageName: 'dev',
        autoDeploy: true,
      }
    );

    this.websocketApiUrl = websocketStage.url;

    // Route settings are not exposed on the L2 WebSocket stage. No execution
    // logging: it needs an account-level API Gateway CloudWatch role, which
    // this stack does not manage.
    const cfnStage = websocketStage.node.defaultChild as apigatewayv2.CfnStage;
    cfnStage.defaultRouteSettings = {
      detailedMetricsEnabled: true,
      // Whole-API ceiling (all users together). Answers stream server-side
      // via post_to_connection, which these limits do not count.
      throttlingRateLimit: 20,
      throttlingBurstLimit: 40,
    };

    // The retrieval Lambda posts answers back to clients through this URL.
    this.websocketCallbackUrl = websocketStage.callbackUrl;

    const httpApi = new apigatewayv2.HttpApi(this, 'SessionsHttpApi', {
      apiName: 'Wisconsin Sessions API',
      description: 'HTTP API for managing chat sessions',
      // Only the throttled `dev` stage below is served; an auto-created
      // $default stage would expose every route without those limits.
      createDefaultStage: false,
      corsPreflight: {
        // The web app's own origin(s) from the `allowedOrigins` context
        // (infra/cdk.json). A fresh account has no CloudFront URL until its
        // first deploy, so unset falls back to any origin.
        allowOrigins: props.allowedOrigins?.length ? props.allowedOrigins : ['*'],
        allowMethods: [
          apigatewayv2.CorsHttpMethod.GET,
          apigatewayv2.CorsHttpMethod.POST,
          apigatewayv2.CorsHttpMethod.PATCH,
          apigatewayv2.CorsHttpMethod.DELETE,
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
      // Whole-API ceiling (all users together), well above normal use.
      throttle: { rateLimit: 25, burstLimit: 50 },
    });
    // The one route that spends Bedrock money: sending a chat message. A
    // tighter ceiling so a runaway client cannot run up the model bill.
    (devStage.node.defaultChild as apigatewayv2.CfnStage).routeSettings = {
      // Sized for a room of ~30 people pressing send together (a training
      // or demo); the per-user limit below is what bounds one account.
      // routeSettings is raw CloudFormation JSON (not an L2 prop), so the
      // keys must be PascalCase; camelCase fails at deploy, not at synth.
      'POST /session/{sessionId}/message': {
        ThrottlingRateLimit: 10,
        ThrottlingBurstLimit: 30,
      },
    };

    this.httpApiUrl = devStage.url;

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
      path: '/sessions',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}',
      methods: [apigatewayv2.HttpMethod.PATCH],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}',
      methods: [apigatewayv2.HttpMethod.DELETE],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}/message',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}/history',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/session/{sessionId}/feedback',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/admin/activity',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/admin/activity/{queryId}',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/admin/ingest',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    httpApi.addRoutes({
      path: '/admin/chunks/documents',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    // Metadata-only chunk projection for the admin grid (paginated).
    httpApi.addRoutes({
      path: '/admin/chunks/{docId}/index',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });

    // Full chunk text, fetched a window at a time when a chunk is opened.
    httpApi.addRoutes({
      path: '/admin/chunks/{docId}/text',
      methods: [apigatewayv2.HttpMethod.GET],
      integration: lambdaIntegration,
      authorizer: authorizer,
    });
  }
}
