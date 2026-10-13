import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export class LambdaLayersStack extends cdk.NestedStack {
  public readonly chatContractsLayer: lambda.LayerVersion;
  public readonly websocketUtilsLayer: lambda.LayerVersion;

  constructor(scope: Construct, id: string, props?: cdk.NestedStackProps) {
    super(scope, id, props);

    this.chatContractsLayer = new lambda.LayerVersion(
      this,
      'ChatContractsLayer',
      {
        code: lambda.Code.fromAsset('bundle/chat_contracts', {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              'bash',
              '-c',
              [
                'pip install -r python/chat_contracts/requirements.txt -t /asset-output/python',
                'cp -r python/. /asset-output/python',
              ].join(' && '),
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
        description: 'Shared chat request/response Pydantic contracts',
        layerVersionName: 'wisconsin-bot-chat-contracts',
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
  }
}
