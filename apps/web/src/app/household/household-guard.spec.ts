import { TestBed } from '@angular/core/testing';
import {
  Router,
  provideRouter,
  type ActivatedRouteSnapshot,
  type CanActivateFn,
  type RouterStateSnapshot,
  type UrlTree,
} from '@angular/router';
import { FakeHouseholdApi, fakeHousehold } from '../../testing/fake-household-api';
import { toApiError } from '../errors/api-error';
import { HOUSEHOLD_API, type HouseholdApi } from './household-api';
import { requireHousehold, requireNoHousehold } from './household-guard';

/** Stands in for an API that is reachable but failing — the case the guards must not misread. */
function failingApi(): HouseholdApi {
  return {
    me: () => Promise.reject(toApiError('internal', 'The API is down.')),
    createHousehold: () => Promise.reject(new Error('unreachable')),
    listMembers: () => Promise.reject(new Error('unreachable')),
    addMember: () => Promise.reject(new Error('unreachable')),
    updateMember: () => Promise.reject(new Error('unreachable')),
    updateSelf: () => Promise.reject(new Error('unreachable')),
    removeMember: () => Promise.reject(new Error('unreachable')),
  };
}

function configure(api: HouseholdApi): void {
  TestBed.configureTestingModule({
    providers: [provideRouter([]), { provide: HOUSEHOLD_API, useValue: api }],
  });
}

function run(guard: CanActivateFn): Promise<boolean | UrlTree> {
  return TestBed.runInInjectionContext(
    () =>
      guard({} as ActivatedRouteSnapshot, { url: '/app' } as RouterStateSnapshot) as Promise<
        boolean | UrlTree
      >,
  );
}

function serialize(result: boolean | UrlTree): string {
  return TestBed.inject(Router).serializeUrl(result as UrlTree);
}

describe('requireHousehold', () => {
  beforeEach(() => localStorage.clear());

  it('lets an account with a household into the shell', async () => {
    configure(new FakeHouseholdApi([fakeHousehold('hh-1', 'The Germanos')]));

    expect(await run(requireHousehold)).toBe(true);
  });

  it('sends an account with no household to onboarding', async () => {
    configure(new FakeHouseholdApi([]));

    expect(serialize(await run(requireHousehold))).toBe('/onboarding');
  });

  it('does NOT mistake a failed load for an empty one', async () => {
    configure(failingApi());

    // Redirecting here would invite someone whose API is merely down to create a
    // second household they already own. The shell renders the error instead.
    expect(await run(requireHousehold)).toBe(true);
  });
});

describe('requireNoHousehold', () => {
  beforeEach(() => localStorage.clear());

  it('lets a household-less account start onboarding', async () => {
    configure(new FakeHouseholdApi([]));

    expect(await run(requireNoHousehold)).toBe(true);
  });

  it('keeps an onboarded account out of the wizard', async () => {
    configure(new FakeHouseholdApi([fakeHousehold('hh-1', 'The Germanos')]));

    expect(serialize(await run(requireNoHousehold))).toBe('/app');
  });

  it('refuses to start onboarding it cannot know is owed', async () => {
    configure(failingApi());

    expect(serialize(await run(requireNoHousehold))).toBe('/app');
  });
});
