import {
  SignJWT,
  exportJWK,
  generateKeyPair,
  type JWK,
  type KeyLike,
} from 'jose';
import { randomUUID } from 'node:crypto';

/**
 * Mints access tokens shaped exactly like GoTrue's, signed for real.
 *
 * Real signatures matter: a test that stubs the verifier proves the guard calls
 * *something*, not that the something rejects a forged token. Everything here
 * goes through the same `jwtVerify` the server uses.
 */

/** Long enough to satisfy the 32-character minimum the config enforces. */
export const TEST_JWT_SECRET =
  'test-secret-with-at-least-32-characters-of-length';
export const TEST_ISSUER = 'http://127.0.0.1:54321/auth/v1';
export const TEST_AUDIENCE = 'authenticated';

export interface TokenOptions {
  sub?: string;
  role?: string;
  email?: string;
  issuer?: string;
  audience?: string;
  isAnonymous?: boolean;
  /** Seconds from now. Negative mints an already-expired token. */
  expiresInSeconds?: number;
  /** Omit `exp` entirely. */
  omitExp?: boolean;
  /** Omit `role` entirely — `role` defaults to "authenticated" otherwise. */
  omitRole?: boolean;
  /** Sign with this instead of the shared secret (alg-confusion tests). */
  secret?: Uint8Array;
  algorithm?: string;
}

export function newUserId(): string {
  return randomUUID();
}

/** HS256 token signed with the shared secret, unless overridden. */
export async function signAccessToken(
  options: TokenOptions = {},
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const expiresIn = options.expiresInSeconds ?? 3600;

  const payload: Record<string, unknown> = { session_id: randomUUID() };
  if (!options.omitRole) payload.role = options.role ?? 'authenticated';
  if (options.email !== undefined) payload.email = options.email;
  if (options.isAnonymous !== undefined)
    payload.is_anonymous = options.isAnonymous;

  let jwt = new SignJWT(payload)
    .setProtectedHeader({ alg: options.algorithm ?? 'HS256' })
    .setIssuedAt(now)
    .setIssuer(options.issuer ?? TEST_ISSUER)
    .setAudience(options.audience ?? TEST_AUDIENCE);

  // `undefined` sub means "omit", which is how GoTrue's anon key looks.
  if (options.sub !== undefined) jwt = jwt.setSubject(options.sub);
  if (!options.omitExp) jwt = jwt.setExpirationTime(now + expiresIn);

  const secret = options.secret ?? new TextEncoder().encode(TEST_JWT_SECRET);
  return jwt.sign(secret);
}

export interface AsymmetricKeys {
  privateKey: KeyLike;
  publicJwk: JWK;
  jwks: { keys: JWK[] };
  kid: string;
}

/**
 * An ES256 keypair, matching what `supabase gen signing-key` produces and what
 * a project using asymmetric signing keys publishes at its JWKS endpoint.
 */
export async function generateSigningKeys(): Promise<AsymmetricKeys> {
  const { privateKey, publicKey } = await generateKeyPair('ES256', {
    extractable: true,
  });
  const kid = randomUUID();
  const publicJwk: JWK = {
    ...(await exportJWK(publicKey)),
    kid,
    alg: 'ES256',
    use: 'sig',
  };
  return { privateKey, publicJwk, jwks: { keys: [publicJwk] }, kid };
}

/** ES256 token signed with the private half of `generateSigningKeys`. */
export async function signAsymmetricToken(
  keys: AsymmetricKeys,
  options: TokenOptions = {},
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  let jwt = new SignJWT({
    role: options.role ?? 'authenticated',
    session_id: randomUUID(),
  })
    .setProtectedHeader({ alg: 'ES256', kid: keys.kid })
    .setIssuedAt(now)
    .setIssuer(options.issuer ?? TEST_ISSUER)
    .setAudience(options.audience ?? TEST_AUDIENCE)
    .setExpirationTime(now + (options.expiresInSeconds ?? 3600));
  if (options.sub !== undefined) jwt = jwt.setSubject(options.sub);
  return jwt.sign(keys.privateKey);
}
