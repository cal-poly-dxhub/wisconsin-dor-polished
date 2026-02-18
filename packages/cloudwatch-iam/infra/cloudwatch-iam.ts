import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';

export interface CloudWatchIamProps extends cdk.StackProps {
  resetCloudWatchIamRole: boolean;
}

export class CloudWatchIam extends cdk.NestedStack {
  constructor(scope: Construct, id: string, props?: CloudWatchIamProps) {
    super(scope, id, props);

    const uid = cdk.Fn.select(
      0,
      cdk.Fn.split('-', cdk.Fn.select(2, cdk.Fn.split('/', this.stackId)))
    );

    const apiGwLogsRole = new iam.Role(this, 'ApiGatewayLogsRole', {
      roleName: cdk.Fn.join('-', ['ApiGatewayCloudWatchLogsRole', uid]),
      assumedBy: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      description: 'Role used by API Gateway to push logs to CloudWatch Logs',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          'service-role/AmazonAPIGatewayPushToCloudWatchLogs'
        ),
      ],
    });

    if (props?.resetCloudWatchIamRole) {
      new apigateway.CfnAccount(this, 'ApiGatewayAccountLogging', {
        cloudWatchRoleArn: apiGwLogsRole.roleArn,
      });
    }
  }
}
