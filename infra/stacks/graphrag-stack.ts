import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { bedrock } from '@cdklabs/generative-ai-cdk-constructs';
import { Construct } from 'constructs';

/**
 * Buckets, the FAQ knowledge base, and the shared DynamoDB tables.
 *
 * The Neptune Analytics graph is deliberately NOT here. Graphs are created and
 * loaded out of band (Fargate `run_fargate.sh load`) and the deployment picks
 * one with the `neptuneGraphId` CDK context pinned in cdk.json — see
 * infra/README.md. Keeping a re-indexed corpus out of CloudFormation is what
 * makes a blue/green swap a context change instead of a stack replacement.
 */
export class GraphRAGStack extends cdk.NestedStack {
  public readonly rawBucketName: string;
  public readonly workBucketName: string;
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
      enforceSSL: true,
    });

    const workBucket = new s3.Bucket(this, 'WisDorWorkBucket', {
      bucketName: cdk.Fn.join('-', ['wis-work-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
    });

    const faqBucket = new s3.Bucket(this, 'WisDorFaqGraphRAG', {
      bucketName: cdk.Fn.join('-', ['wis-faq-bucket-graphrag', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
      enforceSSL: true,
    });

    // Maps a normalized FAQ question to its public revenue.wi.gov URL.
    // Written by tools/ingestion/ops/extract_faq_qa_pairs.py --faq-url-table
    // (and seed_faq_url_table.py from an FAQ export); read by the retrieval
    // Lambda to link each FAQ to its page.
    const faqUrlTable = new dynamodb.Table(this, 'FaqUrlTable', {
      partitionKey: {
        name: 'normalized_question',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.faqUrlTable = faqUrlTable;

    this.modelConfigTable = new dynamodb.Table(this, 'ModelConfigTable', {
      partitionKey: {
        name: 'id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
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

    this.rawBucketName = rawBucket.bucketName;
    this.workBucketName = workBucket.bucketName;
    this.faqKnowledgeBaseId = faqKb.knowledgeBaseId;
    this.faqBucketName = faqBucket.bucketName;
    this.faqDataSourceId = faqDataSource.dataSourceId;
  }
}
