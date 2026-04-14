import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as neptune from 'aws-cdk-lib/aws-neptunegraph';
import { Construct } from 'constructs';

export class GraphRAGStack extends cdk.NestedStack {
  public readonly rawBucketName: string;
  public readonly workBucketName: string;
  public readonly neptuneGraphId: string;
  public readonly neptuneGraphEndpoint: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const uid = cdk.Fn.select(
      0,
      cdk.Fn.split('-', cdk.Fn.select(2, cdk.Fn.split('/', this.stackId)))
    );

    const rawBucket = new s3.Bucket(this, 'WisDorRawDocs', {
      bucketName: cdk.Fn.join('-', ['wis-raw-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    const workBucket = new s3.Bucket(this, 'WisDorWorkBucket', {
      bucketName: cdk.Fn.join('-', ['wis-work-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // publicConnectivity: true so Lambdas can reach it via IAM auth
    // without VPC configuration (no existing VPC in this project)
    const graph = new neptune.CfnGraph(this, 'WisDorGraph', {
      graphName: 'wis-dor-graphrag',
      provisionedMemory: 32,
      vectorSearchConfiguration: {
        vectorSearchDimension: 1024,
      },
      publicConnectivity: true,
      replicaCount: 0,
      deletionProtection: false,
    });

    this.rawBucketName = rawBucket.bucketName;
    this.workBucketName = workBucket.bucketName;
    this.neptuneGraphId = graph.attrGraphId;
    this.neptuneGraphEndpoint = graph.attrEndpoint;

    new cdk.CfnOutput(this, 'RawBucketName', {
      value: rawBucket.bucketName,
      description: 'S3 bucket for raw source documents',
    });
    new cdk.CfnOutput(this, 'WorkBucketName', {
      value: workBucket.bucketName,
      description: 'S3 bucket for intermediate processing cache',
    });
    new cdk.CfnOutput(this, 'NeptuneGraphId', {
      value: graph.attrGraphId,
      description: 'Neptune Analytics Graph ID',
    });
    new cdk.CfnOutput(this, 'NeptuneGraphEndpoint', {
      value: graph.attrEndpoint,
      description: 'Neptune Analytics Graph Endpoint',
    });
  }
}
