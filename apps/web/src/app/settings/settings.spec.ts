import { TestBed, type ComponentFixture } from '@angular/core/testing';
import { FakeAuthBackend, fakeSession } from '../../testing/fake-auth-backend';
import { FakeHouseholdApi, fakeHousehold, fakeMember } from '../../testing/fake-household-api';
import { AUTH_BACKEND } from '../auth/auth-backend';
import { HOUSEHOLD_API, type HouseholdMember } from '../household/household-api';
import { HouseholdStore } from '../household/household-store';
import { Settings } from './settings';

const HOME = fakeHousehold('hh-1', 'The Germanos');
/** Row 0: you. Row 1: a placeholder with no account. Row 2: someone who signed up. */
const DEVON = fakeMember('mem-1', 'hh-1', 'Devon', 'planner', true);
const ALEX = fakeMember('mem-2', 'hh-1', 'Alex', 'eater');
const SAM: HouseholdMember = { ...fakeMember('mem-3', 'hh-1', 'Sam', 'cook'), userId: 'user-9' };

async function mount(): Promise<ComponentFixture<Settings>> {
  TestBed.configureTestingModule({
    imports: [Settings],
    providers: [
      { provide: AUTH_BACKEND, useValue: new FakeAuthBackend(fakeSession()) },
      { provide: HOUSEHOLD_API, useValue: new FakeHouseholdApi([HOME], [DEVON, ALEX, SAM]) },
    ],
  });

  await TestBed.inject(HouseholdStore).load();
  const fixture = TestBed.createComponent(Settings);
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture;
}

function roleSelects(fixture: ComponentFixture<Settings>): HTMLSelectElement[] {
  return Array.from(
    (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLSelectElement>('.role-select'),
  );
}

function rows(fixture: ComponentFixture<Settings>): HTMLLIElement[] {
  return Array.from(
    (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLLIElement>('.members li'),
  );
}

function personNameInputs(fixture: ComponentFixture<Settings>): HTMLInputElement[] {
  return Array.from(
    (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLInputElement>('.person-name'),
  );
}

async function editPersonName(
  fixture: ComponentFixture<Settings>,
  index: number,
  value: string,
): Promise<void> {
  const input = personNameInputs(fixture)[index];
  input.value = value;
  input.dispatchEvent(new Event('change'));
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('Settings', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("shows each member's own role rather than the first option", async () => {
    const fixture = await mount();

    // Your own row has no role control, so the selects belong to Alex and Sam.
    expect(roleSelects(fixture).map((select) => select.value)).toEqual(['eater', 'cook']);
  });

  it('changing a role writes it through to the store', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    const alexSelect = roleSelects(fixture)[0];
    alexSelect.value = 'cook';
    alexSelect.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(store.members().find((member) => member.id === 'mem-2')?.role).toBe('cook');
  });

  it("shows each member's plan identity", async () => {
    const fixture = await mount();

    expect(personNameInputs(fixture).map((input) => input.value)).toEqual(['devon', 'alex']);
  });

  it('saves an edited plan identity', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    await editPersonName(fixture, 1, 'alexandra');

    expect(store.members().find((member) => member.id === 'mem-2')?.personName).toBe('alexandra');
  });

  it('clearing the plan identity unlinks the member rather than erroring', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    await editPersonName(fixture, 1, '   ');

    expect(store.members().find((member) => member.id === 'mem-2')?.personName).toBeNull();
    expect((fixture.nativeElement as HTMLElement).querySelector('.row-error')).toBeNull();
  });

  it('refuses a plan identity the engine could not key on, without saving it', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    await editPersonName(fixture, 1, 'Alex Smith');

    expect(store.members().find((member) => member.id === 'mem-2')?.personName).toBe('alex');
    expect((fixture.nativeElement as HTMLElement).querySelector('.row-error')).not.toBeNull();
  });

  it('gives your own row an editable profile but no role control', async () => {
    const fixture = await mount();
    const mine = rows(fixture)[0];

    expect(mine.querySelector('.display-name')).not.toBeNull();
    expect(mine.querySelector('.person-name')).not.toBeNull();
    // The self route has no role field; offering one would promise a 400.
    expect(mine.querySelector('.role-select')).toBeNull();
  });

  it('gives a placeholder member every control, since a planner owns that row', async () => {
    const fixture = await mount();
    const placeholder = rows(fixture)[1];

    expect(placeholder.querySelector('.display-name')).not.toBeNull();
    expect(placeholder.querySelector('.person-name')).not.toBeNull();
    expect(placeholder.querySelector('.role-select')).not.toBeNull();
    expect(placeholder.textContent).toContain('no account yet');
  });

  it("offers only a role control for a member who has an account", async () => {
    const fixture = await mount();
    const claimed = rows(fixture)[2];

    // Their profile is theirs — the API answers 403, so we never offer the field.
    expect(claimed.querySelector('.display-name')).toBeNull();
    expect(claimed.querySelector('.person-name')).toBeNull();
    expect(claimed.querySelector('.role-select')).not.toBeNull();
  });

  it('saves your own name through the self route', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    const input = rows(fixture)[0].querySelector<HTMLInputElement>('.display-name')!;
    input.value = 'Devon G';
    input.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(store.members().find((member) => member.id === 'mem-1')?.displayName).toBe('Devon G');
  });

  it('refuses to blank a name, since everyone on the plan needs one', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    const input = rows(fixture)[0].querySelector<HTMLInputElement>('.display-name')!;
    input.value = '  ';
    input.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(store.members().find((member) => member.id === 'mem-1')?.displayName).toBe('Devon');
    expect(rows(fixture)[0].querySelector('.row-error')).not.toBeNull();
  });

  it('never offers to remove the signed-in account from its own household', async () => {
    const fixture = await mount();

    const removeButtons = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('.remove'),
    );

    expect(removeButtons[0].disabled).toBe(true);
    expect(removeButtons[1].disabled).toBe(false);
  });
});
