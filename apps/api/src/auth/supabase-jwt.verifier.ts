import { Inject, Injectable, Logger } from '@nestjs/common';
import {
  createRemoteJWKSet,
  jwtVerify,
  type JWTPayload,
  type JWTVerifyGetKey,
} from 'jose';
import { API_CONFIG, type ApiConfig, type AuthConfig } from '../config';
import { ApiException } from '../common/api-error';
import type { AuthenticatedUser } from './authenticated-user';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** GoTrue puts the PostgREST role in the token. Only one value is a real user. */
const USER_ROLE_CLAIM = 'authenticated';

/**
 * Verifies Supabase Auth (GoTrue) access tokens.
 *
 * Two modes, chosen by config and never both (see `loadAuthConfig`):
 *   - JWKS   — asymmetric keys (ES256/RS256). Preferred: this service holds no
 *              signing material, so a compromise here cannot mint tokens.
 *   - Secret — HS256 against `GOTRUE_JWT_SECRET`. What a freshly-started local
 *              stack gives you.
 *
 * Three checks here are not ceremony, and removing any of them is a privilege
 * escalation:
 *
 *   1. ALGORITHMS ARE PINNED per mode. Accepting both families lets an attacker
 *      take an RSA public key (published at the JWKS endpoint), sign a token
 *      with it as an HMAC secret, and have it verify.
 *   2. `role` MUST be `authenticated`. Supabase's anon/publishable key is
 *      itself a valid JWT signed with the same secret — it just carries
 *      `role: "anon"` and no `sub`. Without this check, a key printed in the
 *      web app's bundle authenticates as a user. The same check keeps a
 *      `service_role` key from arriving on a user route.
 *   3. `sub` MUST be a UUID. It is the identity every household query keys on,
 *      and a non-UUID reaching Prisma is a 500 at best.
 */
@Injectable()
export class SupabaseJwtVerifier {
  private readonly logger = new Logger(SupabaseJwtVerifier.name);
  private readonly auth: AuthConfig;
  private readonly algorithms: string[];
  private readonly getKey: JWTVerifyGetKey | Uint8Array;

  constructor(@Inject(API_CONFIG) config: ApiConfig) {
    this.auth = config.auth;

    if (this.auth.jwksUrl) {
      this.algorithms = ['ES256', 'RS256'];
      // Caches keys and rate-limits refetches; a rotated `kid` triggers exactly
      // one refresh rather than one per request.
      this.getKey = createRemoteJWKSet(new URL(this.auth.jwksUrl));
    } else {
      this.algorithms = ['HS256'];
      this.getKey = new TextEncoder().encode(this.auth.jwtSecret!);
    }
  }

  /** Which mode this instance is in. Reported by `GET /healthz` for ops sanity. */
  get mode(): 'jwks' | 'shared-secret' {
    return this.auth.jwksUrl ? 'jwks' : 'shared-secret';
  }

  async verify(token: string): Promise<AuthenticatedUser> {
    let payload: JWTPayload;
    try {
      const result = await jwtVerify(token, this.getKey as JWTVerifyGetKey, {
        issuer: this.auth.issuer,
        audience: this.auth.audience,
        algorithms: this.algorithms,
        clockTolerance: this.auth.clockToleranceSec,
        requiredClaims: ['sub', 'exp'],
      });
      payload = result.payload;
    } catch (error) {
      // The reason a token failed (expired vs wrong issuer vs bad signature) is
      // useful to an attacker probing configuration, so it is logged and not
      // returned.
      this.logger.debug(
        `Token rejected: ${error instanceof Error ? error.message : String(error)}`,
      );
      throw ApiException.unauthenticated('Invalid or expired access token.');
    }

    const role = typeof payload.role === 'string' ? payload.role : null;
    if (role !== USER_ROLE_CLAIM) {
      this.logger.warn(
        `Token rejected: role claim was ${JSON.stringify(role)}, expected "${USER_ROLE_CLAIM}". ` +
          'An anon or service_role key was presented as a user token.',
      );
      throw ApiException.unauthenticated('Invalid or expired access token.');
    }

    const sub = typeof payload.sub === 'string' ? payload.sub : '';
    if (!UUID_RE.test(sub)) {
      this.logger.warn('Token rejected: sub claim is not a UUID.');
      throw ApiException.unauthenticated('Invalid or expired access token.');
    }

    const isAnonymous = payload.is_anonymous === true;
    if (isAnonymous && !this.auth.allowAnonymous) {
      throw ApiException.forbidden(
        'Anonymous sessions cannot use this API. Sign in with a permanent account.',
      );
    }

    return {
      userId: sub,
      email: typeof payload.email === 'string' && payload.email ? payload.email : null,
      isAnonymous,
      sessionId: typeof payload.session_id === 'string' ? payload.session_id : null,
    };
  }
}
