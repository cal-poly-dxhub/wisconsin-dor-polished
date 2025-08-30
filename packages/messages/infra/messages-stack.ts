import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';

export interface MessagesStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  sessionsTable: cdk.aws_dynamodb.ITable;
  websocketCallbackUrl: string;
}

export class MessagesStack extends cdk.NestedStack {
  public readonly classifierFunction: lambda.Function;
  public readonly classifierStateMachine: sfn.StateMachine;
  public readonly modelConfigTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: MessagesStackProps) {
    super(scope, id, props);

    // Model Configuration Table
    this.modelConfigTable = new dynamodb.Table(this, 'ModelConfigTable', {
      partitionKey: {
        name: 'id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Classifier Lambda
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
      layers: [props.stepFunctionTypesLayer, props.websocketUtilsLayer],
      description:
        'Classifier Lambda function that classifies user queries and returns a ClassifierResult',
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: {
        SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
        WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
      },
    });

    // Grant DynamoDB read permissions to classifier function
    props.sessionsTable.grantReadData(this.classifierFunction);

    // Retrieval Lambda (accepts RetrieveJob)
    const retrievalHandler = new lambda.Function(this, 'RetrievalFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code: lambda.Code.fromAsset('bundle/retrieval', {
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
      description:
        'Retrieval Lambda function that returns a RetrieveResult with jobs',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      environment: {
        WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
      },
    });

    // Resource Streaming Lambda (accepts StreamResourcesJob)
    const resourceStreamingHandler = new lambda.Function(
      this,
      'ResourceStreamingFunction',
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'main.handler',
        code: lambda.Code.fromAsset('bundle/resource_streaming', {
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
        description:
          'Resource streaming Lambda for sending resources via WebSocket',
        timeout: cdk.Duration.seconds(30),
        memorySize: 256,
        environment: {
          SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
          WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
        },
      }
    );

    // Grant DynamoDB read permissions to resource streaming function
    props.sessionsTable.grantReadData(resourceStreamingHandler);

    // Grant API Gateway Management API permissions for sending WebSocket messages
    resourceStreamingHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: ['*'],
      })
    );

    // Response Streaming Lambda (accepts GenerateResponseJob)
    const streamingHandler = new lambda.Function(
      this,
      'ResponseStreamingFunction',
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'main.handler',
        code: lambda.Code.fromAsset('bundle/streaming', {
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
        description:
          'Response streaming Lambda for streaming generated answers via WebSocket',
        timeout: cdk.Duration.seconds(60),
        memorySize: 512,
        environment: {
          SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
          WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
          MODEL_CONFIG_TABLE_NAME: this.modelConfigTable.tableName,
        },
      }
    );

    // Grant DynamoDB read permissions to response streaming function
    props.sessionsTable.grantReadData(streamingHandler);
    this.modelConfigTable.grantReadData(streamingHandler);

    // Grant API Gateway Management API permissions for sending WebSocket messages
    streamingHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:ManageConnections'],
        resources: ['*'],
      })
    );

    // Step Function Tasks
    const classifierTask = new tasks.LambdaInvoke(this, 'ClassifierTask', {
      lambdaFunction: this.classifierFunction,
      outputPath: '$.Payload',
    });

    const isFaq = sfn.Condition.stringEquals('$.query_class', 'faq');
    const isRag = sfn.Condition.stringEquals('$.query_class', 'rag');

    const retrievalTaskRag = new tasks.LambdaInvoke(this, 'RetrievalTaskRag', {
      lambdaFunction: retrievalHandler,
      outputPath: '$.Payload',
    });

    const streamingTaskFaq = new tasks.LambdaInvoke(this, 'StreamingTaskFaq', {
      lambdaFunction: streamingHandler,
      outputPath: '$.Payload',
    });

    const streamingTaskRag = new tasks.LambdaInvoke(this, 'StreamingTaskRag', {
      lambdaFunction: streamingHandler,
      outputPath: '$.Payload',
    });

    // Check if response streaming was successful
    const checkStreamingSuccessFaq = new sfn.Choice(
      this,
      'CheckStreamingSuccessFaq'
    )
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'StreamingFailedFaq', {
          error: 'Response streaming failed for FAQ',
          cause: 'The response streaming lambda returned successful=false',
        })
      )
      .otherwise(new sfn.Pass(this, 'StreamingSuccessFaq'));

    const checkStreamingSuccessRag = new sfn.Choice(
      this,
      'CheckStreamingSuccessRag'
    )
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'StreamingFailedRag', {
          error: 'Response streaming failed for RAG',
          cause: 'The response streaming lambda returned successful=false',
        })
      )
      .otherwise(new sfn.Pass(this, 'StreamingSuccessRag'));

    const resourceStreamingTaskFaq = new tasks.LambdaInvoke(
      this,
      'ResourceStreamingTaskFaq',
      {
        lambdaFunction: resourceStreamingHandler,
        outputPath: '$.Payload',
      }
    );

    const resourceStreamingTaskRag = new tasks.LambdaInvoke(
      this,
      'ResourceStreamingTaskRag',
      {
        lambdaFunction: resourceStreamingHandler,
        outputPath: '$.Payload',
      }
    );

    // Check if resource streaming was successful
    const checkResourceStreamingSuccessFaq = new sfn.Choice(
      this,
      'CheckResourceStreamingSuccessFaq'
    )
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'ResourceStreamingFailedFaq', {
          error: 'Resource streaming failed for FAQ',
          cause: 'The resource streaming lambda returned successful=false',
        })
      )
      .otherwise(new sfn.Pass(this, 'ResourceStreamingSuccessFaq'));

    const checkResourceStreamingSuccessRag = new sfn.Choice(
      this,
      'CheckResourceStreamingSuccessRag'
    )
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'ResourceStreamingFailedRag', {
          error: 'Resource streaming failed for RAG',
          cause: 'The resource streaming lambda returned successful=false',
        })
      )
      .otherwise(new sfn.Pass(this, 'ResourceStreamingSuccessRag'));

    // Pass state to select stream_documents_job for resource streaming (FAQ)
    const selectResourceStreamingJobFaq = new sfn.Pass(
      this,
      'SelectResourceStreamingJobFaq',
      {
        parameters: {
          'job.$': '$.stream_documents_job',
        },
        outputPath: '$.job',
      }
    );

    // Pass state to select generate_response_job for streaming (FAQ)
    const selectGenerateResponseJobFaq = new sfn.Pass(
      this,
      'SelectGenerateResponseJobFaq',
      {
        parameters: {
          'job.$': '$.generate_response_job',
        },
        outputPath: '$.job',
      }
    );

    const selectResourceStreamingJobRag = new sfn.Pass(
      this,
      'SelectResourceStreamingJobRag',
      {
        parameters: {
          'job.$': '$.stream_documents_job',
        },
        outputPath: '$.job',
      }
    );

    const selectGenerateResponseJobRag = new sfn.Pass(
      this,
      'SelectGenerateResponseJobRag',
      {
        parameters: {
          'job.$': '$.generate_response_job',
        },
        outputPath: '$.job',
      }
    );

    const selectRetrieveJob = new sfn.Pass(this, 'SelectRetrieveJob', {
      parameters: {
        'job.$': '$.retrieve_job',
      },
      outputPath: '$.job',
    });

    // Check all parallel execution results for success
    const checkAllParallelResults = new sfn.Choice(
      this,
      'CheckAllParallelResults'
    )
      .when(
        sfn.Condition.and(
          sfn.Condition.booleanEquals('$[0].successful', true),
          sfn.Condition.booleanEquals('$[1].successful', true)
        ),
        new sfn.Pass(this, 'AllParallelTasksSuccessful')
      )
      .otherwise(
        new sfn.Fail(this, 'ParallelTasksFailed', {
          error: 'One or more parallel tasks failed',
          cause:
            'At least one of the parallel execution branches returned successful=false',
        })
      );

    // If an FAQ was found, generate a response directly.
    // If RAG, retrieve documents before response generation.
    const faqBranch = new sfn.Parallel(this, 'ParallelResourceAndStreaming')
      .branch(
        selectResourceStreamingJobFaq
          .next(resourceStreamingTaskFaq)
          .next(checkResourceStreamingSuccessFaq)
      )
      .branch(
        selectGenerateResponseJobFaq
          .next(streamingTaskFaq)
          .next(checkStreamingSuccessFaq)
      )
      .next(checkAllParallelResults);

    const ragBranch = selectRetrieveJob.next(
      retrievalTaskRag.next(
        new sfn.Parallel(this, 'ParallelRagStreaming')
          .branch(
            selectResourceStreamingJobRag
              .next(resourceStreamingTaskRag)
              .next(checkResourceStreamingSuccessRag)
          )
          .branch(
            selectGenerateResponseJobRag
              .next(streamingTaskRag)
              .next(checkStreamingSuccessRag)
          )
      )
    );

    // Add error handling for when query_class is null (validation errors)
    const errorChoice = new sfn.Choice(this, 'BranchOnQueryClass')
      .when(isFaq, faqBranch)
      .when(isRag, ragBranch)
      .otherwise(
        new sfn.Fail(this, 'ClassificationFailed', {
          error: 'Classification failed - invalid query or processing error',
          cause:
            'The classifier returned null query_class, indicating a validation or processing error',
        })
      );

    const definition = classifierTask.next(errorChoice);

    this.classifierStateMachine = new sfn.StateMachine(
      this,
      'ChatStateMachine',
      {
        definition,
        stateMachineName: 'ChatStreamingStateMachine',
        timeout: cdk.Duration.minutes(5),
        tracingEnabled: true, // Enable X-Ray tracing for better observability
        logs: {
          destination: new cdk.aws_logs.LogGroup(this, 'StateMachineLogs', {
            logGroupName: `/aws/states/ChatStreamingStateMachine-${crypto.randomUUID()}`,
            retention: cdk.aws_logs.RetentionDays.ONE_WEEK,
          }),
          level: sfn.LogLevel.ALL,
          includeExecutionData: true,
        },
      }
    );

    const triggerMessageProcessing = new events.Rule(
      this,
      'TriggerMessageProcessing',
      {
        eventPattern: {
          source: ['wisconsin-dor.chat-api'],
          detailType: ['ChatMessageReceived'],
        },
      }
    );

    triggerMessageProcessing.addTarget(
      new targets.SfnStateMachine(this.classifierStateMachine, {
        input: events.RuleTargetInput.fromEventPath('$'),
      })
    );

    new cdk.CfnOutput(this, 'ClassifierFunctionArn', {
      value: this.classifierFunction.functionArn,
      description: 'ARN of the Classifier Lambda function',
    });

    new cdk.CfnOutput(this, 'RetrievalFunctionArn', {
      value: retrievalHandler.functionArn,
      description: 'ARN of the Retrieval Lambda function',
    });

    new cdk.CfnOutput(this, 'ResourceStreamingFunctionArn', {
      value: resourceStreamingHandler.functionArn,
      description: 'ARN of the Resource Streaming Lambda function',
    });

    new cdk.CfnOutput(this, 'StreamingFunctionArn', {
      value: streamingHandler.functionArn,
      description: 'ARN of the Response Streaming Lambda function',
    });

    new cdk.CfnOutput(this, 'ChatStateMachineArn', {
      value: this.classifierStateMachine.stateMachineArn,
      description: 'ARN of the classifier Step Functions state machine',
    });

    new cdk.CfnOutput(this, 'ModelConfigTableName', {
      value: this.modelConfigTable.tableName,
      description: 'Name of the Model Configuration DynamoDB table',
    });
  }
}
