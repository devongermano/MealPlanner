import { isLoopbackHost, resolveRuntimeConfig } from './runtime-config';

describe('resolveRuntimeConfig', () => {
  it('accepts a fully specified Supabase config', () => {
    const result = resolveRuntimeConfig(
      {
        authMode: 'supabase',
        supabaseUrl: 'http://127.0.0.1:54321',
        supabaseAnonKey: 'anon-key',
        apiBaseUrl: 'http://localhost:3000',
      },
      false,
    );

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.config).toEqual({
        authMode: 'supabase',
        supabaseUrl: 'http://127.0.0.1:54321',
        supabaseAnonKey: 'anon-key',
        apiBaseUrl: 'http://localhost:3000',
      });
    }
  });

  it('names the missing Supabase settings instead of failing blankly', () => {
    const result = resolveRuntimeConfig(
      { authMode: 'supabase', supabaseUrl: 'http://127.0.0.1:54321', supabaseAnonKey: '  ' },
      true,
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.detail).toContain('supabaseAnonKey');
      expect(result.problem.steps.length).toBeGreaterThan(0);
    }
  });

  it('allows preview auth on localhost', () => {
    const result = resolveRuntimeConfig({ authMode: 'preview' }, true);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.config.authMode).toBe('preview');
    }
  });

  it('refuses preview auth anywhere else', () => {
    const result = resolveRuntimeConfig({ authMode: 'preview' }, false);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.headline).toContain('localhost');
    }
  });

  it('rejects an unknown auth mode and says what is valid', () => {
    const result = resolveRuntimeConfig({ authMode: 'firebase' }, true);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.detail).toContain('firebase');
    }
  });

  it('rejects a config that is not an object', () => {
    expect(resolveRuntimeConfig(null, true).ok).toBe(false);
    expect(resolveRuntimeConfig(['authMode'], true).ok).toBe(false);
  });
});

describe('isLoopbackHost', () => {
  it('recognises loopback names', () => {
    expect(isLoopbackHost('localhost')).toBe(true);
    expect(isLoopbackHost('127.0.0.1')).toBe(true);
    expect(isLoopbackHost('app.localhost')).toBe(true);
  });

  it('rejects public hosts', () => {
    expect(isLoopbackHost('mealplan.onrender.com')).toBe(false);
    expect(isLoopbackHost('localhost.example.com')).toBe(false);
  });
});
