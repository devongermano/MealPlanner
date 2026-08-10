import type {
  AddMemberInput,
  CreateHouseholdInput,
  Household,
  HouseholdApi,
  HouseholdMember,
  HouseholdRole,
  UpdateMemberInput,
  UpdateSelfInput,
} from '../app/household/household-api';

export function fakeHousehold(id: string, name: string): Household {
  return { id, name, createdAt: '2026-08-09T00:00:00.000Z' };
}

export function fakeMember(
  id: string,
  householdId: string,
  displayName: string,
  role: HouseholdRole = 'eater',
  isSelf = false,
): HouseholdMember {
  return {
    id,
    householdId,
    displayName,
    role,
    userId: isSelf ? 'user-1' : null,
    personName: displayName.toLowerCase(),
    email: null,
    isSelf,
  };
}

/** Test double for HOUSEHOLD_API seeded with fixed data — no storage, no Auth dependency. */
export class FakeHouseholdApi implements HouseholdApi {
  constructor(
    private households: Household[] = [],
    private members: HouseholdMember[] = [],
  ) {}

  async listMine(): Promise<readonly Household[]> {
    return this.households;
  }

  async create(input: CreateHouseholdInput): Promise<Household> {
    const household = fakeHousehold(`hh-${this.households.length + 1}`, input.name);
    this.households = [...this.households, household];
    return household;
  }

  async listMembers(householdId: string): Promise<readonly HouseholdMember[]> {
    return this.members.filter((member) => member.householdId === householdId);
  }

  async addMember(householdId: string, input: AddMemberInput): Promise<HouseholdMember> {
    const member = fakeMember(
      `mem-${this.members.length + 1}`,
      householdId,
      input.displayName,
      input.role,
    );
    this.members = [...this.members, member];
    return member;
  }

  async updateMember(
    householdId: string,
    memberId: string,
    patch: UpdateMemberInput,
  ): Promise<HouseholdMember> {
    const target = this.requireMember(householdId, memberId);
    // Mirrors the API's 403: a claimed member's profile is theirs, so role only.
    if (
      target.userId !== null &&
      (patch.displayName !== undefined || patch.personName !== undefined)
    ) {
      throw new Error('Role only: that member has an account.');
    }
    return this.apply(target, patch);
  }

  async updateSelf(householdId: string, patch: UpdateSelfInput): Promise<HouseholdMember> {
    const target = this.members.find(
      (member) => member.householdId === householdId && member.isSelf,
    );
    if (!target) {
      throw new Error(`Not a member of household ${householdId}`);
    }
    return this.apply(target, patch);
  }

  private apply(target: HouseholdMember, patch: UpdateMemberInput): HouseholdMember {
    const updated: HouseholdMember = {
      ...target,
      role: patch.role ?? target.role,
      displayName: patch.displayName?.trim() || target.displayName,
      personName: patch.personName === undefined ? target.personName : patch.personName,
    };
    this.members = this.members.map((member) => (member.id === target.id ? updated : member));
    return updated;
  }

  async removeMember(householdId: string, memberId: string): Promise<void> {
    this.requireMember(householdId, memberId);
    this.members = this.members.filter((member) => member.id !== memberId);
  }

  private requireMember(householdId: string, memberId: string): HouseholdMember {
    const member = this.members.find(
      (candidate) => candidate.id === memberId && candidate.householdId === householdId,
    );
    if (!member) {
      throw new Error(`No member ${memberId} in household ${householdId}`);
    }
    return member;
  }
}
