import * as cdk from 'aws-cdk-lib';
import * as amplify from 'aws-cdk-lib/aws-amplify';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface WebappStackProps extends cdk.StackProps {
  apiUrl: string;
  websocketUrl: string;
  userPoolId: string;
  userPoolClientId: string;
  region: string;
}

export class WebappStack extends cdk.NestedStack {
  public readonly amplifyApp: amplify.CfnApp;

  constructor(scope: Construct, id: string, props: WebappStackProps) {
    super(scope, id, props);

    const amplifyRole = new iam.Role(this, 'AmplifyRole', {
      assumedBy: new iam.ServicePrincipal('amplify.amazonaws.com'),
      description: 'Role for Amplify to deploy the Wisconsin webapp',
    });

    this.amplifyApp = new amplify.CfnApp(this, 'WisconsinWebApp', {
      name: 'wisconsin-bot-webapp',
      description: 'Wisconsin Bot Next.js Web Application',
      repository: '',
      platform: 'WEB_COMPUTE',
      iamServiceRole: amplifyRole.roleArn,
      environmentVariables: [
        {
          name: 'NEXT_PUBLIC_API_BASE_URL',
          value: props.apiUrl,
        },
        {
          name: 'NEXT_PUBLIC_WEBSOCKET_URL',
          value: props.websocketUrl,
        },
        {
          name: 'NEXT_PUBLIC_USER_POOL_ID',
          value: props.userPoolId,
        },
        {
          name: 'NEXT_PUBLIC_USER_POOL_CLIENT_ID',
          value: props.userPoolClientId,
        },
        {
          name: '_LIVE_UPDATES',
          value: JSON.stringify([
            {
              pkg: 'bun',
              type: 'internal',
              version: '1.2.19',
            },
          ]),
        },
      ],
      buildSpec: `version: 1
frontend:
  phases:
    preBuild:
      commands:
        - bun install --frozen-lockfile
    build:
      commands:
        - bun run build
  artifacts:
    baseDirectory: .next
    files:
      - '**/*'
  cache:
    paths:
      - node_modules/**/*
      - .next/cache/**/*`,
      customRules: [
        {
          source: '/<*>',
          target: '/index.html',
          status: '404-200',
        },
      ],
    });

    new cdk.CfnOutput(this, 'AmplifyAppId', {
      value: this.amplifyApp.attrAppId,
      description: 'Amplify App ID',
      exportName: 'WisconsinBot-AmplifyAppId',
    });

    new cdk.CfnOutput(this, 'AmplifyAppUrl', {
      value: `https://main.${this.amplifyApp.attrDefaultDomain}`,
      description: 'Default Amplify App URL',
      exportName: 'WisconsinBot-AmplifyAppUrl',
    });

    new cdk.CfnOutput(this, 'AmplifyConsoleUrl', {
      value: `https://console.aws.amazon.com/amplify/home?region=${props.region}#/${this.amplifyApp.attrAppId}`,
      description: 'Amplify Console URL for GitHub connection',
    });
  }
}
