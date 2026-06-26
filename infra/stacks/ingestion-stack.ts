import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

interface IngestionStackProps extends cdk.NestedStackProps {
  rawBucketName: string;
  workBucketName: string;
  neptuneGraphId: string;
}

export class IngestionStack extends cdk.NestedStack {
  public readonly cluster: ecs.Cluster;
  public readonly taskDefinition: ecs.FargateTaskDefinition;
  public readonly subnetIds: string;
  public readonly securityGroupId: string;

  constructor(scope: Construct, id: string, props: IngestionStackProps) {
    super(scope, id, props);

    const vpc = new ec2.Vpc(this, 'IngestionVpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    const cluster = new ecs.Cluster(this, 'IngestionCluster', {
      vpc,
      clusterName: 'wis-dor-ingestion',
    });
    this.cluster = cluster;

    const repository = new ecr.Repository(this, 'IngestionRepo', {
      repositoryName: 'wis-dor-ingestion',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      lifecycleRules: [
        {
          maxImageCount: 5,
          description: 'Keep only 5 most recent images',
        },
      ],
    });

    const logGroup = new logs.LogGroup(this, 'IngestionLogGroup', {
      logGroupName: '/ecs/wis-dor-ingestion',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const taskRole = new iam.Role(this, 'IngestionTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'Role for ingestion Fargate task with access to S3, Bedrock, Neptune, Textract',
    });

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          's3:GetObject',
          's3:PutObject',
          's3:ListBucket',
          's3:DeleteObject',
        ],
        resources: [
          `arn:aws:s3:::${props.rawBucketName}`,
          `arn:aws:s3:::${props.rawBucketName}/*`,
          `arn:aws:s3:::${props.workBucketName}`,
          `arn:aws:s3:::${props.workBucketName}/*`,
        ],
      })
    );

    // Textract staging bucket (legacy name from initial development)
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          's3:GetObject',
          's3:PutObject',
          's3:ListBucket',
          's3:DeleteObject',
        ],
        resources: [
          'arn:aws:s3:::textract-chunk-result-dhgoel',
          'arn:aws:s3:::textract-chunk-result-dhgoel/*',
        ],
      })
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: ['*'],
      })
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'neptune-graph:ExecuteQuery',
          'neptune-graph:ReadDataViaQuery',
          'neptune-graph:WriteDataViaQuery',
          'neptune-graph:DeleteDataViaQuery',
          'neptune-graph:GetGraph',
        ],
        resources: [
          `arn:aws:neptune-graph:${this.region}:${this.account}:graph/${props.neptuneGraphId}`,
        ],
      })
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'textract:AnalyzeDocument',
          'textract:DetectDocumentText',
          'textract:StartDocumentAnalysis',
          'textract:GetDocumentAnalysis',
          'textract:StartDocumentTextDetection',
          'textract:GetDocumentTextDetection',
        ],
        resources: ['*'],
      })
    );

    const taskDefinition = new ecs.FargateTaskDefinition(
      this,
      'IngestionTaskDef',
      {
        cpu: 2048,
        memoryLimitMiB: 8192,
        taskRole,
      }
    );
    this.taskDefinition = taskDefinition;

    taskDefinition.addContainer('ingestion', {
      image: ecs.ContainerImage.fromEcrRepository(repository, 'latest'),
      logging: ecs.LogDrivers.awsLogs({
        logGroup,
        streamPrefix: 'ingestion',
      }),
      environment: {
        AWS_REGION: 'us-east-1',
        RAW_BUCKET: props.rawBucketName,
        WORK_BUCKET: props.workBucketName,
        GRAPH_ID: props.neptuneGraphId,
        MAX_WORKERS: '3',
        TEXTRACT_STAGING_BUCKET: 'textract-chunk-result-dhgoel',
      },
    });

    const securityGroup = new ec2.SecurityGroup(this, 'IngestionSg', {
      vpc,
      description: 'Security group for ingestion Fargate tasks',
      allowAllOutbound: true,
    });

    this.subnetIds = vpc.publicSubnets.map((s) => s.subnetId).join(',');
    this.securityGroupId = securityGroup.securityGroupId;

    // Outputs for the wrapper scripts
    new cdk.CfnOutput(this, 'ClusterArn', {
      value: cluster.clusterArn,
      description: 'Ingestion ECS Cluster ARN',
    });

    new cdk.CfnOutput(this, 'TaskDefinitionArn', {
      value: taskDefinition.taskDefinitionArn,
      description: 'Ingestion Fargate Task Definition ARN',
    });

    new cdk.CfnOutput(this, 'SubnetIds', {
      value: vpc.publicSubnets.map((s) => s.subnetId).join(','),
      description: 'Public subnet IDs for Fargate tasks',
    });

    new cdk.CfnOutput(this, 'SecurityGroupId', {
      value: securityGroup.securityGroupId,
      description: 'Security group ID for Fargate tasks',
    });

    new cdk.CfnOutput(this, 'EcrRepositoryUri', {
      value: repository.repositoryUri,
      description: 'ECR repository URI for ingestion images',
    });
  }
}
