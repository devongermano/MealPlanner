/**
 * Env-driven config with sane defaults, validated at boot.
 *
 * Auth config is validated STRICTLY and fails the process on start rather than
 * degrading: a mis-configured verifier is the one bug where "it still boots" is
 * worse than "it does not boot". See `.env.example` for a working local set.
 */

export interface AuthConfig {
  /**
   * JWKS endpoint of the Supabase project's auth server, e.g.
   * `http://127.0.0.1:54321/auth/v1/.well-known/jwks.json`. Set this when the
   * project uses asymmetric signing keys (ES256/RS256) — the production
   * posture, because the API never holds signing material.
   * Env: SUPABASE_JWKS_URL.
   */
  jwksUrl: string | null;
  /**
   * Shared HS256 secret (GoTrue's `GOTRUE_JWT_SECRET`). The default for a
   * freshly-started local stack, where `supabase start` prints it.
   * Env: SUPABASE_JWT_SECRET.
   */
  jwtSecret: string | null;
  /** Expected `iss`. Env: SUPABASE_JWT_ISSUER. */
  issuer: string;
  /** Expected `aud`. GoTrue issues user tokens with `authenticated`. Env: SUPABASE_JWT_AUDIENCE. */
  audience: string;
  /**
   * Whether tokens minted by Supabase anonymous sign-in are accepted. Off by
   * default: a household is durable state and an ephemeral identity should not
   * be able to create one. Env: AUTH_ALLOW_ANONYMOUS.
   */
  allowAnonymous: boolean;
  /** Leeway for exp/nbf, in seconds. Env: AUTH_CLOCK_TOLERANCE_SEC. */
  clockToleranceSec: number;
}

export interface ApiConfig {
  /** Port the HTTP server binds. Env: PORT. */
  port: number;
  /** Host the HTTP server binds. Env: HOST. */
  host: string;
  /** Base URL of the (private) Python solver service. Env: SOLVER_URL. Unused until M2 proper. */
  solverUrl: string;
  /** Postgres connection string. Env: DATABASE_URL. */
  databaseUrl: string;
  /**
   * Serve the Swagger UI on `/docs`. Off by default — the generated contract in
   * packages/contracts-api is what consumers build against, and a public route
   * map is a gift to anyone probing. Env: API_DOCS.
   */
  docsEnabled: boolean;
  auth: AuthConfig;
}

export const API_CONFIG = Symbol('API_CONFIG');

export class ConfigError extends Error {
  constructor(message: string) {
    super(`Invalid API configuration: ${message}`);
    this.name = 'ConfigError';
  }
}

function bool(raw: string | undefined, fallback: boolean): boolean {
  if (raw === undefined || raw === '') return fallback;
  if (['1', 'true', 'yes', 'on'].includes(raw.toLowerCase())) return true;
  if (['0', 'false', 'no', 'off'].includes(raw.toLowerCase())) return false;
  throw new ConfigError(`expected a boolean, got ${JSON.stringify(raw)}`);
}

function num(raw: string | undefined, fallback: number, label: string): number {
  if (raw === undefined || raw === '') return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    throw new ConfigError(`${label} must be a number, got ${JSON.stringify(raw)}`);
  }
  return parsed;
}

function trimmed(raw: string | undefined): string | null {
  const value = raw?.trim();
  return value ? value : null;
}

export function loadAuthConfig(env: NodeJS.ProcessEnv = process.env): AuthConfig {
  const jwksUrl = trimmed(env.SUPABASE_JWKS_URL);
  const jwtSecret = trimmed(env.SUPABASE_JWT_SECRET);

  // Both set is a configuration ambiguity, not a fallback chain. Silently
  // preferring one is how a project ends up verifying against the wrong key
  // for months without noticing.
  if (jwksUrl && jwtSecret) {
    throw new ConfigError(
      'set exactly one of SUPABASE_JWKS_URL (asymmetric, preferred) or SUPABASE_JWT_SECRET (HS256) — both were provided',
    );
  }
  if (!jwksUrl && !jwtSecret) {
    throw new ConfigError(
      'set SUPABASE_JWKS_URL (asymmetric, preferred) or SUPABASE_JWT_SECRET (HS256) — neither was provided. See apps/api/.env.example',
    );
  }
  if (jwksUrl) {
    try {
      new URL(jwksUrl);
    } catch {
      throw new ConfigError(`SUPABASE_JWKS_URL is not a URL: ${JSON.stringify(jwksUrl)}`);
    }
  }
  // GoTrue rejects secrets under 32 chars; refusing them here turns a runtime
  // verification failure into a boot failure with a readable cause.
  if (jwtSecret && jwtSecret.length < 32) {
    throw new ConfigError('SUPABASE_JWT_SECRET must be at least 32 characters');
  }

  const issuer = trimmed(env.SUPABASE_JWT_ISSUER);
  if (!issuer) {
    throw new ConfigError(
      'SUPABASE_JWT_ISSUER is required — an unverified issuer accepts tokens from any project sharing the algorithm',
    );
  }

  return {
    jwksUrl,
    jwtSecret,
    issuer,
    audience: trimmed(env.SUPABASE_JWT_AUDIENCE) ?? 'authenticated',
    allowAnonymous: bool(env.AUTH_ALLOW_ANONYMOUS, false),
    clockToleranceSec: num(env.AUTH_CLOCK_TOLERANCE_SEC, 5, 'AUTH_CLOCK_TOLERANCE_SEC'),
  };
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): ApiConfig {
  const databaseUrl = trimmed(env.DATABASE_URL);
  if (!databaseUrl) {
    throw new ConfigError('DATABASE_URL is required. See apps/api/.env.example');
  }

  return {
    port: num(env.PORT, 3000, 'PORT'),
    host: env.HOST ?? '0.0.0.0',
    solverUrl: env.SOLVER_URL ?? 'http://localhost:8000',
    databaseUrl,
    docsEnabled: bool(env.API_DOCS, false),
    auth: loadAuthConfig(env),
  };
}
