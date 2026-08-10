import { TestBed, type ComponentFixture } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { FakeAuthBackend, fakeSession } from '../../testing/fake-auth-backend';
import { AUTH_BACKEND } from '../auth/auth-backend';
import { HOUSEHOLD_API } from '../household/household-api';
import { HouseholdApiMock } from '../household/household-api-mock';
import { HouseholdStore } from '../household/household-store';
import { Onboarding } from './onboarding';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  await fixture.whenStable();
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

function element(fixture: ComponentFixture<unknown>): HTMLElement {
  return fixture.nativeElement as HTMLElement;
}

function type(fixture: ComponentFixture<unknown>, selector: string, value: string): void {
  const input = element(fixture).querySelector<HTMLInputElement>(selector);
  if (!input) {
    throw new Error(`No input matching ${selector}`);
  }
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

async function submit(fixture: ComponentFixture<unknown>, selector: string): Promise<void> {
  const form = element(fixture).querySelector<HTMLFormElement>(selector);
  if (!form) {
    throw new Error(`No form matching ${selector}`);
  }
  form.dispatchEvent(new Event('submit'));
  await settle(fixture);
}

async function clickByText(fixture: ComponentFixture<unknown>, text: string): Promise<void> {
  const button = Array.from(element(fixture).querySelectorAll('button')).find((candidate) =>
    candidate.textContent?.includes(text),
  );
  if (!button) {
    throw new Error(`No button labelled ${text}`);
  }
  button.click();
  await settle(fixture);
}

function memberNames(fixture: ComponentFixture<unknown>): string[] {
  return Array.from(element(fixture).querySelectorAll('.members .name')).map((node) =>
    (node.textContent ?? '').trim(),
  );
}

describe('Onboarding', () => {
  let fixture: ComponentFixture<Onboarding>;

  beforeEach(async () => {
    sessionStorage.clear();
    localStorage.clear();

    await TestBed.configureTestingModule({
      imports: [Onboarding],
      providers: [
        provideRouter([]),
        { provide: AUTH_BACKEND, useValue: new FakeAuthBackend(fakeSession()) },
        { provide: HOUSEHOLD_API, useClass: HouseholdApiMock },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Onboarding);
    await settle(fixture);
  });

  it('opens on the household step', () => {
    expect(element(fixture).querySelector('h1')?.textContent).toContain('Name your household');
  });

  it('refuses to advance without a household name', async () => {
    await submit(fixture, 'form.form-stack');

    expect(element(fixture).querySelector('h1')?.textContent).toContain('Name your household');
    expect(element(fixture).textContent).toContain('Give the household a name');
  });

  it('walks household to people to done, seeding the creator as a member', async () => {
    type(fixture, '#householdName', 'The Germanos');
    await submit(fixture, 'form.form-stack');

    expect(element(fixture).querySelector('h1')?.textContent).toContain('Who eats here?');
    expect(memberNames(fixture)).toEqual(['Devon']);

    type(fixture, '#memberName', 'Alex');
    await submit(fixture, 'form.member-form');

    expect(memberNames(fixture)).toEqual(['Devon', 'Alex']);

    await clickByText(fixture, 'Done adding people');

    expect(element(fixture).querySelector('h1')?.textContent).toContain('The Germanos is set up');
    expect(element(fixture).textContent).toContain('2 people on the plan');
  });

  it('derives a plan identity, and adds people as placeholders with no account', async () => {
    type(fixture, '#householdName', 'The Germanos');
    await submit(fixture, 'form.form-stack');
    type(fixture, '#memberName', 'Alex Smith');
    await submit(fixture, 'form.member-form');

    const [devon, alex] = TestBed.inject(HouseholdStore).members();

    expect(devon.personName).toBe('devon');
    expect(devon.userId).toBe('user-1');

    // Someone the plan cooks for who has not signed up — the ratified model.
    expect(alex.personName).toBe('alex_smith');
    expect(alex.userId).toBeNull();
  });

  it('removes an added member but never the account itself', async () => {
    type(fixture, '#householdName', 'The Germanos');
    await submit(fixture, 'form.form-stack');
    type(fixture, '#memberName', 'Alex');
    await submit(fixture, 'form.member-form');

    const removeButtons = element(fixture).querySelectorAll('.members .remove');
    expect(removeButtons.length).toBe(1);

    await clickByText(fixture, 'Remove');

    expect(memberNames(fixture)).toEqual(['Devon']);
  });
});
