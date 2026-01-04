import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { bedrock } from '@cdklabs/generative-ai-cdk-constructs';
import { Construct } from 'constructs';

export class KnowledgeBaseStack extends cdk.NestedStack {
  public readonly faqKnowledgeBase: bedrock.VectorKnowledgeBase;
  public readonly ragKnowledgeBase: bedrock.VectorKnowledgeBase;
  public readonly faqBucketName: string;
  public readonly ragBucketName: string;
  public readonly faqDataSourceId: string;
  public readonly ragDataSourceId: string;


  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const uid = cdk.Fn.select(
      0,
      cdk.Fn.split('-', cdk.Fn.select(2, cdk.Fn.split('/', this.stackId)))
    );

    // ===========================================================
    // Buckets for FAQ + RAG documents
    // ===========================================================
    const faqBucket = new s3.Bucket(this, 'WisDorDocsFaq', {
      bucketName: cdk.Fn.join('-', ['wis-faq-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    const ragBucket = new s3.Bucket(this, 'WisDorDocsRag', {
      bucketName: cdk.Fn.join('-', ['wis-rag-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    // ===========================================================
    // FAQ Knowledge Base (auto OpenSearch Serverless)
    // ===========================================================
    const faqKb = new bedrock.VectorKnowledgeBase(this, 'WisDorKbFaq', {
      name: 'wis-faq-base',
      embeddingsModel: bedrock.BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
      instruction:
        'Use this knowledge base to answer frequently asked questions about Wisconsin DOR property manuals.',
    });

    // S3 data source for FAQ KB
    const faqDataSource = new bedrock.S3DataSource(this, 'WisDorDataSourceFaq', {
      bucket: faqBucket,
      knowledgeBase: faqKb,
      dataSourceName: 'faq-docs',
      chunkingStrategy: bedrock.ChunkingStrategy.NONE,
    });

    // ===========================================================
    // RAG Knowledge Base (auto OpenSearch Serverless)
    // ===========================================================
    const ragKb = new bedrock.VectorKnowledgeBase(this, 'WisDorKbRag', {
      name: 'wis-rag-base',
      embeddingsModel: bedrock.BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
      instruction:
        'Use this knowledge base to retrieve and summarize details from Wisconsin DOR manuals, laws, and publications.',
    });

    // S3 data source for RAG KB
    const ragDataSource = new bedrock.S3DataSource(this, 'WisDorDataSourceRag', {
      bucket: ragBucket,
      knowledgeBase: ragKb,
      dataSourceName: 'rag-docs',
      chunkingStrategy: bedrock.ChunkingStrategy.NONE,
    });

    this.faqKnowledgeBase = faqKb;
    this.ragKnowledgeBase = ragKb;
    this.faqBucketName = faqBucket.bucketName;
    this.ragBucketName = ragBucket.bucketName;
    this.faqDataSourceId = faqDataSource.dataSourceId;
    this.ragDataSourceId = ragDataSource.dataSourceId;

    // ===========================================================
    // Outputs for reference
    // ===========================================================
    new cdk.CfnOutput(this, 'FaqKnowledgeBaseId', {
      value: this.faqKnowledgeBase.knowledgeBaseId,
      description: 'FAQ Bedrock Knowledge Base ID',
    });

    new cdk.CfnOutput(this, 'RagKnowledgeBaseId', {
      value: this.ragKnowledgeBase.knowledgeBaseId,
      description: 'RAG Bedrock Knowledge Base ID',
    });

    new cdk.CfnOutput(this, 'FaqBucketName', {
      value: faqBucket.bucketName,
      description: 'S3 bucket for FAQ documents',
    });

    new cdk.CfnOutput(this, 'RagBucketName', {
      value: ragBucket.bucketName,
      description: 'S3 bucket for RAG documents',
    });

    new cdk.CfnOutput(this, 'FaqDataSourceId', {
      value: this.faqDataSourceId,
      description: 'FAQ Bedrock KB Data Source ID',
    });
    
    new cdk.CfnOutput(this, 'RagDataSourceId', {
      value: this.ragDataSourceId,
      description: 'RAG Bedrock KB Data Source ID',
    });
    
  }
}
