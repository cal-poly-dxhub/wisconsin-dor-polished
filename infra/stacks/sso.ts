import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

/**
 * Single sign-on through DOR's identity provider, federated into the Cognito
 * user pool. Driven by the optional `sso` CDK context (infra/README.md has the
 * runbook). Absent or `enabled: false` adds nothing, so synth is unchanged.
 *
 *   enabled, no provider  -> Cognito domain + authorization-code login on the
 *                            app client, so the redirect flow can be tested
 *                            end to end with an ordinary pool account.
 *   enabled, provider set -> also registers DOR's SAML or OIDC provider and
 *                            shows the "Sign in with DOR" button.
 *
 * Email/password sign-in keeps working in every mode.
 */
export interface SsoProviderConfig {
  type: 'saml' | 'oidc';
  /** Provider name inside Cognito, also sent as `identity_provider` on the redirect. */
  name: string;
  /** Button text on the login page. */
  label?: string;
  /** SAML: DOR's federation metadata URL. */
  metadataUrl?: string;
  /** OIDC: issuer URL and client id. The secret lives in Secrets Manager. */
  issuerUrl?: string;
  clientId?: string;
  clientSecretName?: string;
  /** The IdP attribute that carries the user's email (required by the pool). */
  emailAttribute?: string;
}

export interface SsoConfig {
  domainPrefix: string;
  /** Origins the app is served from; each gets /auth/callback and /login registered. */
  appUrls: string[];
  provider?: SsoProviderConfig;
}

/** Common Entra ID / ADFS email claim, the SAML default when none is given. */
const SAML_EMAIL_CLAIM = 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress';

export function parseSsoConfig(raw: unknown): SsoConfig | undefined {
  if (raw === undefined || raw === null) return undefined;
  const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
  if (typeof value !== 'object' || value === null) {
    throw new Error('CDK context "sso" must be an object');
  }
  const cfg = value as Record<string, unknown>;
  if (!cfg.enabled) return undefined;

  const domainPrefix = cfg.domainPrefix;
  if (typeof domainPrefix !== 'string' || !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(domainPrefix)) {
    throw new Error('sso.domainPrefix must be lowercase letters, digits and hyphens');
  }
  const appUrls = cfg.appUrls;
  if (!Array.isArray(appUrls) || appUrls.length === 0 || !appUrls.every(u => typeof u === 'string' && /^https?:\/\/[^/]+$/.test(u))) {
    throw new Error('sso.appUrls must be a non-empty list of origins like "https://example.com" (no trailing slash)');
  }

  let provider: SsoProviderConfig | undefined;
  if (cfg.provider) {
    provider = cfg.provider as SsoProviderConfig;
    if (!provider.name || !/^[\w.-]{1,32}$/.test(provider.name)) {
      throw new Error('sso.provider.name is required (letters, digits, "_", ".", "-")');
    }
    if (provider.type === 'saml') {
      if (!provider.metadataUrl) throw new Error('sso.provider.metadataUrl is required for SAML');
    } else if (provider.type === 'oidc') {
      if (!provider.issuerUrl || !provider.clientId || !provider.clientSecretName) {
        throw new Error('sso.provider needs issuerUrl, clientId and clientSecretName for OIDC');
      }
    } else {
      throw new Error('sso.provider.type must be "saml" or "oidc"');
    }
  }

  return { domainPrefix, appUrls: appUrls as string[], provider };
}

export interface SsoResourcesProps {
  userPool: cognito.UserPool;
  config: SsoConfig;
}

export class SsoResources extends Construct {
  /** Base URL of the Cognito domain, e.g. https://prefix.auth.us-east-1.amazoncognito.com */
  public readonly domainUrl: string;
  public readonly provider?: cognito.UserPoolIdentityProviderSaml | cognito.UserPoolIdentityProviderOidc;
  /** Spread into the app client's props. */
  public readonly clientOptions: Pick<cognito.UserPoolClientProps, 'oAuth' | 'supportedIdentityProviders'>;
  /** NEXT_PUBLIC_* values the frontend needs for the redirect flow. */
  public readonly frontendEnv: Record<string, string>;

  constructor(scope: Construct, id: string, props: SsoResourcesProps) {
    super(scope, id);
    const { userPool, config } = props;

    const domain = userPool.addDomain('Domain', {
      cognitoDomain: { domainPrefix: config.domainPrefix },
    });
    this.domainUrl = domain.baseUrl();

    const p = config.provider;
    if (p?.type === 'saml') {
      this.provider = new cognito.UserPoolIdentityProviderSaml(this, 'Provider', {
        userPool,
        name: p.name,
        metadata: cognito.UserPoolIdentityProviderSamlMetadata.url(p.metadataUrl!),
        attributeMapping: {
          email: cognito.ProviderAttribute.other(p.emailAttribute ?? SAML_EMAIL_CLAIM),
        },
      });
    } else if (p?.type === 'oidc') {
      this.provider = new cognito.UserPoolIdentityProviderOidc(this, 'Provider', {
        userPool,
        name: p.name,
        issuerUrl: p.issuerUrl!,
        clientId: p.clientId!,
        // A {{resolve:secretsmanager:...}} reference, resolved by CloudFormation
        // at deploy time; the secret itself never enters the template.
        clientSecret: cdk.SecretValue.secretsManager(p.clientSecretName!).unsafeUnwrap(),
        scopes: ['openid', 'email', 'profile'],
        attributeMapping: {
          email: cognito.ProviderAttribute.other(p.emailAttribute ?? 'email'),
        },
      });
    }

    this.clientOptions = {
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: config.appUrls.map(u => `${u}/auth/callback`),
        logoutUrls: config.appUrls.map(u => `${u}/login`),
      },
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.COGNITO,
        ...(p ? [cognito.UserPoolClientIdentityProvider.custom(p.name)] : []),
      ],
    };

    this.frontendEnv = {
      NEXT_PUBLIC_COGNITO_DOMAIN: this.domainUrl,
      NEXT_PUBLIC_SSO_PROVIDER: p?.name ?? '',
      NEXT_PUBLIC_SSO_LABEL: p?.label ?? (p ? `Sign in with ${p.name}` : 'Sign in with single sign-on'),
    };

    // What DOR's identity team registers on their side.
    new cdk.CfnOutput(this, 'CognitoDomainUrl', { value: this.domainUrl });
    new cdk.CfnOutput(this, 'SamlAcsUrl', { value: `${this.domainUrl}/saml2/idpresponse` });
    new cdk.CfnOutput(this, 'SamlEntityId', { value: `urn:amazon:cognito:sp:${userPool.userPoolId}` });
    new cdk.CfnOutput(this, 'OidcRedirectUri', { value: `${this.domainUrl}/oauth2/idpresponse` });
  }
}
