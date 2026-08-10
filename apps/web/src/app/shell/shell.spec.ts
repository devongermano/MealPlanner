import { TestBed, type ComponentFixture } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { FakeAuthBackend, fakeSession } from '../../testing/fake-auth-backend';
import {
  FAKE_USER_ID,
  FakeHouseholdApi,
  fakeHousehold,
  fakeMember,
} from '../../testing/fake-household-api';
import { AUTH_BACKEND } from '../auth/auth-backend';
import {
  HOUSEHOLD_API,
  type HouseholdMemberView,
  type HouseholdSummary,
} from '../household/household-api';
import { HouseholdStore } from '../household/household-store';
import { Shell } from './shell';

const HOME = fakeHousehold('hh-1', 'The Germanos');
const CABIN = fakeHousehold('hh-2', 'Cabin week');

async function mount(
  households: HouseholdSummary[],
  members: HouseholdMemberView[],
  backend = new FakeAuthBackend(fakeSession()),
): Promise<ComponentFixture<Shell>> {
  TestBed.configureTestingModule({
    imports: [Shell],
    providers: [
      provideRouter([]),
      { provide: AUTH_BACKEND, useValue: backend },
      { provide: HOUSEHOLD_API, useValue: new FakeHouseholdApi(households, members) },
    ],
  });

  await TestBed.inject(HouseholdStore).load();
  const fixture = TestBed.createComponent(Shell);
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture;
}

function text(fixture: ComponentFixture<Shell>): string {
  return (fixture.nativeElement as HTMLElement).textContent ?? '';
}

function query<T extends Element>(fixture: ComponentFixture<Shell>, selector: string): T | null {
  return (fixture.nativeElement as HTMLElement).querySelector<T>(selector);
}

describe('Shell', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('shows the account and the active household', async () => {
    const fixture = await mount([HOME], [fakeMember('mem-1', 'Devon', 'planner', FAKE_USER_ID)]);

    expect(text(fixture)).toContain('Devon');
    expect(query(fixture, '.household-name')?.textContent).toContain('The Germanos');
    expect(query(fixture, '.avatar')?.textContent?.trim()).toBe('D');
  });

  it('shows the household as plain text when there is only one', async () => {
    const fixture = await mount([HOME], []);

    expect(query(fixture, '.switcher')).toBeNull();
    expect(query(fixture, '.household-name')).not.toBeNull();
  });

  it('offers a switcher once there is more than one household', async () => {
    const fixture = await mount([HOME, CABIN], []);

    const switcher = query<HTMLSelectElement>(fixture, '.switcher');
    expect(switcher).not.toBeNull();
    expect(switcher?.querySelectorAll('option').length).toBe(2);
  });

  it('links to the pages the shell actually owns', async () => {
    const fixture = await mount([HOME], []);

    const hrefs = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('nav a'),
    ).map((anchor) => anchor.getAttribute('href'));

    expect(hrefs).toEqual(['/app', '/settings']);
  });

  it('signs out, drops household state, and returns to the login page', async () => {
    const backend = new FakeAuthBackend(fakeSession());
    const fixture = await mount([HOME], [fakeMember('mem-1', 'Devon')], backend);
    const store = TestBed.inject(HouseholdStore);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);

    expect(store.hasHousehold()).toBe(true);

    const signOutButton = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).find((button) => button.textContent?.includes('Sign out'));
    signOutButton?.click();
    await fixture.whenStable();

    expect(backend.signOutCount).toBe(1);
    expect(store.hasHousehold()).toBe(false);
    expect(navigate).toHaveBeenCalledWith('/login');
  });
});
