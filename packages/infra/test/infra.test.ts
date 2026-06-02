import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { GraphRAGStack } from '../../graphrag/infra/graphrag-stack';

describe('GraphRAGStack', () => {
  function synth(): Template {
    const app = new cdk.App();
    const parent = new cdk.Stack(app, 'ParentStack');
    const stack = new GraphRAGStack(parent, 'GraphRAGStack');
    return Template.fromStack(stack);
  }

  test('FaqUrlTable exists with normalized_question hash key', () => {
    const template = synth();
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      KeySchema: [{ AttributeName: 'normalized_question', KeyType: 'HASH' }],
    });
  });

  test('FaqUrlTable uses on-demand (pay-per-request) billing', () => {
    const template = synth();
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      BillingMode: 'PAY_PER_REQUEST',
      KeySchema: [{ AttributeName: 'normalized_question', KeyType: 'HASH' }],
    });
  });
});
