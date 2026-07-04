import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as neptune from 'aws-cdk-lib/aws-neptunegraph';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { bedrock } from '@cdklabs/generative-ai-cdk-constructs';
import { Construct } from 'constructs';

export class GraphRAGStack extends cdk.NestedStack {
  public readonly rawBucketName: string;
  public readonly workBucketName: string;
  public readonly neptuneGraphId: string;
  public readonly neptuneGraphEndpoint: string;
  public readonly faqKnowledgeBaseId: string;
  public readonly faqBucketName: string;
  public readonly faqDataSourceId: string;
  public readonly faqUrlTable: dynamodb.Table;
  public readonly modelConfigTable: dynamodb.Table;

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

    const faqBucket = new s3.Bucket(this, 'WisDorFaqGraphRAG', {
      bucketName: cdk.Fn.join('-', ['wis-faq-bucket-graphrag', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    // Maps a normalized FAQ question to its public revenue.wi.gov URL.
    // Seeded from documents/faqs.json and refreshed by extract_faq_qa_pairs.py.
    const faqUrlTable = new dynamodb.Table(this, 'FaqUrlTable', {
      partitionKey: {
        name: 'normalized_question',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.faqUrlTable = faqUrlTable;

    this.modelConfigTable = new dynamodb.Table(this, 'ModelConfigTable', {
      partitionKey: {
        name: 'id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const faqKb = new bedrock.VectorKnowledgeBase(this, 'WisDorFaqKbGraphRAG', {
      name: 'wis-faq-graphrag',
      embeddingsModel: bedrock.BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
      instruction:
        'Use this knowledge base to answer frequently asked questions about Wisconsin DOR property assessment and taxation.',
    });

    const faqDataSource = new bedrock.S3DataSource(this, 'WisDorFaqDataSourceGraphRAG', {
      bucket: faqBucket,
      knowledgeBase: faqKb,
      dataSourceName: 'faq-docs-graphrag',
      chunkingStrategy: bedrock.ChunkingStrategy.NONE,
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
    this.faqKnowledgeBaseId = faqKb.knowledgeBaseId;
    this.faqBucketName = faqBucket.bucketName;
    this.faqDataSourceId = faqDataSource.dataSourceId;

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
    new cdk.CfnOutput(this, 'FaqKnowledgeBaseId', {
      value: faqKb.knowledgeBaseId,
      description: 'FAQ Bedrock Knowledge Base ID (GraphRAG)',
    });
    new cdk.CfnOutput(this, 'FaqBucketNameGraphRAG', {
      value: faqBucket.bucketName,
      description: 'S3 bucket for FAQ documents (GraphRAG)',
    });
    new cdk.CfnOutput(this, 'FaqDataSourceId', {
      value: faqDataSource.dataSourceId,
      description: 'FAQ Bedrock KB Data Source ID (GraphRAG)',
    });
    new cdk.CfnOutput(this, 'FaqUrlTableName', {
      value: faqUrlTable.tableName,
      description: 'DynamoDB table mapping normalized FAQ question -> source URL',
    });
    new cdk.CfnOutput(this, 'ModelConfigTableName', {
      value: this.modelConfigTable.tableName,
      description: 'DynamoDB table for externalized LLM prompt configs',
    });
  }
}
