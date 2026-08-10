import { InjectionToken } from '@angular/core';

/**
 * Runtime configuration, fetched from `/config.json` before the app bootstraps.
 *
 * Deliberately runtime and not build-time: the web app ships to Render as a static
 * bundle, and pointing a build at a different Supabase project should not require
 * rebuilding it. Deploys overwrite `config.json`; the bundle is identical everywhere.
 */
export type RuntimeConfig =
  | {
      readonly authMode: 'supabase';
      readonly supabaseUrl: string;
      readonly supabaseAnonKey: string;
      readonly apiBaseUrl: string;
    }
  | {
      readonly authMode: 'preview';
      readonly apiBaseUrl: string;
    };

export const RUNTIME_CONFIG = new InjectionToken<RuntimeConfig>('RUNTIME_CONFIG');

/** A configuration failure, written to be read by a person who has to fix it. */
export interface ConfigProblem {
  readonly headline: string;
  readonly detail: string;
  readonly steps: readonly string[];
}

export type ConfigResolution =
  | { readonly ok: true; readonly config: RuntimeConfig }
  | { readonly ok: false; readonly problem: ConfigProblem };

const CONFIG_PATH = 'config.json';

const MISSING_FILE: ConfigProblem = {
  headline: 'mealplan could not read its configuration',
  detail:
    'The app fetches config.json at startup to learn which Supabase project to sign you in against. That request did not return usable JSON.',
  steps: [
    'Confirm apps/web/public/config.json exists and contains valid JSON.',
    'If this is a deployed build, check that your deploy step writes config.json into the published directory.',
  ],
};

const PREVIEW_OFF_LOCALHOST: ConfigProblem = {
  headline: 'Preview mode only runs on localhost',
  detail:
    'config.json asks for the in-memory preview backend, which fakes accounts and verifies nothing. Refusing it here is deliberate: a deployment that answered to forged sign-ins would be worse than one that does not load.',
  steps: [
    'Set "authMode": "supabase" in the deployed config.json.',
    'Supply supabaseUrl and supabaseAnonKey for the project this deployment belongs to.',
  ],
};

const LOOPBACK_HOSTS: readonly string[] = ['localhost', '127.0.0.1', '[::1]', '::1', ''];

/** Preview auth is gated on this rather than on a build flag — a host name cannot be optimized away. */
export function isLoopbackHost(hostname: string): boolean {
  return LOOPBACK_HOSTS.includes(hostname) || hostname.endsWith('.localhost');
}

function unconfiguredSupabase(missing: readonly string[]): ConfigProblem {
  return {
    headline: 'Supabase is not configured yet',
    detail: `config.json selects the Supabase auth backend but ${missing.join(' and ')} ${
      missing.length === 1 ? 'is' : 'are'
    } empty, so there is nowhere to send a sign-in.`,
    steps: [
      'Start the local stack with `supabase start` — it prints an API URL and an anon key when it finishes.',
      'Paste both into apps/web/public/config.json as supabaseUrl and supabaseAnonKey.',
      'Reload this page. Nothing needs rebuilding.',
    ],
  };
}

function asString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * Validate a parsed config.json. Pure, so the failure modes are unit-testable
 * without a network or a browser.
 *
 * @param raw parsed contents of config.json
 * @param loopback whether the page is served from localhost; preview auth is refused elsewhere
 */
export function resolveRuntimeConfig(raw: unknown, loopback: boolean): ConfigResolution {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, problem: MISSING_FILE };
  }

  const source = raw as Record<string, unknown>;
  const apiBaseUrl = asString(source, 'apiBaseUrl') || 'http://localhost:3000';
  const authMode = asString(source, 'authMode');

  if (authMode === 'preview') {
    if (!loopback) {
      return { ok: false, problem: PREVIEW_OFF_LOCALHOST };
    }
    return { ok: true, config: { authMode: 'preview', apiBaseUrl } };
  }

  if (authMode === 'supabase') {
    const supabaseUrl = asString(source, 'supabaseUrl');
    const supabaseAnonKey = asString(source, 'supabaseAnonKey');
    const missing: string[] = [];
    if (!supabaseUrl) {
      missing.push('supabaseUrl');
    }
    if (!supabaseAnonKey) {
      missing.push('supabaseAnonKey');
    }
    if (missing.length > 0) {
      return { ok: false, problem: unconfiguredSupabase(missing) };
    }
    return { ok: true, config: { authMode: 'supabase', supabaseUrl, supabaseAnonKey, apiBaseUrl } };
  }

  return {
    ok: false,
    problem: {
      headline: 'config.json does not name a usable auth backend',
      detail: `Expected "authMode" to be "supabase" or "preview"${
        authMode ? `, but found "${authMode}"` : ', but it was missing'
      }.`,
      steps: [
        'Set "authMode": "supabase" to sign in against a real Supabase project.',
        'Set "authMode": "preview" to walk the app locally with in-memory accounts.',
      ],
    },
  };
}

/** Fetch and validate config.json. Never throws — every failure becomes a ConfigProblem. */
export async function loadRuntimeConfig(): Promise<ConfigResolution> {
  try {
    const response = await fetch(CONFIG_PATH, { cache: 'no-store' });
    if (!response.ok) {
      return { ok: false, problem: MISSING_FILE };
    }
    return resolveRuntimeConfig(await response.json(), isLoopbackHost(location.hostname));
  } catch {
    return { ok: false, problem: MISSING_FILE };
  }
}
