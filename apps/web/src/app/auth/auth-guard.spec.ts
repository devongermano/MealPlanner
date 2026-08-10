import { TestBed } from '@angular/core/testing';
import {
  Router,
  provideRouter,
  type ActivatedRouteSnapshot,
  type CanActivateFn,
  type RouterStateSnapshot,
  type UrlTree,
} from '@angular/router';
import { FakeAuthBackend, fakeSession } from '../../testing/fake-auth-backend';
import { AUTH_BACKEND, type AuthSession } from './auth-backend';
import { requireGuest, requireSession } from './auth-guard';

function configure(session: AuthSession | null): void {
  TestBed.configureTestingModule({
    providers: [provideRouter([]), { provide: AUTH_BACKEND, useValue: new FakeAuthBackend(session) }],
  });
}

function run(guard: CanActivateFn, url: string): Promise<boolean | UrlTree> {
  return TestBed.runInInjectionContext(
    () =>
      guard({} as ActivatedRouteSnapshot, { url } as RouterStateSnapshot) as Promise<
        boolean | UrlTree
      >,
  );
}

function serialize(result: boolean | UrlTree): string {
  return TestBed.inject(Router).serializeUrl(result as UrlTree);
}

describe('requireSession', () => {
  it('lets a signed-in account through', async () => {
    configure(fakeSession());

    expect(await run(requireSession, '/app')).toBe(true);
  });

  it('sends a signed-out visitor to /login and remembers where they were going', async () => {
    configure(null);

    const result = await run(requireSession, '/settings');

    expect(result).not.toBe(true);
    expect(serialize(result)).toBe('/login?next=%2Fsettings');
  });
});

describe('requireGuest', () => {
  it('lets a signed-out visitor reach the auth pages', async () => {
    configure(null);

    expect(await run(requireGuest, '/login')).toBe(true);
  });

  it('redirects a signed-in account away from the auth pages', async () => {
    configure(fakeSession());

    const result = await run(requireGuest, '/login');

    expect(result).not.toBe(true);
    expect(serialize(result)).toBe('/app');
  });
});
