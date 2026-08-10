import { Injectable, inject } from '@angular/core';
import { Auth } from '../auth/auth';
import {
  type AddMemberInput,
  type CreateHouseholdInput,
  type Household,
  type HouseholdApi,
  type HouseholdMember,
  type HouseholdRole,
} from './household-api';

/*
 * REGENERATE-FROM-CONTRACTS-API — throwaway by design.
 *
 * Stands in for the NestJS household endpoints so the shell and the onboarding
 * wizard can be built and tested before those endpoints exist. Deleting this file
 * and providing an HTTP implementation of HouseholdApi is the whole swap; no UI
 * code changes. State is scoped per account and kept in sessionStorage so a page
 * reload during review does not erase the household you just created.
 */
@Injectable()
export class HouseholdApiMock implements HouseholdApi {
  private readonly auth = inject(Auth);

  async listMine(): Promise<readonly Household[]> {
    return this.read().households;
  }

  async create(input: CreateHouseholdInput): Promise<Household> {
    const state = this.read();
    const household: Household = {
      id: `hh-${crypto.randomUUID()}`,
      name: input.name.trim(),
      createdAt: new Date().toISOString(),
    };
    const self: HouseholdMember = {
      id: `mem-${crypto.randomUUID()}`,
      householdId: household.id,
      displayName: input.creatorDisplayName.trim(),
      role: input.creatorRole,
      email: this.auth.user()?.email ?? null,
      isSelf: true,
    };
    this.write({
      households: [...state.households, household],
      members: [...state.members, self],
    });
    return household;
  }

  async listMembers(householdId: string): Promise<readonly HouseholdMember[]> {
    return this.read().members.filter((member) => member.householdId === householdId);
  }

  async addMember(householdId: string, input: AddMemberInput): Promise<HouseholdMember> {
    const state = this.read();
    const member: HouseholdMember = {
      id: `mem-${crypto.randomUUID()}`,
      householdId,
      displayName: input.displayName.trim(),
      role: input.role,
      email: input.email?.trim() || null,
      isSelf: false,
    };
    this.write({ ...state, members: [...state.members, member] });
    return member;
  }

  async updateMemberRole(
    householdId: string,
    memberId: string,
    role: HouseholdRole,
  ): Promise<HouseholdMember> {
    const state = this.read();
    const target = state.members.find(
      (member) => member.id === memberId && member.householdId === householdId,
    );
    if (!target) {
      throw new Error(`No member ${memberId} in household ${householdId}`);
    }
    const updated: HouseholdMember = { ...target, role };
    this.write({
      ...state,
      members: state.members.map((member) => (member.id === memberId ? updated : member)),
    });
    return updated;
  }

  async removeMember(householdId: string, memberId: string): Promise<void> {
    const state = this.read();
    this.write({
      ...state,
      members: state.members.filter(
        (member) => !(member.id === memberId && member.householdId === householdId),
      ),
    });
  }

  private storageKey(): string {
    return `mealplan.mock.households.${this.auth.user()?.id ?? 'anonymous'}`;
  }

  private read(): MockState {
    try {
      const raw = sessionStorage.getItem(this.storageKey());
      return raw ? (JSON.parse(raw) as MockState) : EMPTY_STATE;
    } catch {
      return EMPTY_STATE;
    }
  }

  private write(state: MockState): void {
    sessionStorage.setItem(this.storageKey(), JSON.stringify(state));
  }
}

interface MockState {
  readonly households: readonly Household[];
  readonly members: readonly HouseholdMember[];
}

const EMPTY_STATE: MockState = { households: [], members: [] };
