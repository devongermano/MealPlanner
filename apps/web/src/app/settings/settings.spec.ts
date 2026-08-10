import { TestBed, type ComponentFixture } from '@angular/core/testing';
import { FakeAuthBackend, fakeSession } from '../../testing/fake-auth-backend';
import { FakeHouseholdApi, fakeHousehold, fakeMember } from '../../testing/fake-household-api';
import { AUTH_BACKEND } from '../auth/auth-backend';
import { HOUSEHOLD_API } from '../household/household-api';
import { HouseholdStore } from '../household/household-store';
import { Settings } from './settings';

const HOME = fakeHousehold('hh-1', 'The Germanos');
const DEVON = fakeMember('mem-1', 'hh-1', 'Devon', 'planner', true);
const ALEX = fakeMember('mem-2', 'hh-1', 'Alex', 'eater');

async function mount(): Promise<ComponentFixture<Settings>> {
  TestBed.configureTestingModule({
    imports: [Settings],
    providers: [
      { provide: AUTH_BACKEND, useValue: new FakeAuthBackend(fakeSession()) },
      { provide: HOUSEHOLD_API, useValue: new FakeHouseholdApi([HOME], [DEVON, ALEX]) },
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

    expect(roleSelects(fixture).map((select) => select.value)).toEqual(['planner', 'eater']);
  });

  it('changing a role writes it through to the store', async () => {
    const fixture = await mount();
    const store = TestBed.inject(HouseholdStore);

    const alexSelect = roleSelects(fixture)[1];
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

  it('never offers to remove the signed-in account from its own household', async () => {
    const fixture = await mount();

    const removeButtons = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('.remove'),
    );

    expect(removeButtons[0].disabled).toBe(true);
    expect(removeButtons[1].disabled).toBe(false);
  });
});
