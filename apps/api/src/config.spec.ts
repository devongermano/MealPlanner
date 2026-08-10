import { ConfigError, loadAuthConfig, loadConfig } from './config';

const BASE = {
  DATABASE_URL: 'postgresql://user:pass@127.0.0.1:5432/mealplan',
  SUPABASE_JWT_SECRET: 'a-secret-that-is-at-least-32-characters',
  SUPABASE_JWT_ISSUER: 'http://127.0.0.1:54321/auth/v1',
};

describe('loadAuthConfig', () => {
  it('accepts shared-secret mode', () => {
    const auth = loadAuthConfig(BASE);
    expect(auth.jwtSecret).toBe(BASE.SUPABASE_JWT_SECRET);
    expect(auth.jwksUrl).toBeNull();
    expect(auth.audience).toBe('authenticated');
    expect(auth.allowAnonymous).toBe(false);
  });

  it('accepts JWKS mode', () => {
    const auth = loadAuthConfig({
      SUPABASE_JWKS_URL: 'http://127.0.0.1:54321/auth/v1/.well-known/jwks.json',
      SUPABASE_JWT_ISSUER: BASE.SUPABASE_JWT_ISSUER,
    });
    expect(auth.jwksUrl).toBe(
      'http://127.0.0.1:54321/auth/v1/.well-known/jwks.json',
    );
    expect(auth.jwtSecret).toBeNull();
  });

  /**
   * Refusing both is the point: silently preferring one is how a service ends
   * up verifying against a key nobody believes it is using.
   */
  it('refuses both modes at once', () => {
    expect(() =>
      loadAuthConfig({
        ...BASE,
        SUPABASE_JWKS_URL:
          'http://127.0.0.1:54321/auth/v1/.well-known/jwks.json',
      }),
    ).toThrow(ConfigError);
  });

  it('refuses neither mode', () => {
    expect(() =>
      loadAuthConfig({ SUPABASE_JWT_ISSUER: BASE.SUPABASE_JWT_ISSUER }),
    ).toThrow(ConfigError);
  });

  it('refuses a short secret', () => {
    expect(() =>
      loadAuthConfig({ ...BASE, SUPABASE_JWT_SECRET: 'too-short' }),
    ).toThrow(ConfigError);
  });

  it('refuses a JWKS value that is not a URL', () => {
    expect(() =>
      loadAuthConfig({
        SUPABASE_JWKS_URL: 'not a url',
        SUPABASE_JWT_ISSUER: BASE.SUPABASE_JWT_ISSUER,
      }),
    ).toThrow(ConfigError);
  });

  /** No issuer check means any project signing with the same algorithm is trusted. */
  it('refuses a missing issuer', () => {
    expect(() => loadAuthConfig({ ...BASE, SUPABASE_JWT_ISSUER: '' })).toThrow(
      ConfigError,
    );
  });

  it('treats whitespace-only values as absent', () => {
    expect(() =>
      loadAuthConfig({ ...BASE, SUPABASE_JWT_SECRET: '   ' }),
    ).toThrow(ConfigError);
  });

  it.each([
    ['true', true],
    ['1', true],
    ['yes', true],
    ['false', false],
    ['0', false],
    ['', false],
  ])('parses AUTH_ALLOW_ANONYMOUS=%s as %s', (raw, expected) => {
    expect(
      loadAuthConfig({ ...BASE, AUTH_ALLOW_ANONYMOUS: raw }).allowAnonymous,
    ).toBe(expected);
  });

  it('refuses a non-boolean AUTH_ALLOW_ANONYMOUS instead of guessing', () => {
    expect(() =>
      loadAuthConfig({ ...BASE, AUTH_ALLOW_ANONYMOUS: 'maybe' }),
    ).toThrow(ConfigError);
  });
});

describe('loadConfig', () => {
  it('requires a database url', () => {
    const withoutDatabase = {
      SUPABASE_JWT_SECRET: BASE.SUPABASE_JWT_SECRET,
      SUPABASE_JWT_ISSUER: BASE.SUPABASE_JWT_ISSUER,
    };
    expect(() => loadConfig(withoutDatabase)).toThrow(ConfigError);
    expect(() => loadConfig({ ...BASE, DATABASE_URL: '   ' })).toThrow(
      ConfigError,
    );
  });

  it('defaults the port, host and docs flag', () => {
    const config = loadConfig(BASE);
    expect(config.port).toBe(3000);
    expect(config.host).toBe('0.0.0.0');
    expect(config.docsEnabled).toBe(false);
  });

  it('refuses a non-numeric port', () => {
    expect(() => loadConfig({ ...BASE, PORT: 'eighty' })).toThrow(ConfigError);
  });
});
