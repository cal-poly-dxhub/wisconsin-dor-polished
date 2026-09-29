/** @bun */
import { describe, expect, test } from 'bun:test';
import { buildAuthorizeUrl, buildLogoutUrl, pkceChallenge, type SsoSettings } from '../sso';

const settings: SsoSettings = {
  domain: 'https://wisconsin-dor-chat.auth.us-east-1.amazoncognito.com',
  clientId: 'client123',
  provider: 'DOR',
  label: 'Sign in with DOR',
};

describe('sso', () => {
  test('PKCE challenge matches the RFC 7636 test vector', async () => {
    expect(await pkceChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe(
      'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
    );
  });

  test('authorize URL goes straight to DOR with PKCE and our callback', () => {
    const url = new URL(buildAuthorizeUrl(settings, 'https://app.example.gov', 'st', 'ch'));
    expect(url.origin + url.pathname).toBe(`${settings.domain}/oauth2/authorize`);
    const q = url.searchParams;
    expect(q.get('identity_provider')).toBe('DOR');
    expect(q.get('redirect_uri')).toBe('https://app.example.gov/auth/callback');
    expect(q.get('response_type')).toBe('code');
    expect(q.get('code_challenge_method')).toBe('S256');
    expect(q.get('state')).toBe('st');
  });

  test('with no provider the user lands on the Cognito page (pre-DOR testing)', () => {
    const url = new URL(buildAuthorizeUrl({ ...settings, provider: '' }, 'http://localhost:3000', 's', 'c'));
    expect(url.searchParams.has('identity_provider')).toBe(false);
  });

  test('logout URL returns to the login page', () => {
    const url = new URL(buildLogoutUrl(settings, 'https://app.example.gov'));
    expect(url.pathname).toBe('/logout');
    expect(url.searchParams.get('logout_uri')).toBe('https://app.example.gov/login');
    expect(url.searchParams.get('client_id')).toBe('client123');
  });
});
