import { Injectable, inject, signal } from '@angular/core';
import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js';
import { RUNTIME_CONFIG, type RuntimeConfig } from '../config/runtime-config';
import type {
  AuthBackend,
  AuthOutcome,
  AuthSession,
  CredentialsInput,
  SignUpInput,
} from './auth-backend';

type SupabaseRuntimeConfig = Extract<RuntimeConfig, { authMode: 'supabase' }>;

/**
 * Supabase-backed authentication. Per ARCHITECTURE.md this is the *only* thing the
 * web app talks to Supabase for — every other read and write goes through the
 * NestJS API, which verifies the access token this backend hands out.
 */
@Injectable()
export class SupabaseAuth implements AuthBackend {
  private readonly config = inject(RUNTIME_CONFIG) as SupabaseRuntimeConfig;
  private readonly client: SupabaseClient = createClient(
    this.config.supabaseUrl,
    this.config.supabaseAnonKey,
    { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } },
  );

  private readonly state = signal<AuthSession | null>(null);
  readonly session = this.state.asReadonly();

  private resolveReady!: () => void;
  readonly whenReady = new Promise<void>((resolve) => {
    this.resolveReady = resolve;
  });

  constructor() {
    // Fires immediately with INITIAL_SESSION, then on every sign-in, sign-out and
    // token refresh — so the signal tracks the persisted session without polling.
    this.client.auth.onAuthStateChange((_event, session) => {
      this.state.set(toAuthSession(session));
      this.resolveReady();
    });
  }

  async signUp({ email, password, displayName }: SignUpInput): Promise<AuthOutcome> {
    const { data, error } = await this.client.auth.signUp({
      email,
      password,
      options: { data: { display_name: displayName } },
    });
    if (error) {
      return { status: 'failed', message: readableAuthError(error.message) };
    }
    return data.session ? { status: 'signed-in' } : { status: 'confirm-email', email };
  }

  async signIn({ email, password }: CredentialsInput): Promise<AuthOutcome> {
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    return error
      ? { status: 'failed', message: readableAuthError(error.message) }
      : { status: 'signed-in' };
  }

  async signOut(): Promise<void> {
    await this.client.auth.signOut();
    this.state.set(null);
  }
}

function toAuthSession(session: Session | null): AuthSession | null {
  if (!session?.user) {
    return null;
  }
  const metadata = session.user.user_metadata as { display_name?: unknown } | undefined;
  const displayName = typeof metadata?.display_name === 'string' ? metadata.display_name : null;
  return {
    user: {
      id: session.user.id,
      email: session.user.email ?? '',
      displayName: displayName || null,
    },
    accessToken: session.access_token ?? null,
  };
}

/** GoTrue's messages are terse and lowercase; these are the ones users actually hit. */
function readableAuthError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes('invalid login credentials')) {
    return 'That email and password do not match an account.';
  }
  if (normalized.includes('already registered')) {
    return 'An account already exists for that email. Try signing in instead.';
  }
  if (normalized.includes('email not confirmed')) {
    return 'This account still needs to be confirmed. Check your inbox for the link.';
  }
  if (normalized.includes('failed to fetch') || normalized.includes('network')) {
    return 'Could not reach the authentication service. Check that it is running and try again.';
  }
  return message;
}
