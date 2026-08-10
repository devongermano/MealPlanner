import { signal } from '@angular/core';
import type {
  AuthBackend,
  AuthOutcome,
  AuthSession,
  CredentialsInput,
  SignUpInput,
} from '../app/auth/auth-backend';

export function fakeSession(overrides: Partial<AuthSession['user']> = {}): AuthSession {
  return {
    user: { id: 'user-1', email: 'devon@example.com', displayName: 'Devon', ...overrides },
    accessToken: 'token-1',
  };
}

/** Test double for AUTH_BACKEND: no network, no storage, and it records what was called. */
export class FakeAuthBackend implements AuthBackend {
  private readonly state = signal<AuthSession | null>(null);
  readonly session = this.state.asReadonly();
  readonly whenReady = Promise.resolve();

  signOutCount = 0;
  lastSignUp: SignUpInput | null = null;
  lastSignIn: CredentialsInput | null = null;
  nextOutcome: AuthOutcome = { status: 'signed-in' };

  constructor(initial: AuthSession | null = null) {
    this.state.set(initial);
  }

  async signUp(input: SignUpInput): Promise<AuthOutcome> {
    this.lastSignUp = input;
    return this.settle();
  }

  async signIn(input: CredentialsInput): Promise<AuthOutcome> {
    this.lastSignIn = input;
    return this.settle();
  }

  async signOut(): Promise<void> {
    this.signOutCount++;
    this.state.set(null);
  }

  private settle(): AuthOutcome {
    if (this.nextOutcome.status === 'signed-in') {
      this.state.set(fakeSession());
    }
    return this.nextOutcome;
  }
}
