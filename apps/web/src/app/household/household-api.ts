import { InjectionToken } from '@angular/core';

/*
 * ============================================================================
 * REGENERATE-FROM-CONTRACTS-API
 * ----------------------------------------------------------------------------
 * Every type in this file is a PLACEHOLDER. The api-steward owns the real
 * household shapes; when they land in packages/contracts these definitions get
 * deleted and re-exported from the generated types instead — per ARCHITECTURE.md,
 * "nobody hand-writes the TypeScript mirror" of a cross-boundary type.
 *
 * The interface below is the seam that makes that swap mechanical: the UI depends
 * only on HOUSEHOLD_API, never on the implementation behind it.
 * ============================================================================
 */

/** PRD §4.2. planner configures and approves, cook preps, eater sees sheets and vetoes. */
export type HouseholdRole = 'planner' | 'cook' | 'eater';

export const HOUSEHOLD_ROLES: readonly HouseholdRole[] = ['planner', 'cook', 'eater'];

export const ROLE_DESCRIPTIONS: Readonly<Record<HouseholdRole, string>> = {
  planner: 'Sets up people and targets, approves the week, does the shopping.',
  cook: 'Gets the prep plan for cook days. Often the same person as the planner.',
  eater: 'Sees their own sheets, vetoes dishes, and nudges portions.',
};

export interface Household {
  readonly id: string;
  readonly name: string;
  readonly createdAt: string;
}

export interface HouseholdMember {
  readonly id: string;
  readonly householdId: string;
  readonly displayName: string;
  readonly role: HouseholdRole;
  /** Null until the member is invited — a household can be planned before everyone signs up. */
  readonly email: string | null;
  /** True for the member row belonging to the signed-in account. */
  readonly isSelf: boolean;
}

export interface CreateHouseholdInput {
  readonly name: string;
  /** Role the creating account takes in their own household. */
  readonly creatorRole: HouseholdRole;
  readonly creatorDisplayName: string;
}

export interface AddMemberInput {
  readonly displayName: string;
  readonly role: HouseholdRole;
  readonly email?: string | null;
}

export interface HouseholdApi {
  /** Households the signed-in account belongs to. Empty means onboarding is owed. */
  listMine(): Promise<readonly Household[]>;
  create(input: CreateHouseholdInput): Promise<Household>;
  listMembers(householdId: string): Promise<readonly HouseholdMember[]>;
  addMember(householdId: string, input: AddMemberInput): Promise<HouseholdMember>;
  updateMemberRole(
    householdId: string,
    memberId: string,
    role: HouseholdRole,
  ): Promise<HouseholdMember>;
  removeMember(householdId: string, memberId: string): Promise<void>;
}

export const HOUSEHOLD_API = new InjectionToken<HouseholdApi>('HOUSEHOLD_API');
