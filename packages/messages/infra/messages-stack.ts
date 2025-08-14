import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';

export class MessagesStack extends cdk.NestedStack {
  constructor(scope: Construct, id: string, props: cdk.NestedStackProps) {
    super(scope, id, props);
  }
}
