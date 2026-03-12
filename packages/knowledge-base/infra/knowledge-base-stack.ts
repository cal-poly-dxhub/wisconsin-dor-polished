import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import * as bedrockL1 from 'aws-cdk-lib/aws-bedrock';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export class KnowledgeBaseStack extends cdk.NestedStack {
  public readonly knowledgeBaseId: string;
  public readonly documentsBucketName: string;
  public readonly dataSourceId: string;
  // Retained for backward-compatibility (existing data must not be lost).
  public readonly faqBucketName: string;
  public readonly ragBucketName: string;

  constructor(scope: Construct, id: string, props?: cdk.NestedStackProps) {
    super(scope, id, props);

    const uid = cdk.Fn.select(
      0,
      cdk.Fn.split('-', cdk.Fn.select(2, cdk.Fn.split('/', this.stackId)))
    );

    // ===========================================================
    // Legacy buckets — retained to preserve existing data.
    // Not connected to any knowledge base.
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

    this.faqBucketName = faqBucket.bucketName;
    this.ragBucketName = ragBucket.bucketName;

    // ===========================================================
    // Documents bucket — data source for the knowledge base.
    // ===========================================================
    const documentsBucket = new s3.Bucket(this, 'WisDorDocuments', {
      bucketName: cdk.Fn.join('-', ['wisconsin-documents', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    // ===========================================================
    // S3 Vectors: vector bucket + index
    // Stores the embedding vectors produced by Bedrock.
    // Dimensions and data type match amazon.titan-embed-text-v2:0.
    // ===========================================================
    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'WisDorVectorBucket', {
      vectorBucketName: cdk.Fn.join('-', ['wis-vector-bucket', uid]),
    });
    vectorBucket.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

    // A vector index with a complete unfilterable metadata list
    // Fields are filterable by default. Only doc_id and document_type should be filterable.
    // All other fields are specified as non-filterable below.
    const vectorIndexMetadataFiltered = new s3vectors.CfnIndex(this, 'WisDorVectorIndexMetadataFiltered', {
      vectorBucketArn: vectorBucket.attrVectorBucketArn,
      indexName: cdk.Fn.join('-', ['wis-vector-index-metadata-filtered', uid]),
      dataType: 'float32',
      dimension: 1024,
      distanceMetric: 'cosine',
      metadataConfiguration: {
        nonFilterableMetadataKeys: [
          'AMAZON_BEDROCK_TEXT',
          'AMAZON_BEDROCK_METADATA',
          'source',
          'source_url',
          'chunk_index',
          'total_chunks',
          'source_id',
        ],
      },
    });
    vectorIndexMetadataFiltered.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

    // ===========================================================
    // IAM service role for metadata-filtered knowledge base
    // ===========================================================
    const kbRoleFiltered = new iam.Role(this, 'KnowledgeBaseRoleFiltered', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': cdk.Aws.ACCOUNT_ID },
        },
      }),
      inlinePolicies: {
        S3VectorsAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                's3vectors:GetIndex',
                's3vectors:PutVectors',
                's3vectors:GetVectors',
                's3vectors:DeleteVectors',
                's3vectors:QueryVectors',
              ],
              resources: [vectorIndexMetadataFiltered.attrIndexArn],
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['bedrock:InvokeModel'],
              resources: [
                `arn:${cdk.Aws.PARTITION}:bedrock:${cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0`,
              ],
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['s3:GetObject', 's3:ListBucket'],
              resources: [
                documentsBucket.bucketArn,
                `${documentsBucket.bucketArn}/*`,
              ],
            }),
          ],
        }),
      },
    });

    // ===========================================================
    // Bedrock Knowledge Base with metadata-filtered index
    // ===========================================================
    const knowledgeBaseFiltered = new bedrockL1.CfnKnowledgeBase(this, 'WisDorKnowledgeBaseFiltered', {
      name: cdk.Fn.join('-', ['wis-kb-metadata-filtered', uid]),
      roleArn: kbRoleFiltered.roleArn,
      knowledgeBaseConfiguration: {
        type: 'VECTOR',
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn: `arn:${cdk.Aws.PARTITION}:bedrock:${cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0`,
          embeddingModelConfiguration: {
            bedrockEmbeddingModelConfiguration: {
              dimensions: 1024,
            },
          },
        },
      },
      storageConfiguration: {
        type: 'S3_VECTORS',
        s3VectorsConfiguration: {
          indexArn: vectorIndexMetadataFiltered.attrIndexArn,
        },
      },
    });

    // ===========================================================
    // S3 data source for metadata-filtered knowledge base
    // ===========================================================
    const dataSourceFiltered = new bedrockL1.CfnDataSource(this, 'WisDorDataSourceFiltered', {
      knowledgeBaseId: knowledgeBaseFiltered.attrKnowledgeBaseId,
      name: cdk.Fn.join('-', ['wis-docs-metadata-filtered', uid]),
      dataSourceConfiguration: {
        type: 'S3',
        s3Configuration: {
          bucketArn: documentsBucket.bucketArn,
        },
      },
    });

    this.knowledgeBaseId = knowledgeBaseFiltered.attrKnowledgeBaseId;
    this.documentsBucketName = documentsBucket.bucketName;
    this.dataSourceId = dataSourceFiltered.attrDataSourceId;

    // ===========================================================
    // Outputs
    // ===========================================================
    new cdk.CfnOutput(this, 'KnowledgeBaseId', {
      value: this.knowledgeBaseId,
      description: 'Bedrock Knowledge Base ID',
    });

    new cdk.CfnOutput(this, 'DocumentsBucketName', {
      value: documentsBucket.bucketName,
      description: 'S3 bucket for Wisconsin documents',
    });

    new cdk.CfnOutput(this, 'DataSourceId', {
      value: this.dataSourceId,
      description: 'Bedrock KB Data Source ID',
    });

    new cdk.CfnOutput(this, 'VectorBucketArn', {
      value: vectorBucket.attrVectorBucketArn,
      description: 'S3 Vectors bucket ARN',
    });

    new cdk.CfnOutput(this, 'FaqBucketName', {
      value: faqBucket.bucketName,
      description: 'Legacy FAQ S3 bucket (retained)',
    });

    new cdk.CfnOutput(this, 'RagBucketName', {
      value: ragBucket.bucketName,
      description: 'Legacy RAG S3 bucket (retained)',
    });
  }
}
