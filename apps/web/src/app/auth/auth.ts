import { Injectable, computed, inject } from '@angular/core';
import {
  AUTH_BACKEND,
  type AuthOutcome,
  type CredentialsInput,
  type SignUpInput,
} from './auth-backend';

/**
 * What the rest of the app injects. Everything above this line is backend-agnostic:
 * swapping Supabase for the preview backend changes one provider in app.config.ts
 * and nothing else.
 */
@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly backend = inject(AUTH_BACKEND);

  readonly session = this.backend.session;
  readonly user = computed(() => this.session()?.user ?? null);
  readonly isSignedIn = computed(() => this.session() !== null);

  /** Display name if the user gave one, otherwise the local part of their email. */
  readonly displayName = computed(() => {
    const user = this.user();
    if (!user) {
      return '';
    }
    return user.displayName?.trim() || user.email.split('@')[0];
  });

  /** Bearer token for API calls. Guards and route resolvers await `whenReady` first. */
  readonly accessToken = computed(() => this.session()?.accessToken ?? null);

  whenReady(): Promise<void> {
    return this.backend.whenReady;
  }

  signUp(input: SignUpInput): Promise<AuthOutcome> {
    return this.backend.signUp(input);
  }

  signIn(input: CredentialsInput): Promise<AuthOutcome> {
    return this.backend.signIn(input);
  }

  signOut(): Promise<void> {
    return this.backend.signOut();
  }
}
