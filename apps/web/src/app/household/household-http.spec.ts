import { TestBed } from '@angular/core/testing';
import { FakeAuthBackend, fakeSession } from '../../testing/fake-auth-backend';
import { AUTH_BACKEND } from '../auth/auth-backend';
import { RUNTIME_CONFIG, type RuntimeConfig } from '../config/runtime-config';
import { isApiErrorResponse } from '../errors/api-error';
import { HouseholdHttp } from './household-http';

const SUPABASE: RuntimeConfig = {
  authMode: 'supabase',
  supabaseUrl: 'http://127.0.0.1:54321',
  supabaseAnonKey: 'anon-key',
  apiBaseUrl: 'http://localhost:3000/',
};

const PREVIEW: RuntimeConfig = { authMode: 'preview', apiBaseUrl: 'http://localhost:3000' };

function client(config: RuntimeConfig = SUPABASE): HouseholdHttp {
  TestBed.configureTestingModule({
    providers: [
      HouseholdHttp,
      { provide: RUNTIME_CONFIG, useValue: config },
      { provide: AUTH_BACKEND, useValue: new FakeAuthBackend(fakeSession()) },
    ],
  });
  return TestBed.inject(HouseholdHttp);
}

function respondWith(body: unknown, init: ResponseInit = {}): ReturnType<typeof vi.fn> {
  const stub = vi.fn(async () =>
    body === undefined
      ? new Response(null, { status: 204, ...init })
      : new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
          ...init,
        }),
  );
  vi.stubGlobal('fetch', stub);
  return stub;
}

describe('HouseholdHttp', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the Supabase access token as a bearer credential', async () => {
    const stub = respondWith({ userId: 'user-1', email: null, isAnonymous: false, households: [] });

    await client().me();

    const [url, init] = stub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://localhost:3000/me');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer token-1');
  });

  it('does not double the slash when apiBaseUrl has a trailing one', async () => {
    const stub = respondWith([]);

    await client().listMembers('hh-1');

    expect(stub.mock.calls[0][0]).toBe('http://localhost:3000/households/hh-1/members');
  });

  it('refuses to call the API at all in preview mode, and says why', async () => {
    const stub = respondWith({});

    await expect(client(PREVIEW).me()).rejects.toMatchObject({
      error: { code: 'unauthenticated' },
    });
    // The point is that nothing was sent — a 401 would have read as an expired session.
    expect(stub).not.toHaveBeenCalled();
  });

  it('treats 204 as success with no body', async () => {
    respondWith(undefined);

    await expect(client().removeMember('hh-1', 'mem-1')).resolves.toBeUndefined();
  });

  it("rethrows the API's error envelope so callers can switch on the code", async () => {
    const envelope = {
      error: { code: 'forbidden', message: 'displayName belongs to that account.' },
      requestId: 'req-1',
    };
    respondWith(envelope, { status: 403 });

    await expect(
      client().updateMember('hh-1', 'mem-1', { displayName: 'Nope' }),
    ).rejects.toEqual(envelope);
  });

  it('turns an unreachable API into an envelope rather than a raw fetch failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    const cause = await client()
      .me()
      .catch((error: unknown) => error);

    expect(isApiErrorResponse(cause)).toBe(true);
    expect((cause as { error: { code: string } }).error.code).toBe('internal');
  });
});
