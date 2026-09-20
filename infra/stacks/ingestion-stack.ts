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
  /**
   * Neptune Analytics graph the `load` phase writes to by default, pinned by
   * the `neptuneGraphId` CDK context (infra/cdk.json). Scopes both the task
   * definition's NEPTUNE_GRAPH_ID and the task role's IAM.
   */
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
        // Scoped to the pinned graph only. Loading a NOT-yet-promoted graph
        // from Fargate (`run_fargate.sh load --graph-id g-new`) therefore needs
        // that graph's ARN added here for the duration of the blue/green load
        // — see the blue/green section of infra/README.md.
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
        // load.py falls back to this when `--graph-id` is omitted, so a routine
        // `run_fargate.sh load` lands on the pinned graph. `--graph-id` (which
        // run_fargate.sh forwards as the GRAPH_ID container override) still wins.
        NEPTUNE_GRAPH_ID: props.neptuneGraphId,
        MAX_WORKERS: '3',
        // TEXTRACT_STAGING_BUCKET is intentionally NOT set: the bucket it
        // used to name (a personal dev bucket) no longer exists, and the task
        // role no longer grants it. The Textract fallback in
        // tools/ingestion/chunking/pdfChunker.py must fail closed (skip
        // Textract) when this var is unset rather than fall back to a
        // hard-coded bucket name. Set it here, and re-grant the bucket above,
        // if Textract fallback is ever wanted again.
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
