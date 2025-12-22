import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as route53 from 'aws-cdk-lib/aws-route53';
import { Nextjs } from 'cdk-nextjs-standalone';
import { Construct } from 'constructs';

export interface WebAppStackProps extends cdk.StackProps {
  userPool: cognito.UserPool;
  userPoolClient: cognito.UserPoolClient;
  httpApiUrl: string;
  websocketApiUrl: string;
  domainName?: string;
  hostedZoneName?: string;
  hostedZoneId?: string;
}

export class WebAppStack extends cdk.NestedStack {
  public readonly distributionUrl: string;

  constructor(scope: Construct, id: string, props: WebAppStackProps) {
    super(scope, id, props);

    let certificate: acm.ICertificate | undefined;
    let hostedZone: route53.IHostedZone | undefined;

    if (props.domainName && props.hostedZoneName && props.hostedZoneId) {
      hostedZone = route53.HostedZone.fromHostedZoneAttributes(
        this,
        'HostedZone',
        {
          hostedZoneId: props.hostedZoneId,
          zoneName: props.hostedZoneName,
        }
      );

      certificate = new acm.Certificate(this, 'Certificate', {
        domainName: props.domainName,
        validation: acm.CertificateValidation.fromDns(hostedZone),
      });
    }

    const buildCommand = `cd ../../ && bun install && cd packages/webapp && bunx open-next build && find .open-next -path '*/node_modules/.bin' -type d -exec rm -rf {} + 2>/dev/null || true`;
    const excludeBinSymlinks = ['**/node_modules/.bin/**'];
    const nextjs = new Nextjs(this, 'NextjsApp', {
      nextjsPath: '../../packages/webapp',
      buildCommand,
      environment: {
        NEXT_PUBLIC_USER_POOL_ID: props.userPool.userPoolId,
        NEXT_PUBLIC_USER_POOL_CLIENT_ID: props.userPoolClient.userPoolClientId,
        NEXT_PUBLIC_API_BASE_URL: props.httpApiUrl,
        NEXT_PUBLIC_WEBSOCKET_URL: props.websocketApiUrl,
      },
      overrides: {
        nextjsServer: {
          sourceCodeAssetProps: { exclude: excludeBinSymlinks },
          destinationCodeAssetProps: { exclude: excludeBinSymlinks },
        },
        nextjsStaticAssets: {
          assetProps: { exclude: excludeBinSymlinks },
        },
      },
      ...(props.domainName &&
        certificate &&
        hostedZone && {
          domainProps: {
            domainName: props.domainName,
            certificate,
            hostedZone,
          },
        }),
    });

    this.distributionUrl = props.domainName
      ? `https://${props.domainName}`
      : `https://${nextjs.distribution.distributionDomain}`;

    new cdk.CfnOutput(this, 'CloudFrontDistributionDomain', {
      value: nextjs.distribution.distributionDomain,
      description: 'CloudFront distribution domain for the web app',
      exportName: 'WisconsinBot-CloudFrontDomain',
    });

    new cdk.CfnOutput(this, 'WebAppUrl', {
      value: this.distributionUrl,
      description: 'URL of the web application',
    });

    if (props.domainName) {
      new cdk.CfnOutput(this, 'CustomDomain', {
        value: props.domainName,
        description: 'Custom domain name for the web application',
      });
    }
  }
}
