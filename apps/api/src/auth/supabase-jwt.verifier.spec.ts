import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { ApiException } from '../common/api-error';
import type { ApiConfig, AuthConfig } from '../config';
import {
  TEST_AUDIENCE,
  TEST_ISSUER,
  TEST_JWT_SECRET,
  generateSigningKeys,
  newUserId,
  signAccessToken,
  signAsymmetricToken,
  type AsymmetricKeys,
} from '../../test/harness/tokens';
import { SupabaseJwtVerifier } from './supabase-jwt.verifier';

function configWith(auth: Partial<AuthConfig>): ApiConfig {
  return {
    port: 0,
    host: '127.0.0.1',
    solverUrl: 'http://localhost:8000',
    databaseUrl: 'postgresql://unused',
    docsEnabled: false,
    auth: {
      jwksUrl: null,
      jwtSecret: TEST_JWT_SECRET,
      issuer: TEST_ISSUER,
      audience: TEST_AUDIENCE,
      allowAnonymous: false,
      clockToleranceSec: 5,
      ...auth,
    },
  };
}

/** Asserts the call rejected, and with the code we expect rather than any error. */
async function expectRejection(
  promise: Promise<unknown>,
  code: 'unauthenticated' | 'forbidden',
): Promise<void> {
  await expect(promise).rejects.toBeInstanceOf(ApiException);
  await expect(promise).rejects.toMatchObject({ code });
}

describe('SupabaseJwtVerifier — shared secret (HS256)', () => {
  const verifier = new SupabaseJwtVerifier(configWith({}));

  it('reports its mode', () => {
    expect(verifier.mode).toBe('shared-secret');
  });

  it('accepts a well-formed user token and returns only verified claims', async () => {
    const sub = newUserId();
    const token = await signAccessToken({ sub, email: 'devon@example.com' });

    const user = await verifier.verify(token);

    expect(user.userId).toBe(sub);
    expect(user.email).toBe('devon@example.com');
    expect(user.isAnonymous).toBe(false);
    expect(user.sessionId).toEqual(expect.any(String));
  });

  it('rejects a token signed with a different secret', async () => {
    const token = await signAccessToken({
      sub: newUserId(),
      secret: new TextEncoder().encode(
        'a-completely-different-secret-32-chars',
      ),
    });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects an expired token', async () => {
    // Beyond the 5s clock tolerance.
    const token = await signAccessToken({
      sub: newUserId(),
      expiresInSeconds: -60,
    });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects a token with no expiry', async () => {
    const token = await signAccessToken({ sub: newUserId(), omitExp: true });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects a token from another issuer', async () => {
    const token = await signAccessToken({
      sub: newUserId(),
      issuer: 'https://someone-elses-project.supabase.co/auth/v1',
    });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects a token for another audience', async () => {
    const token = await signAccessToken({
      sub: newUserId(),
      audience: 'some-other-api',
    });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects garbage that is not a JWT at all', async () => {
    await expectRejection(verifier.verify('not-a-token'), 'unauthenticated');
    await expectRejection(verifier.verify(''), 'unauthenticated');
  });

  /**
   * The publishable ("anon") key is a real JWT signed with the same secret and
   * is printed in the web app's bundle. Only the `role` claim distinguishes it
   * from a user token. If this test ever passes a user through, anyone who has
   * viewed the site source can call this API as an authenticated principal.
   */
  it('rejects the publishable anon key', async () => {
    const anonKey = await signAccessToken({
      role: 'anon',
      audience: TEST_AUDIENCE,
    });
    await expectRejection(verifier.verify(anonKey), 'unauthenticated');
  });

  /** Same signature, far worse blast radius: service_role is the god key. */
  it('rejects a service_role key', async () => {
    const serviceKey = await signAccessToken({
      role: 'service_role',
      sub: newUserId(),
    });
    await expectRejection(verifier.verify(serviceKey), 'unauthenticated');
  });

  it('rejects a token with no role claim at all', async () => {
    const token = await signAccessToken({ sub: newUserId(), omitRole: true });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects a subject that is not a UUID', async () => {
    for (const sub of ['not-a-uuid', '12345', 'admin', '../../etc/passwd']) {
      await expectRejection(
        verifier.verify(await signAccessToken({ sub })),
        'unauthenticated',
      );
    }
  });

  it('rejects a token with no subject', async () => {
    const token = await signAccessToken({});
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  it('rejects an anonymous session by default', async () => {
    const token = await signAccessToken({
      sub: newUserId(),
      isAnonymous: true,
    });
    await expectRejection(verifier.verify(token), 'forbidden');
  });

  it('accepts an anonymous session when explicitly allowed', async () => {
    const permissive = new SupabaseJwtVerifier(
      configWith({ allowAnonymous: true }),
    );
    const sub = newUserId();
    const user = await permissive.verify(
      await signAccessToken({ sub, isAnonymous: true }),
    );
    expect(user).toMatchObject({ userId: sub, isAnonymous: true });
  });

  it('honours the clock tolerance for a token that just expired', async () => {
    const sub = newUserId();
    const token = await signAccessToken({ sub, expiresInSeconds: -2 });
    await expect(verifier.verify(token)).resolves.toMatchObject({
      userId: sub,
    });
  });
});

describe('SupabaseJwtVerifier — asymmetric (JWKS)', () => {
  let keys: AsymmetricKeys;
  let server: Server;
  let verifier: SupabaseJwtVerifier;
  let jwksRequests = 0;

  beforeAll(async () => {
    keys = await generateSigningKeys();
    server = createServer((_request, response) => {
      jwksRequests += 1;
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify(keys.jwks));
    });
    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );
    const { port } = server.address() as AddressInfo;
    verifier = new SupabaseJwtVerifier(
      configWith({
        jwtSecret: null,
        jwksUrl: `http://127.0.0.1:${port}/auth/v1/.well-known/jwks.json`,
      }),
    );
  });

  afterAll(async () => {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  });

  it('reports its mode', () => {
    expect(verifier.mode).toBe('jwks');
  });

  it('accepts an ES256 token signed by the published key', async () => {
    const sub = newUserId();
    const token = await signAsymmetricToken(keys, { sub });
    await expect(verifier.verify(token)).resolves.toMatchObject({
      userId: sub,
    });
  });

  it('fetches the key set once and caches it', async () => {
    const before = jwksRequests;
    await verifier.verify(
      await signAsymmetricToken(keys, { sub: newUserId() }),
    );
    await verifier.verify(
      await signAsymmetricToken(keys, { sub: newUserId() }),
    );
    expect(jwksRequests).toBe(before);
  });

  it('rejects a token signed by a different key', async () => {
    const otherKeys = await generateSigningKeys();
    const token = await signAsymmetricToken(otherKeys, { sub: newUserId() });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });

  /**
   * The algorithm-confusion attack this verifier pins against: take the public
   * key everyone can fetch from the JWKS endpoint, use its bytes as an HMAC
   * secret, and sign your own token. A verifier that accepts "whatever the
   * header says" will validate it, because it has the "secret". Pinning
   * `algorithms: ['ES256','RS256']` in JWKS mode is what stops it.
   */
  it('rejects an HS256 token forged from the published public key', async () => {
    const publicKeyBytes = new TextEncoder().encode(
      JSON.stringify(keys.publicJwk),
    );
    const forged = await signAccessToken({
      sub: newUserId(),
      algorithm: 'HS256',
      secret: publicKeyBytes,
    });
    await expectRejection(verifier.verify(forged), 'unauthenticated');
  });

  it('rejects an unsigned ("alg: none") token', async () => {
    // Hand-built: jose refuses to sign with "none", which is itself the point.
    const header = Buffer.from(
      JSON.stringify({ alg: 'none', typ: 'JWT' }),
    ).toString('base64url');
    const payload = Buffer.from(
      JSON.stringify({
        sub: newUserId(),
        role: 'authenticated',
        iss: TEST_ISSUER,
        aud: TEST_AUDIENCE,
        exp: Math.floor(Date.now() / 1000) + 3600,
      }),
    ).toString('base64url');
    await expectRejection(
      verifier.verify(`${header}.${payload}.`),
      'unauthenticated',
    );
  });

  it('still rejects the anon role when the signature is valid', async () => {
    const token = await signAsymmetricToken(keys, {
      sub: newUserId(),
      role: 'anon',
    });
    await expectRejection(verifier.verify(token), 'unauthenticated');
  });
});
