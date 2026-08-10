import { Injectable, computed, inject, signal } from '@angular/core';
import {
  HOUSEHOLD_API,
  type AddMemberInput,
  type CreateHouseholdInput,
  type Household,
  type HouseholdMember,
  type HouseholdRole,
  type UpdateMemberInput,
} from './household-api';

const ACTIVE_KEY = 'mealplan.activeHouseholdId';

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/**
 * Single source of household state for the shell, the wizard and settings.
 * Talks only to HOUSEHOLD_API, so it survives the mock being replaced by the
 * real endpoints untouched.
 */
@Injectable({ providedIn: 'root' })
export class HouseholdStore {
  private readonly api = inject(HOUSEHOLD_API);

  private readonly householdList = signal<readonly Household[]>([]);
  private readonly memberList = signal<readonly HouseholdMember[]>([]);
  private readonly activeId = signal<string | null>(readActiveId());
  private readonly loadStatus = signal<LoadStatus>('idle');
  private readonly loadError = signal<string | null>(null);

  readonly households = this.householdList.asReadonly();
  readonly members = this.memberList.asReadonly();
  readonly status = this.loadStatus.asReadonly();
  readonly error = this.loadError.asReadonly();

  readonly active = computed(() => {
    const households = this.householdList();
    const id = this.activeId();
    return households.find((household) => household.id === id) ?? households[0] ?? null;
  });

  readonly hasHousehold = computed(() => this.householdList().length > 0);

  private inFlight: Promise<void> | null = null;

  /** Idempotent: concurrent callers (guard + shell) share one round trip. */
  load(): Promise<void> {
    this.inFlight ??= this.runLoad().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  /** Forces a refetch — used after a mutation that the API is authoritative for. */
  async reload(): Promise<void> {
    this.loadStatus.set('idle');
    await this.load();
  }

  async createHousehold(input: CreateHouseholdInput): Promise<Household> {
    const household = await this.api.create(input);
    this.householdList.update((list) => [...list, household]);
    this.setActive(household.id);
    this.memberList.set(await this.api.listMembers(household.id));
    return household;
  }

  async addMember(input: AddMemberInput): Promise<void> {
    const household = this.requireActive();
    const member = await this.api.addMember(household.id, input);
    this.memberList.update((list) => [...list, member]);
  }

  updateMemberRole(memberId: string, role: HouseholdRole): Promise<void> {
    return this.patchMember(memberId, { role });
  }

  /** Null unlinks: the member keeps their role and stops being an eater in the plan. */
  updateMemberPersonName(memberId: string, personName: string | null): Promise<void> {
    return this.patchMember(memberId, { personName });
  }

  private async patchMember(memberId: string, patch: UpdateMemberInput): Promise<void> {
    const household = this.requireActive();
    const updated = await this.api.updateMember(household.id, memberId, patch);
    this.memberList.update((list) =>
      list.map((member) => (member.id === memberId ? updated : member)),
    );
  }

  async removeMember(memberId: string): Promise<void> {
    const household = this.requireActive();
    await this.api.removeMember(household.id, memberId);
    this.memberList.update((list) => list.filter((member) => member.id !== memberId));
  }

  setActive(householdId: string): void {
    this.activeId.set(householdId);
    try {
      localStorage.setItem(ACTIVE_KEY, householdId);
    } catch {
      // A browser refusing storage is not a reason to fail the switch.
    }
  }

  /** Drops cached state on sign-out so the next account never sees the last one's household. */
  clear(): void {
    this.householdList.set([]);
    this.memberList.set([]);
    this.activeId.set(null);
    this.loadStatus.set('idle');
    this.loadError.set(null);
  }

  private async runLoad(): Promise<void> {
    if (this.loadStatus() === 'ready') {
      return;
    }
    this.loadStatus.set('loading');
    this.loadError.set(null);
    try {
      const households = await this.api.listMine();
      this.householdList.set(households);
      const active = this.active();
      this.memberList.set(active ? await this.api.listMembers(active.id) : []);
      this.loadStatus.set('ready');
    } catch (cause) {
      this.loadError.set(cause instanceof Error ? cause.message : 'Could not load your household.');
      this.loadStatus.set('error');
    }
  }

  private requireActive(): Household {
    const household = this.active();
    if (!household) {
      throw new Error('No active household');
    }
    return household;
  }
}

function readActiveId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}
