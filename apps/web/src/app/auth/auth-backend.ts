import { InjectionToken, type Signal } from '@angular/core';

export interface AuthUser {
  readonly id: string;
  readonly email: string;
  readonly displayName: string | null;
}

export interface AuthSession {
  readonly user: AuthUser;
  /** Bearer token for the mealplan API. Null under the preview backend, which has no API. */
  readonly accessToken: string | null;
}

export interface SignUpInput {
  readonly email: string;
  readonly password: string;
  readonly displayName: string;
}

export interface CredentialsInput {
  readonly email: string;
  readonly password: string;
}

/**
 * Result of a sign-in or sign-up attempt. `confirm-email` is a real Supabase
 * outcome, not an error: projects with confirmations on return no session until
 * the user clicks the link, and the UI has to say so rather than hang.
 */
export type AuthOutcome =
  | { readonly status: 'signed-in' }
  | { readonly status: 'confirm-email'; readonly email: string }
  | { readonly status: 'failed'; readonly message: string };

/**
 * The authentication seam. Exactly two implementations exist and the runtime
 * config picks one: Supabase (real) and preview (in-memory, development only).
 */
export interface AuthBackend {
  /** Resolves once a persisted session has been restored, or ruled out. */
  readonly whenReady: Promise<void>;
  readonly session: Signal<AuthSession | null>;
  signUp(input: SignUpInput): Promise<AuthOutcome>;
  signIn(input: CredentialsInput): Promise<AuthOutcome>;
  signOut(): Promise<void>;
}

export const AUTH_BACKEND = new InjectionToken<AuthBackend>('AUTH_BACKEND');
