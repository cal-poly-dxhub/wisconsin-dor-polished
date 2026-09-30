import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Template } from 'aws-cdk-lib/assertions';
import { GraphRAGStack } from '../stacks/graphrag-stack';
import { WisconsinBotStack } from '../stacks/stack';
import { parseSsoConfig, SsoResources } from '../stacks/sso';
import { AdminsGroup } from '../stacks/admins-group';

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

  test('owns no Neptune graph: the graph is pinned by context, not managed here', () => {
    synth().resourceCountIs('AWS::NeptuneGraph::Graph', 0);
  });
});

describe('WisconsinBotStack neptuneGraphId context', () => {
  test('synth fails loudly when the pin is missing instead of falling back', () => {
    const app = new cdk.App({ context: {} });
    expect(() => new WisconsinBotStack(app, 'NoPin')).toThrow(/neptuneGraphId/);
  });
});

describe('DOR single sign-on (sso context)', () => {
  const base = {
    enabled: true,
    domainPrefix: 'wisconsin-dor-chat',
    appUrls: ['https://app.example.gov', 'http://localhost:3000'],
  };

  function synth(config: unknown): Template {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'Auth');
    const userPool = new cognito.UserPool(stack, 'Pool');
    const parsed = parseSsoConfig(config);
    const sso = parsed ? new SsoResources(stack, 'Sso', { userPool, config: parsed }) : undefined;
    const client = new cognito.UserPoolClient(stack, 'Client', { userPool, ...sso?.clientOptions });
    if (sso?.provider) client.node.addDependency(sso.provider);
    return Template.fromStack(stack);
  }

  test('absent or disabled adds nothing', () => {
    expect(parseSsoConfig(undefined)).toBeUndefined();
    expect(parseSsoConfig({ ...base, enabled: false })).toBeUndefined();
    const t = synth(undefined);
    t.resourceCountIs('AWS::Cognito::UserPoolDomain', 0);
    t.resourceCountIs('AWS::Cognito::UserPoolIdentityProvider', 0);
  });

  test('enabled without a provider: domain + code flow, Cognito login only', () => {
    const t = synth(base);
    t.hasResourceProperties('AWS::Cognito::UserPoolDomain', { Domain: 'wisconsin-dor-chat' });
    t.resourceCountIs('AWS::Cognito::UserPoolIdentityProvider', 0);
    t.hasResourceProperties('AWS::Cognito::UserPoolClient', {
      AllowedOAuthFlows: ['code'],
      CallbackURLs: ['https://app.example.gov/auth/callback', 'http://localhost:3000/auth/callback'],
      LogoutURLs: ['https://app.example.gov/login', 'http://localhost:3000/login'],
      SupportedIdentityProviders: ['COGNITO'],
    });
  });

  test('SAML provider is registered and offered by the client', () => {
    const t = synth({
      ...base,
      provider: { type: 'saml', name: 'DOR', metadataUrl: 'https://idp.example.gov/metadata.xml' },
    });
    t.hasResourceProperties('AWS::Cognito::UserPoolIdentityProvider', {
      ProviderName: 'DOR',
      ProviderType: 'SAML',
      ProviderDetails: { MetadataURL: 'https://idp.example.gov/metadata.xml' },
    });
    t.hasResourceProperties('AWS::Cognito::UserPoolClient', {
      SupportedIdentityProviders: ['COGNITO', 'DOR'],
    });
  });

  test('accepts the context as a JSON string (-c sso=...)', () => {
    expect(parseSsoConfig(JSON.stringify(base))?.domainPrefix).toBe('wisconsin-dor-chat');
  });

  test('rejects incomplete config instead of deploying half of it', () => {
    expect(() => parseSsoConfig({ ...base, appUrls: ['https://app.example.gov/'] })).toThrow(/appUrls/);
    expect(() => parseSsoConfig({ ...base, provider: { type: 'saml', name: 'DOR' } })).toThrow(/metadataUrl/);
    expect(() => parseSsoConfig({ ...base, provider: { type: 'oidc', name: 'DOR', issuerUrl: 'https://x' } })).toThrow(/clientSecretName/);
    expect(() => parseSsoConfig({ ...base, domainPrefix: 'Bad_Prefix' })).toThrow(/domainPrefix/);
  });
});

describe('Admins group (security pass F4)', () => {
  test('is ensured idempotently and never deleted with the stack', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'Admins');
    const userPool = new cognito.UserPool(stack, 'Pool');
    new AdminsGroup(stack, 'AdminsGroup', { userPool });
    const t = Template.fromStack(stack);

    const [res] = Object.values(t.findResources('Custom::AWS')) as { Properties: Record<string, unknown> }[];
    const create = JSON.stringify(res.Properties.Create);
    expect(create).toContain('createGroup');
    expect(create).toContain('\\"GroupName\\":\\"Admins\\"');
    expect(create).toContain('GroupExistsException');
    expect(res.Properties.Delete).toBeUndefined();
    t.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: { Statement: [{ Action: 'cognito-idp:CreateGroup', Effect: 'Allow' }] },
    });
  });
});
