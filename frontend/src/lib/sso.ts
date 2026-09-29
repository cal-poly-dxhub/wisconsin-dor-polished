import {
  CognitoAccessToken,
  CognitoIdToken,
  CognitoRefreshToken,
  CognitoUser,
  CognitoUserSession,
} from 'amazon-cognito-identity-js';
import { userPool } from './auth';

/**
 * Single sign-on through the Cognito domain (authorization code + PKCE).
 *
 * On return, the tokens are handed to amazon-cognito-identity-js via
 * `setSignInUserSession`, which stores them exactly where a password sign-in
 * would. Everything downstream (getCurrentSession, token refresh, the API and
 * WebSocket auth) then works unchanged. Configured by infra/stacks/sso.ts; with
 * no NEXT_PUBLIC_COGNITO_DOMAIN the feature is simply off.
 */

const VERIFIER_KEY = 'wisco:sso-verifier';
const STATE_KEY = 'wisco:sso-state';
/** Marks a session that came from SSO, so sign-out also ends the Cognito one. */
const METHOD_KEY = 'wisco:auth-method';

export interface SsoSettings {
  domain: string;
  clientId: string;
  /** Cognito name of DOR's provider; empty sends users to Cognito's own page. */
  provider: string;
  label: string;
}

export function getSsoSettings(): SsoSettings | null {
  const domain = process.env.NEXT_PUBLIC_COGNITO_DOMAIN;
  const clientId = process.env.NEXT_PUBLIC_USER_POOL_CLIENT_ID;
  if (!domain || !clientId) return null;
  return {
    domain: domain.replace(/\/$/, ''),
    clientId,
    provider: process.env.NEXT_PUBLIC_SSO_PROVIDER ?? '',
    label: process.env.NEXT_PUBLIC_SSO_LABEL || 'Sign in with single sign-on',
  };
}

function base64Url(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomString(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

export async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

export function redirectUri(origin: string): string {
  return `${origin}/auth/callback`;
}

export function buildAuthorizeUrl(
  settings: SsoSettings,
  origin: string,
  state: string,
  challenge: string,
): string {
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: settings.clientId,
    redirect_uri: redirectUri(origin),
    scope: 'openid email profile',
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  if (settings.provider) params.set('identity_provider', settings.provider);
  return `${settings.domain}/oauth2/authorize?${params}`;
}

export function buildLogoutUrl(settings: SsoSettings, origin: string): string {
  const params = new URLSearchParams({ client_id: settings.clientId, logout_uri: `${origin}/login` });
  return `${settings.domain}/logout?${params}`;
}

/** Leave for the identity provider. Does not return. */
export async function startSsoSignIn(): Promise<void> {
  const settings = getSsoSettings();
  if (!settings) throw new Error('Single sign-on is not configured');
  const verifier = randomString();
  const state = randomString();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  window.location.assign(
    buildAuthorizeUrl(settings, window.location.origin, state, await pkceChallenge(verifier)),
  );
}

/** Exchange the returned code for tokens and store them as the current session. */
export async function completeSsoSignIn(code: string, state: string): Promise<void> {
  const settings = getSsoSettings();
  if (!settings) throw new Error('Single sign-on is not configured');
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  const expected = sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  if (!verifier || !expected || state !== expected) {
    throw new Error('Sign-in could not be verified. Please try again.');
  }

  const res = await fetch(`${settings.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: settings.clientId,
      code,
      redirect_uri: redirectUri(window.location.origin),
      code_verifier: verifier,
    }),
  });
  if (!res.ok) throw new Error('Sign-in failed while exchanging the authorization code.');
  const tokens = (await res.json()) as { id_token: string; access_token: string; refresh_token: string };

  const session = new CognitoUserSession({
    IdToken: new CognitoIdToken({ IdToken: tokens.id_token }),
    AccessToken: new CognitoAccessToken({ AccessToken: tokens.access_token }),
    RefreshToken: new CognitoRefreshToken({ RefreshToken: tokens.refresh_token }),
  });
  const username = session.getAccessToken().decodePayload().username as string;
  new CognitoUser({ Username: username, Pool: userPool.current }).setSignInUserSession(session);
  localStorage.setItem(METHOD_KEY, 'sso');
}

/** Where to send the browser after the local session is cleared. */
export function signOutDestination(): string {
  const settings = getSsoSettings();
  let viaSso = false;
  try {
    viaSso = localStorage.getItem(METHOD_KEY) === 'sso';
    localStorage.removeItem(METHOD_KEY);
  } catch {
    // Storage unavailable: fall back to the local sign-out page.
  }
  return viaSso && settings ? buildLogoutUrl(settings, window.location.origin) : '/login';
}
