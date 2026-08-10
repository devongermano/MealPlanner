import { Injectable, signal } from '@angular/core';
import type {
  AuthBackend,
  AuthOutcome,
  AuthSession,
  CredentialsInput,
  SignUpInput,
} from './auth-backend';

const STORE_KEY = 'mealplan.preview.accounts';
const SESSION_KEY = 'mealplan.preview.session';

interface PreviewAccount {
  readonly id: string;
  readonly email: string;
  readonly displayName: string;
  readonly passwordDigest: string;
}

/**
 * In-memory accounts for walking the app without a Supabase instance. Selected only
 * by an explicit `"authMode": "preview"` in config.json, and refused outright by
 * production builds (see resolveRuntimeConfig) — it verifies nothing an attacker
 * could not trivially forge, so it must never be reachable from a real deployment.
 *
 * Passwords are stored as a non-cryptographic digest. That is obfuscation, not
 * security: it exists so a throwaway password does not sit in sessionStorage in
 * plain text, and for no stronger reason.
 */
@Injectable()
export class PreviewAuth implements AuthBackend {
  private readonly state = signal<AuthSession | null>(restoreSession());
  readonly session = this.state.asReadonly();
  readonly whenReady = Promise.resolve();

  async signUp({ email, password, displayName }: SignUpInput): Promise<AuthOutcome> {
    const accounts = readAccounts();
    const normalizedEmail = email.trim().toLowerCase();
    if (accounts.some((account) => account.email === normalizedEmail)) {
      return {
        status: 'failed',
        message: 'An account already exists for that email. Try signing in instead.',
      };
    }
    const account: PreviewAccount = {
      id: `preview-${crypto.randomUUID()}`,
      email: normalizedEmail,
      displayName: displayName.trim(),
      passwordDigest: digest(password),
    };
    writeAccounts([...accounts, account]);
    this.establish(account);
    return { status: 'signed-in' };
  }

  async signIn({ email, password }: CredentialsInput): Promise<AuthOutcome> {
    const normalizedEmail = email.trim().toLowerCase();
    const account = readAccounts().find((candidate) => candidate.email === normalizedEmail);
    if (!account || account.passwordDigest !== digest(password)) {
      return { status: 'failed', message: 'That email and password do not match an account.' };
    }
    this.establish(account);
    return { status: 'signed-in' };
  }

  async signOut(): Promise<void> {
    sessionStorage.removeItem(SESSION_KEY);
    this.state.set(null);
  }

  private establish(account: PreviewAccount): void {
    const session: AuthSession = {
      user: { id: account.id, email: account.email, displayName: account.displayName },
      accessToken: null,
    };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    this.state.set(session);
  }
}

function readAccounts(): readonly PreviewAccount[] {
  return readJson<PreviewAccount[]>(STORE_KEY) ?? [];
}

function writeAccounts(accounts: readonly PreviewAccount[]): void {
  sessionStorage.setItem(STORE_KEY, JSON.stringify(accounts));
}

function restoreSession(): AuthSession | null {
  return readJson<AuthSession>(SESSION_KEY);
}

function readJson<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

/** FNV-1a. Fast, stable, and not a password hash — see the class comment. */
function digest(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16);
}
