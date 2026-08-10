import { Injectable, computed, inject, signal } from '@angular/core';
import { describeApiError } from '../errors/api-error';
import {
  HOUSEHOLD_API,
  type AddHouseholdMemberRequest,
  type CreateHouseholdRequest,
  type HouseholdMemberView,
  type HouseholdSummary,
  type UpdateHouseholdMemberRequest,
  type UpdateOwnMembershipRequest,
} from './household-api';

const ACTIVE_KEY = 'mealplan.activeHouseholdId';

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/** A member plus whether it is you — derived, because the API has no such field. */
export interface HouseholdMemberRow extends HouseholdMemberView {
  readonly isSelf: boolean;
}

/**
 * Single source of household state for the shell, the wizard and settings.
 * Talks only to HOUSEHOLD_API.
 */
@Injectable({ providedIn: 'root' })
export class HouseholdStore {
  private readonly api = inject(HOUSEHOLD_API);

  private readonly householdList = signal<readonly HouseholdSummary[]>([]);
  private readonly memberList = signal<readonly HouseholdMemberView[]>([]);
  private readonly activeId = signal<string | null>(readActiveId());
  private readonly loadStatus = signal<LoadStatus>('idle');
  private readonly loadError = signal<string | null>(null);
  private readonly currentUserId = signal<string | null>(null);

  readonly households = this.householdList.asReadonly();
  readonly status = this.loadStatus.asReadonly();
  readonly error = this.loadError.asReadonly();
  readonly userId = this.currentUserId.asReadonly();

  /**
   * A placeholder member has no userId at all, so it can never be you — the
   * null check matters as much as the comparison.
   */
  readonly members = computed<readonly HouseholdMemberRow[]>(() => {
    const me = this.currentUserId();
    return this.memberList().map((member) => ({
      ...member,
      isSelf: member.userId !== null && member.userId === me,
    }));
  });

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

  async reload(): Promise<void> {
    this.loadStatus.set('idle');
    await this.load();
  }

  async createHousehold(input: CreateHouseholdRequest): Promise<HouseholdSummary> {
    const household = await this.api.createHousehold(input);
    this.householdList.update((list) => [...list, household]);
    this.setActive(household.id);
    this.memberList.set(await this.api.listMembers(household.id));
    return household;
  }

  async addMember(input: AddHouseholdMemberRequest): Promise<void> {
    const household = this.requireActive();
    const member = await this.api.addMember(household.id, input);
    this.memberList.update((list) => [...list, member]);
  }

  /** Planner route: role and personName on anyone, profile fields on placeholders only. */
  async updateMember(memberId: string, patch: UpdateHouseholdMemberRequest): Promise<void> {
    const household = this.requireActive();
    this.replace(memberId, await this.api.updateMember(household.id, memberId, patch));
  }

  /** Self route: your own profile, never your role. */
  async updateSelf(memberId: string, patch: UpdateOwnMembershipRequest): Promise<void> {
    const household = this.requireActive();
    this.replace(memberId, await this.api.updateSelf(household.id, patch));
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
    this.currentUserId.set(null);
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
      // One call for identity and every household — GET /me exists for this.
      const me = await this.api.me();
      this.currentUserId.set(me.userId);
      this.householdList.set(me.households);
      const active = this.active();
      this.memberList.set(active ? await this.api.listMembers(active.id) : []);
      this.loadStatus.set('ready');
    } catch (cause) {
      this.loadError.set(describeApiError(cause, 'Could not load your household.'));
      this.loadStatus.set('error');
    }
  }

  private replace(memberId: string, updated: HouseholdMemberView): void {
    this.memberList.update((list) =>
      list.map((member) => (member.id === memberId ? updated : member)),
    );
  }

  private requireActive(): HouseholdSummary {
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
