import { toApiError } from '../app/errors/api-error';
import type {
  AddHouseholdMemberRequest,
  CreateHouseholdRequest,
  HouseholdApi,
  HouseholdMemberView,
  HouseholdRole,
  HouseholdSummary,
  MeResponse,
  UpdateHouseholdMemberRequest,
  UpdateOwnMembershipRequest,
} from '../app/household/household-api';

export const FAKE_USER_ID = 'user-1';

export function fakeHousehold(
  id: string,
  name: string,
  overrides: Partial<HouseholdSummary> = {},
): HouseholdSummary {
  return {
    id,
    name,
    role: 'planner',
    displayName: 'Devon',
    personName: 'devon',
    memberCount: 1,
    createdAt: '2026-08-09T00:00:00.000Z',
    ...overrides,
  };
}

/** Defaults to a placeholder member; pass a userId to make it a claimed one. */
export function fakeMember(
  id: string,
  displayName: string,
  role: HouseholdRole = 'eater',
  userId: string | null = null,
): HouseholdMemberView {
  return {
    id,
    displayName,
    role,
    userId,
    personName: displayName.toLowerCase(),
    inviteEmail: null,
    createdAt: '2026-08-09T00:00:00.000Z',
  };
}

/**
 * Test double for HOUSEHOLD_API. Enforces the same authorization rules as the real
 * service, because a double that is more permissive than the API teaches the UI a
 * habit that breaks in production.
 */
export class FakeHouseholdApi implements HouseholdApi {
  constructor(
    private households: HouseholdSummary[] = [],
    private members: HouseholdMemberView[] = [],
    private readonly userId = FAKE_USER_ID,
  ) {}

  async me(): Promise<MeResponse> {
    return {
      userId: this.userId,
      email: 'devon@example.com',
      isAnonymous: false,
      households: this.households,
    };
  }

  async createHousehold(input: CreateHouseholdRequest): Promise<HouseholdSummary> {
    const household = fakeHousehold(`hh-${this.households.length + 1}`, input.name, {
      displayName: input.displayName,
      personName: input.personName ?? null,
    });
    this.households = [...this.households, household];
    this.members = [
      ...this.members,
      {
        ...fakeMember(`mem-${this.members.length + 1}`, input.displayName, 'planner', this.userId),
        personName: input.personName ?? null,
      },
    ];
    return household;
  }

  // Implemented without the householdId the interface passes: this double holds
  // one household's members, so there is nothing to filter by.
  async listMembers(): Promise<readonly HouseholdMemberView[]> {
    return this.members;
  }

  async addMember(
    _householdId: string,
    input: AddHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    const member: HouseholdMemberView = {
      ...fakeMember(
        `mem-${this.members.length + 1}`,
        input.displayName,
        input.role,
        input.userId ?? null,
      ),
      personName: input.personName ?? null,
      inviteEmail: input.inviteEmail ?? null,
    };
    this.members = [...this.members, member];
    return member;
  }

  async updateMember(
    _householdId: string,
    memberId: string,
    patch: UpdateHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    const target = this.requireMember(memberId);
    // Mirrors assertPlannerMayEdit: displayName and inviteEmail belong to the
    // account. personName does not — the library link is planning data. The whole
    // patch is refused rather than partially applied.
    if (target.userId !== null) {
      const owned = (['displayName', 'inviteEmail'] as const).filter(
        (field) => patch[field] !== undefined,
      );
      if (owned.length > 0) {
        throw toApiError(
          'forbidden',
          `${owned.join(' and ')} belong to that member's own account.`,
        );
      }
    }
    return this.apply(target, patch);
  }

  async updateSelf(
    _householdId: string,
    patch: UpdateOwnMembershipRequest,
  ): Promise<HouseholdMemberView> {
    const target = this.members.find((member) => member.userId === this.userId);
    if (!target) {
      throw toApiError('not_found', 'You are not a member of that household.');
    }
    return this.apply(target, patch);
  }

  async removeMember(_householdId: string, memberId: string): Promise<void> {
    this.requireMember(memberId);
    this.members = this.members.filter((member) => member.id !== memberId);
  }

  /** personName is unique per household, so both routes can answer 409. */
  private assertPersonNameFree(personName: string | null | undefined, memberId: string): void {
    if (!personName) {
      return;
    }
    const taken = this.members.some(
      (member) => member.id !== memberId && member.personName === personName,
    );
    if (taken) {
      throw toApiError('conflict', 'That person is already linked to another member.');
    }
  }

  private apply(
    target: HouseholdMemberView,
    patch: UpdateHouseholdMemberRequest,
  ): HouseholdMemberView {
    this.assertPersonNameFree(patch.personName, target.id);
    const updated: HouseholdMemberView = {
      ...target,
      role: patch.role ?? target.role,
      displayName: patch.displayName?.trim() || target.displayName,
      personName: patch.personName === undefined ? target.personName : patch.personName,
      inviteEmail: patch.inviteEmail === undefined ? target.inviteEmail : patch.inviteEmail,
    };
    this.members = this.members.map((member) => (member.id === target.id ? updated : member));
    return updated;
  }

  private requireMember(memberId: string): HouseholdMemberView {
    const member = this.members.find((candidate) => candidate.id === memberId);
    if (!member) {
      throw toApiError('not_found', `No member ${memberId}.`);
    }
    return member;
  }
}
